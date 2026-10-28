"""Phase D tests: the HTTP API. Fast tests use FakeEngine; `slow` ones drive real GPT-2."""

import csv
import io
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from grammarguard.api.main import create_app
from grammarguard.api.settings import Settings
from grammarguard.core.dfa import regex_to_dfa
from tests.api_fakes import FakeEngine

API = "/api/v1"


def make_client(engine=None, **overrides):
    """A TestClient around a fresh app with test settings (no model warm-up, no frontend)."""
    settings = Settings(warm_start=False, frontend_dir="/nonexistent", **overrides)
    engine = engine or FakeEngine()
    return TestClient(create_app(settings, engine)), engine


@pytest.fixture()
def client():
    c, _ = make_client()
    with c:
        yield c


def post(client, path, **body):
    return client.post(API + path, json=body)


def sse(response) -> list[tuple[str, dict]]:
    """Parse a finished SSE response into [(event, data)]."""
    events, name = [], None
    for line in response.iter_lines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:") and name:
            events.append((name, json.loads(line.split(":", 1)[1])))
            name = None
    return events


def stream(client, path, **body):
    with client.stream("POST", API + path, json=body) as r:
        assert r.status_code == 200, r.read()
        return sse(r)


def error_of(response, status=422):
    assert response.status_code == status, response.text
    return response.json()["error"]


# ---------------------------------------------------------------- meta


def test_openapi_lists_every_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "compile",
        "membership",
        "certificate/verify",
        "builtins",
        "algebra",
        "equivalence",
        "count",
        "validators/diff",
        "validators/testgen",
        "learn/subset",
        "learn/minimize",
        "generate",
        "generate/json",
        "generate/batch",
        "system/health",
        "system/models",
    }
    assert {p.removeprefix(API + "/") for p in paths} == expected
    assert client.get("/").json()["docs"] == "/docs"


def test_health_models_headers_and_cors(client):
    h = client.get(API + "/system/health")
    assert h.status_code == 200 and h.json()["status"] == "ok" and h.json()["engine"]["loaded"] is True
    assert len(h.headers["x-request-id"]) >= 8
    assert client.get(API + "/system/health", headers={"x-request-id": "abc"}).headers["x-request-id"] == "abc"
    m = client.get(API + "/system/models").json()
    assert m["model"] == "gpt2" and m["limits"]["max_tokens"] == 200
    cors = client.get(API + "/system/health", headers={"Origin": "http://localhost:5173"})
    assert cors.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert (
        "access-control-allow-origin"
        not in client.get(API + "/system/health", headers={"Origin": "http://evil.example"}).headers
    )


def test_validation_errors_use_the_envelope(client):
    e = error_of(post(client, "/compile"))
    assert e["code"] == "validation" and "regex" in e["message"] and e["details"][0]["field"] == "regex"
    e = error_of(post(client, "/count", constraint={"text": "a"}, max_length=-1))
    assert e["code"] == "validation"


# ---------------------------------------------------------------- compile / membership / certificates


def test_compile_pipeline(client):
    r = post(client, "/compile", regex="(a|b)*abb")
    assert r.status_code == 200
    d = r.json()
    assert (d["nfa"]["states"], d["dfa"]["states"], d["min_dfa"]["states"]) == (14, 6, 5)
    assert d["alphabet"] == ["a", "b"] and "digraph" in d["min_dfa"]["dot"] and "ε" in d["nfa"]["dot"]
    assert d["min_dfa"]["accepting"] and d["min_dfa"]["transitions"] and d["dfa"]["state_labels"]["0"].startswith("{")
    assert d["ast"] and d["elapsed_ms"] >= 0
    assert post(client, "/compile", regex="(a|b)*abb").json()["min_dfa"] == d["min_dfa"]  # cached


def test_compile_errors_carry_positions(client):
    e = error_of(post(client, "/compile", regex="ab(c"))
    assert e["code"] == "regex_syntax" and e["position"] == 2
    e = error_of(post(client, "/compile", regex="a.b"))
    assert e["code"] == "regex_syntax" and e["position"] == 1


def test_compile_limits():
    c, _ = make_client(max_regex_len=20, max_dfa_states=500)
    with c:
        assert error_of(post(c, "/compile", regex="a" * 21))["code"] == "limit_exceeded"
        blowup = error_of(post(c, "/compile", regex="(a|b)*a(a|b){12}"))  # needs 2^13 DFA states
        assert blowup["code"] == "limit_exceeded" and blowup["details"]["limit"] == 500
    c, _ = make_client(max_dot_states=3)
    with c:
        assert post(c, "/compile", regex="(a|b)*abb").json()["min_dfa"]["dot"] is None  # too big to draw


def test_membership_path_and_certificate_roundtrip(client):
    body = dict(constraint={"kind": "regex", "text": "[0-9]{4}-[0-9]{2}"}, text="2024-05")
    r = post(client, "/membership", **body).json()
    assert r["accepted"] and r["viable_prefix"] and r["doomed_at"] is None and len(r["path"]) == 7
    assert r["path"][0]["source"] == r["start"] and r["path"][-1]["target"] == r["final_state"]
    v = post(client, "/certificate/verify", certificate=r["certificate"]).json()
    assert v == {
        "valid": True,
        "accepted": True,
        "reason": "all 7 transitions check out",
        "automaton_states": 7 + 1 + 1,
    }
    # tamper with the certificate
    forged = dict(r["certificate"], accepted=False)
    assert post(client, "/certificate/verify", certificate=forged).json()["valid"] is False
    forged = dict(r["certificate"], text="2024-0x")
    assert "step 7" in post(client, "/certificate/verify", certificate=forged).json()["reason"]


def test_membership_rejections(client):
    r = post(client, "/membership", constraint={"text": "[0-9]{4}-[0-9]{2}"}, text="20x4-05").json()
    assert not r["accepted"] and r["doomed_at"] == 2 and not r["viable_prefix"] and r["path"][2]["live"] is False
    r = post(client, "/membership", constraint={"text": "[0-9]{4}-[0-9]{2}"}, text="2024-").json()
    assert not r["accepted"] and r["viable_prefix"]
    r = post(
        client, "/membership", constraint={"kind": "expression", "text": "digits & !/0[0-9]*/"}, text="0123"
    ).json()
    assert not r["accepted"]
    assert (
        error_of(post(client, "/membership", constraint={"text": "a"}, text="a" * 10_001))["code"] == "limit_exceeded"
    )


def test_certificate_with_named_constraints_is_self_contained(client):
    c = {"kind": "expression", "text": "code & !/XX[A-Z0-9]*/", "names": {"code": "[A-Z]{2}[0-9]{2}"}}
    cert = post(client, "/membership", constraint=c, text="AB12").json()["certificate"]
    assert cert["names"] == {"code": "[A-Z]{2}[0-9]{2}"}
    assert post(client, "/certificate/verify", certificate=cert).json()["valid"]


def test_certificate_errors(client):
    assert error_of(post(client, "/certificate/verify", certificate={"kind": "dfa"}))["code"] == "validation"
    bad = dict(kind="nope", constraint="x", fingerprint="", text="", trace=[], accepted=False)
    assert error_of(post(client, "/certificate/verify", certificate=bad))["code"] == "validation"
    assert (
        error_of(post(client, "/certificate/verify", certificate=dict(bad, kind="dfa", constraint="/(/")))["code"]
        == "algebra"
    )


# ---------------------------------------------------------------- algebra / equivalence / count


def test_builtins(client):
    b = client.get(API + "/builtins").json()["builtins"]
    assert b["iso_date"].startswith("[0-9]{4}") and "email" in b and len(b) >= 15


def test_algebra_policy(client):
    expr = "email & !contains(/spam/) & length(8, 30)"
    d = post(client, "/algebra", expression=expr).json()
    assert (
        d["finite"] is True and d["size"].isdigit() and len(d["size"]) > 16
    )  # bounded by length(8, 30); too big for a JS number
    assert any("complement" in s["operation"] for s in d["steps"]) and any(
        "intersection" in s["operation"] for s in d["steps"]
    )
    assert d["dfa"]["states"] > 10 and d["examples"] and d["counts"][:8] == ["0"] * 8
    # the regex produced by state elimination is equivalent to the expression (checked through the API itself)
    if d["regex"]:
        eq = post(
            client, "/equivalence", a={"kind": "expression", "text": expr}, b={"kind": "regex", "text": d["regex"]}
        ).json()
        assert eq["equivalent"], eq


def test_algebra_finite_language_and_big_numbers(client):
    d = post(client, "/algebra", expression="/[ab]{2,3}/").json()
    assert d["finite"] and d["size"] == "12" and d["regex"] == "[ab]{2}[ab]?"
    c = post(client, "/count", constraint={"text": "[0-9]{30}"}, max_length=30).json()
    assert c["counts"][30] == "1" + "0" * 30 and isinstance(c["counts"][30], str)  # beyond 2^53, so a string


def test_algebra_wildcard_languages_have_no_regex(client):
    d = post(client, "/algebra", expression="!/a/").json()
    assert d["regex"] is None and "outside its alphabet" in d["regex_note"]


@pytest.mark.parametrize(
    "expr,code_msg,pos",
    [
        ("/a/ &", "unexpected end", 5),
        ("nope", "unknown name", 0),
        ("", "empty", 0),
        ("/(/", "bad regex", 0),
        ("length(5,2)", "needs", 0),
    ],
)
def test_algebra_errors(client, expr, code_msg, pos):
    e = error_of(post(client, "/algebra", expression=expr))
    assert e["code"] == "algebra" and code_msg in e["message"] and e["position"] >= 0


def test_algebra_state_and_length_limits():
    c, _ = make_client(max_states=50)
    with c:
        e = error_of(post(c, "/algebra", expression="email & !contains(/competitor/) & length(5, 40)"))
        assert e["code"] in ("algebra", "limit_exceeded") and "large" in e["message"] or "states" in e["message"]
    c, _ = make_client(max_expression_len=10)
    with c:
        assert error_of(post(c, "/algebra", expression="/a/ | /b/ | /c/ | /d/"))["code"] == "limit_exceeded"


def test_equivalence(client):
    r = post(client, "/equivalence", a={"text": "(a|b)*"}, b={"text": "(a*b*)*"}).json()
    assert r["equivalent"] and r["counterexample"] is None
    r = post(client, "/equivalence", a={"text": "(a|b)*abb"}, b={"text": "(a|b)*bb"}).json()
    assert (
        not r["equivalent"]
        and r["counterexample"] == "bb"
        and r["accepted_by_a"] is False
        and r["accepted_by_b"] is True
    )
    r = post(client, "/equivalence", a={"text": "a*"}, b={"text": "a+"}).json()
    assert r["counterexample"] == "" and r["accepted_by_a"] is True


def test_count_and_uniform_samples(client):
    d = regex_to_dfa("[ab]{3}")
    r = post(client, "/count", constraint={"text": "[ab]{3}"}, max_length=5, sample_length=3, samples=6, seed=1).json()
    assert r["counts"] == ["0", "0", "0", "8", "0", "0"] and r["finite"] and r["size"] == "8"
    assert len(r["samples"]) == 6 and all(d.accepts(s) for s in r["samples"]) and r["shortest"][0] == "aaa"
    r = post(client, "/count", constraint={"text": "a*"}).json()
    assert r["finite"] is False and r["size"] is None
    assert error_of(post(client, "/count", constraint={"text": "a"}, max_length=65))["code"] == "limit_exceeded"


# ---------------------------------------------------------------- validators


@pytest.mark.parametrize(
    "old,new,relation",
    [
        ("[0-9]{3}-[0-9]{2}", "[0-9]{3}-[0-9]{2}", "equal"),
        ("[0-9]{3}-[0-9]{2}", "[0-9]{3}[-_][0-9]{2}", "new_looser"),
        ("[0-9]{2,3}-[0-9]{2}", "[0-9]{3}-[0-9]{2}", "new_stricter"),
        ("a[ab]*", "[ab]*b", "overlap"),
        ("a+", "b+", "disjoint"),
    ],
)
def test_validator_diff_relations_and_witnesses(client, old, new, relation):
    r = post(client, "/validators/diff", old={"text": old}, new={"text": new}, examples=4).json()
    assert r["relation"] == relation and r["summary"]
    o, n = regex_to_dfa(old), regex_to_dfa(new)
    assert all(o.accepts(s) and not n.accepts(s) for s in r["only_old"])
    assert all(n.accepts(s) and not o.accepts(s) for s in r["only_new"])
    assert all(o.accepts(s) and n.accepts(s) for s in r["both"])
    assert bool(r["only_old"]) == (relation in ("new_stricter", "overlap", "disjoint"))
    assert bool(r["only_new"]) == (relation in ("new_looser", "overlap", "disjoint"))


def test_validator_diff_witnesses_are_shortest(client):
    r = post(client, "/validators/diff", old={"text": "[0-9]{3}-[0-9]{2}"}, new={"text": "[0-9]{3}[-_][0-9]{2}"}).json()
    assert r["only_new"][0] == "000_00" and all(len(s) == 6 for s in r["only_new"])


def test_testgen_json_csv_pytest(client):
    pattern = "EMP-[0-9]{3}"
    r = post(client, "/validators/testgen", constraint={"text": pattern}).json()
    assert r["coverage"]["transition_coverage"] == 1.0 and set(r["counts"]) >= {"transition", "boundary", "truncation"}
    assert r["csv"] is None and r["pytest"] is None and not r["truncated"]
    r = post(client, "/validators/testgen", constraint={"text": pattern}, format="csv").json()
    rows = list(csv.reader(io.StringIO(r["csv"])))
    assert rows[0] == ["text", "expected", "kind", "reason"] and len(rows) == 1 + len(r["cases"])
    r = post(client, "/validators/testgen", constraint={"text": pattern}, format="pytest").json()
    ns: dict = {}
    exec(compile(r["pytest"], "generated.py", "exec"), ns)
    assert ns["PATTERN"] == pattern and len(ns["CASES"]) == len(r["cases"])
    r = post(client, "/validators/testgen", constraint={"text": pattern}, max_cases=5).json()
    assert len(r["cases"]) == 5 and r["truncated"] is True and r["coverage"]["transition_coverage"] == 1.0


def test_testgen_expression_exports_equivalent_regex_or_explains(client):
    ok = post(
        client,
        "/validators/testgen",
        constraint={"kind": "expression", "text": "digits & length(3,3)"},
        format="pytest",
    ).json()
    assert ok["pattern"] and "re.fullmatch" in ok["pytest"]
    e = error_of(
        post(client, "/validators/testgen", constraint={"kind": "expression", "text": "!/a/"}, format="pytest")
    )
    assert e["code"] == "not_expressible"
    assert (
        error_of(post(client, "/validators/testgen", constraint={"text": "a"}, max_cases=100_000))["code"]
        == "limit_exceeded"
    )


# ---------------------------------------------------------------- learn


def test_learn_subset_trace(client):
    r = client.get(API + "/learn/subset", params={"regex": "(a|b)*abb"}).json()
    assert len(r["steps"]) == r["dfa"]["states"] * 3  # |Q| subsets x {a, b, other}
    assert r["steps"][0]["from"] and {s["symbol"] for s in r["steps"]} == {"a", "b", "other"}
    assert sum(s["new"] for s in r["steps"]) == r["dfa"]["states"] - 1 and r["nfa"]["states"] == 14


def test_learn_minimize_trace(client):
    r = client.get(API + "/learn/minimize", params={"regex": "(a|b)*abb"}).json()
    assert (
        r["rounds"][0]["round"] == 0 and r["rounds"][0]["marked"] and len(r["classes"]) == r["min_dfa"]["states"] == 5
    )
    assert r["dfa"]["states"] == 6
    later = [m for rd in r["rounds"][1:] for m in rd["marked"]]
    assert later and all("leads_to" in m for m in later)
    assert (
        error_of(client.get(API + "/learn/minimize", params={"regex": "(a|b)*a(a|b){6}"}), 422)["code"]
        == "limit_exceeded"
    )
    assert error_of(client.get(API + "/learn/subset", params={"regex": "(a"}))["code"] == "regex_syntax"


# ---------------------------------------------------------------- generate (SSE, fake engine)


def test_generate_stream_protocol(client):
    events = stream(client, "/generate", constraint={"text": "EMP-[0-9]{3}"}, prompt="ID: ", seed=0)
    names = [n for n, _ in events]
    assert names[0] == "meta" and names[-2:] == ["done", "end"] and names[1:-2] == ["token"] * 7
    meta, done = events[0][1], events[-2][1]
    assert meta["kind"] == "dfa" and meta["states"] == 8 + 1 and meta["constraint"] == "/EMP-[0-9]{3}/"
    tokens = [d for n, d in events if n == "token"]
    assert [t["index"] for t in tokens] == list(range(7)) and tokens[0]["telemetry"]["pressure"] == 0.5
    assert (
        done["text"] == "EMP-000"
        and done["accepted"]
        and done["stop_reason"] == "eos"
        and done["summary"]["steps"] == 7
    )
    assert post(client, "/certificate/verify", certificate=done["certificate"]).json()["valid"]


def test_generate_compare_streams_unconstrained_first(client):
    events = stream(client, "/generate", constraint={"text": "ab"}, compare=True, max_tokens=3)
    dones = [d for n, d in events if n == "done"]
    assert [d["source"] for d in dones] == ["unconstrained", "constrained"]
    assert dones[0]["accepted"] is False and dones[1]["accepted"] is True and "certificate" not in dones[0]
    assert [d["source"] for n, d in events if n == "token"] == ["unconstrained"] * 3 + ["constrained"] * 2


def test_generate_without_certificate(client):
    events = stream(client, "/generate", constraint={"text": "ab"}, certificate=False)
    assert "certificate" not in [d for n, d in events if n == "done"][0]


def test_generate_validation_happens_before_streaming(client):
    assert error_of(post(client, "/generate", constraint={"text": "(a"}, prompt="x"))["code"] == "regex_syntax"
    assert error_of(post(client, "/generate", constraint={"text": "a"}, max_tokens=500))["code"] == "limit_exceeded"
    assert error_of(post(client, "/generate", constraint={"text": "a"}, prompt="x" * 4001))["code"] == "limit_exceeded"
    assert error_of(post(client, "/generate", constraint={"text": "a"}, temperature=0))["code"] == "validation"


def test_generate_json_stream_with_stack(client):
    events = stream(client, "/generate/json", prompt="x")
    toks = [d for n, d in events if n == "token"]
    done = [d for n, d in events if n == "done"][0]
    assert events[0][1]["kind"] == "pda" and events[0][1]["constraint"] == "json"
    assert toks[0]["stack"] == ["Obj"] and toks[-1]["stack"] == [] and toks[3]["fallback"] is True
    assert done["text"] == '{"a":[1,2]}' and done["accepted"] and done["summary"]["fallbacks"] == 1
    assert post(client, "/certificate/verify", certificate=done["certificate"]).json()["accepted"]


def test_generate_json_with_schema():
    schema = {"type": "object", "properties": {"id": {"type": "integer"}}}
    c, _ = make_client(FakeEngine(json_text='{"id":42}'))
    with c:
        events = stream(c, "/generate/json", schema=schema, compare=True)
        done = [d for n, d in events if n == "done"]
        assert done[0]["source"] == "unconstrained" and done[1]["accepted"] and done[1]["text"] == '{"id":42}'
        cert = done[1]["certificate"]
        assert cert["constraint"].startswith("schema:")
        assert post(c, "/certificate/verify", certificate=cert).json()["accepted"]
        assert error_of(post(c, "/generate/json", schema={"type": "float"}))["code"] == "schema"


def test_generate_reports_queueing():
    c, engine = make_client()
    with c:
        engine._lock.acquire()  # somebody else is generating
        released = threading.Timer(0.3, engine._lock.release)
        released.start()
        events = stream(c, "/generate", constraint={"text": "ab"})
        names = [n for n, _ in events]
        assert names[:2] == ["meta", "status"] and events[1][1] == {"state": "queued"} and names[-2:] == ["done", "end"]


def test_generate_timeout_reports_timeout():
    c, _ = make_client(FakeEngine(delay=0.05), generation_timeout=0.3)
    with c:
        events = stream(c, "/generate", constraint={"text": "a{50}"})
        done = [d for n, d in events if n == "done"][0]
        assert done["stop_reason"] == "timeout" and 0 < done["tokens"] < 50 and done["accepted"] is False


def test_generate_server_error_becomes_error_event():
    c, engine = make_client(FakeEngine(explode=True))
    with c:
        events = stream(c, "/generate", constraint={"text": "ab"})
        names = [n for n, _ in events]
        assert (
            names[-2:] == ["error", "end"]
            and events[-2][1]["code"] == "internal"
            and "boom" in events[-2][1]["message"]
        )
        assert not engine.busy


def test_client_disconnect_cancels_generation_and_frees_the_engine():
    c, engine = make_client(FakeEngine(delay=0.05))
    with c:
        with c.stream("POST", API + "/generate", json={"constraint": {"text": "a{50}"}}) as r:
            for line in r.iter_lines():
                if line.startswith("event: token"):
                    break
        deadline = time.time() + 5
        while engine.busy and time.time() < deadline:
            time.sleep(0.05)
        assert not engine.busy and 0 < engine.tokens_emitted < 50  # it did start, and it stopped early


# ---------------------------------------------------------------- batch


def test_batch_distinct_valid_samples_with_certificates(client):
    r = post(client, "/generate/batch", constraint={"text": "[ab]{1,3}"}, count=4, certificates=True).json()
    texts = [s["text"] for s in r["samples"]]
    d = regex_to_dfa("[ab]{1,3}")
    assert len(texts) == len(set(texts)) == 4 and all(d.accepts(t) for t in texts) and r["note"] is None
    assert all(post(client, "/certificate/verify", certificate=s["certificate"]).json()["valid"] for s in r["samples"])


def test_batch_finite_language_returns_what_exists(client):
    r = post(client, "/generate/batch", constraint={"text": "abc"}, count=3).json()
    assert [s["text"] for s in r["samples"]] == ["abc"] and r["attempts"] == 9 and "fewer" in r["note"]


def test_batch_limits(client):
    assert error_of(post(client, "/generate/batch", constraint={"text": "a"}, count=51))["code"] == "limit_exceeded"
    assert error_of(post(client, "/generate/batch", constraint={"text": "a"}, top_k=1))["code"] == "validation"


# ---------------------------------------------------------------- real GPT-2


@pytest.mark.slow
def test_real_model_end_to_end():
    from grammarguard.decoding.engine import Engine

    c, _ = make_client(Engine("gpt2"))
    with c:
        events = stream(
            c, "/generate", constraint={"text": "EMP-[0-9]{3}"}, prompt="Employee ID: ", compare=True, max_tokens=20
        )
        dones = [d for n, d in events if n == "done"]
        assert dones[0]["source"] == "unconstrained" and dones[1]["accepted"] and dones[1]["stop_reason"] == "eos"
        assert any(n == "token" and d["telemetry"] for n, d in events)
        assert post(c, "/certificate/verify", certificate=dones[1]["certificate"]).json()["valid"]
        events = stream(c, "/generate/json", prompt='{"name":"ann","age":31}\n{"name":"bob","age":25}\n', max_tokens=40)
        done = [d for n, d in events if n == "done"][0]
        assert (
            done["stop_reason"] in ("eos", "max_tokens")
            and post(c, "/certificate/verify", certificate=done["certificate"]).json()["valid"]
        )
        batch = post(
            c, "/generate/batch", constraint={"text": "[0-9]{4}-[0-9]{2}-[0-9]{2}"}, prompt="Date: ", count=3
        ).json()
        assert len(batch["samples"]) == 3 and len({s["text"] for s in batch["samples"]}) == 3


@pytest.mark.slow
def test_real_model_disconnect_frees_the_engine():
    from grammarguard.decoding.engine import Engine

    engine = Engine("gpt2")
    c, _ = make_client(engine)
    with c:
        with c.stream("POST", API + "/generate", json={"constraint": {"text": "[a-z]+"}, "max_tokens": 200}) as r:
            seen = 0
            for line in r.iter_lines():
                seen += line.startswith("event: token")
                if seen == 2:
                    break
        deadline = time.time() + 10
        while engine.busy and time.time() < deadline:
            time.sleep(0.1)
        assert not engine.busy
