"""Phase C tests: trie/lazy token index, telemetry, certificates, PDA trie walk, engine.

The first half uses synthetic vocabularies (fast, offline); tests marked `slow` use real GPT-2.
"""

import json
import math
import threading
import time

import pytest
import torch
from hypothesis import given, settings
from hypothesis import strategies as st

from grammarguard.core.algebra import evaluate
from grammarguard.core.certificate import (
    Certificate,
    make_dfa_certificate,
    make_pda_certificate,
    verify_dfa_certificate,
    verify_pda_certificate,
)
from grammarguard.core.dfa import regex_to_dfa
from grammarguard.core.pda import JSON_PDA, PDA
from grammarguard.core.schema import schema_to_grammar
from grammarguard.decoding.engine import CancelToken, Engine
from grammarguard.decoding.generate import (
    Step,
    Telemetry,
    _telemetry,
    collect,
    generate_constrained,
    generate_unconstrained,
    load_model,
    summarize,
)
from grammarguard.decoding.pda_generate import generate_json, valid_tokens
from grammarguard.decoding.token_index import (
    LazyTokenIndex,
    VocabTrie,
    build_index_loop,
    build_index_trie,
    vocab_trie,
)
from tests.test_regular import PATTERNS, regex_strategy
from tests.test_theory import SCHEMA, conforms

# ---------------------------------------------------------------- trie index == reference loop

vocab_strategy = st.lists(st.one_of(st.none(), st.text("abcd01-", max_size=4)), min_size=1, max_size=60)


@settings(max_examples=200, deadline=None)
@given(pattern=regex_strategy(), vocab=vocab_strategy)
def test_trie_index_equals_loop_on_random_vocab(pattern, vocab):
    """The pruned trie walk returns exactly the reference per-token loop's index."""
    d = regex_to_dfa(pattern)
    ref, fast = build_index_loop(d, vocab), build_index_trie(d, VocabTrie(vocab))
    assert ref.allowed == fast.allowed and ref.next_state == fast.next_state


@settings(max_examples=60, deadline=None)
@given(vocab=vocab_strategy)
def test_trie_index_equals_loop_with_complement(vocab):
    """Also for constraints where OTHER leads to a live state (nothing is pruned)."""
    for expr in ["!contains(/ab/)", "length(2, 3) & !/a./".replace(".", "[ab]"), "starts(/a/) - ends(/b/)"]:
        d = evaluate(expr).dfa
        ref, fast = build_index_loop(d, vocab), build_index_trie(d, VocabTrie(vocab))
        assert ref.allowed == fast.allowed and ref.next_state == fast.next_state, expr


def test_trie_shares_prefixes():
    """Tokens with a common prefix share trie nodes; duplicates of one text share a node's token list."""
    trie = VocabTrie(["20", "201", "2019", "2019", None, "", "x"])
    assert len(trie) == 1 + 4 + 1  # root, 2-0-1-9 chain, x
    assert sorted(
        trie.tokens_at[trie.children[trie.children[trie.children[trie.children[0]["2"]]["0"]]["1"]]["9"]]
    ) == [2, 3]


def test_lazy_index_matches_eager_and_is_lazy():
    """LazyTokenIndex rows equal the eager rows, and untouched states are never computed."""
    vocab = [a + b for a in "ab01-" for b in " ab01-"] + list("ab01-")
    d = evaluate("email & length(5, 40)").dfa
    trie = VocabTrie([v for v in vocab])
    eager, lazy = build_index_trie(d, trie), LazyTokenIndex(d, trie)
    assert lazy.rows_computed == 0
    for q in (d.start, 3, 5):
        assert lazy.allowed_for(q) == eager.allowed_for(q)
        for t in lazy.allowed_for(q)[:5]:
            assert lazy.next_for(q, t) == eager.next_for(q, t)
    assert lazy.rows_computed == 3 < d.num_states()
    assert lazy.allowed_for(d.num_states() + 7) == []  # unknown / non-live state


# ---------------------------------------------------------------- PDA trie walk


def test_valid_tokens_trie_equals_brute_force():
    """The trie walk finds exactly the tokens a per-token PDA run accepts, with the same resulting stacks."""
    vocab = ['{"', '"', "a", "ab", ":", ",", "1", "12", "}", "]", "[", "tru", "true", "nul", "x1", "},", '":', ""]
    trie = VocabTrie(vocab)
    for prefix in ["", "{", '{"a', '{"a":', '{"a":[1', '{"a":[1,tr', '{"a":1}']:
        stack = JSON_PDA.run(JSON_PDA.initial(), prefix)
        assert stack is not None
        expected = {t: JSON_PDA.run(stack, s) for t, s in enumerate(vocab) if s and JSON_PDA.run(stack, s) is not None}
        assert valid_tokens(JSON_PDA, stack, trie) == expected, prefix


# ---------------------------------------------------------------- telemetry maths


class _Tok:
    """Minimal tokenizer stub: decode(ids) -> 't<id>'."""

    def decode(self, ids):
        return "".join(f"t{i}" for i in ids)


def test_telemetry_numbers():
    """mass, pressure, cost (bits), entropies and override flag on a hand-checkable distribution."""
    # p = [0.5, 0.25, 0.125, 0.125]; allow tokens 1 and 2 -> mass 0.375
    logits = torch.log(torch.tensor([0.5, 0.25, 0.125, 0.125]))
    t = _telemetry(logits, torch.tensor([1, 2]), 2, _Tok())
    assert t.mass_kept == pytest.approx(0.375)
    assert t.pressure == pytest.approx(0.625)
    assert t.cost_bits == pytest.approx(-math.log2(0.375))
    assert t.preferred == "t0" and t.preferred_legal is False
    assert t.p_chosen == pytest.approx(0.125)
    assert t.entropy_before == pytest.approx(1.75)
    q = [0.25 / 0.375, 0.125 / 0.375]
    assert t.entropy_after == pytest.approx(-sum(x * math.log2(x) for x in q))
    # nothing removed -> zero pressure, zero cost, legal preferred
    t = _telemetry(logits, torch.arange(4), 0, _Tok())
    assert (
        t.pressure == pytest.approx(0, abs=1e-12) and t.cost_bits == pytest.approx(0, abs=1e-12) and t.preferred_legal
    )


def test_summarize():
    """Aggregates: means, max, totals, override count."""

    def tel(mass, legal):
        return Telemetry(mass, 1 - mass, -math.log2(mass), "x", legal, 0.1, 3.0, 2.0)

    steps = [Step("a", 1, 5, tel(0.5, True)), Step("b", 2, 5, tel(0.25, False)), Step("c", 3, 5, None)]
    m = summarize(steps)
    assert m["steps"] == 3 and m["constrained_steps"] == 2
    assert m["mean_pressure"] == pytest.approx(0.625) and m["max_pressure"] == pytest.approx(0.75)
    assert m["total_cost_bits"] == pytest.approx(3.0) and m["overrides"] == 1 and m["override_rate"] == 0.5
    assert summarize([Step("a", None, 1)]) == {"steps": 1, "constrained_steps": 0}


# ---------------------------------------------------------------- certificates


def test_dfa_certificate_roundtrip_and_tampering():
    """A genuine certificate verifies; each kind of forgery is detected with a reason."""
    d = regex_to_dfa("[0-9]{4}-[0-9]{2}")
    good = make_dfa_certificate(d, "[0-9]{4}-[0-9]{2}", "2024-05", tokens=["20", "24", "-", "05"])
    v = verify_dfa_certificate(good, d)
    assert v.valid and v.accepted and good.token_ends == [2, 4, 5, 7]
    assert verify_dfa_certificate(Certificate.from_dict(json.loads(json.dumps(good.to_dict()))), d).valid

    def forged(**changes):
        data = good.to_dict() | changes
        return Certificate.from_dict(data)

    bad_trace = list(good.trace)
    bad_trace[3] = (bad_trace[3] + 1) % d.num_states()
    cases = {
        "step 3": forged(trace=bad_trace),
        "fingerprint": forged(fingerprint="0" * 64),
        "expected": forged(trace=good.trace[:-1]),
        "disagrees": forged(accepted=False),
        "step 1": forged(text="x024-05"),
        "not a DFA": forged(kind="pda"),
        "token boundaries": forged(token_ends=[9, 12]),
    }
    for needle, cert in cases.items():
        r = verify_dfa_certificate(cert, d)
        assert not r.valid and needle in r.reason, (needle, r)
    other = regex_to_dfa("[0-9]{4}-[0-9]{3}")
    assert "fingerprint" in verify_dfa_certificate(good, other).reason


def test_dfa_certificate_for_rejected_text_is_valid_but_not_accepted():
    """A certificate can honestly show that a text is NOT in the language."""
    d = regex_to_dfa("ab+")
    cert = make_dfa_certificate(d, "ab+", "abx")
    v = verify_dfa_certificate(cert, d)
    assert v.valid and not v.accepted and not cert.accepted


def test_token_boundary_mismatch_rejected_at_creation():
    """Tokens must concatenate to the text."""
    with pytest.raises(ValueError):
        make_dfa_certificate(regex_to_dfa("ab"), "ab", "ab", tokens=["a", "c"])


def test_pda_certificate():
    """Stack traces verify; tampering and rejected prefixes are handled."""
    text = '{"a":[1,2]}'
    good = make_pda_certificate(JSON_PDA, "json", text, tokens=['{"', 'a":', "[1,2]}"])
    genuine = verify_pda_certificate(good, JSON_PDA)
    assert genuine.valid and genuine.accepted and good.token_ends == [2, 5, 11]
    tampered = Certificate.from_dict(
        good.to_dict() | {"trace": [good.trace[0], *good.trace[1:5][::-1], *good.trace[5:]]}
    )
    assert not verify_pda_certificate(tampered, JSON_PDA).valid
    assert not verify_pda_certificate(Certificate.from_dict(good.to_dict() | {"accepted": False}), JSON_PDA).valid
    bad = make_pda_certificate(JSON_PDA, "json", "[1,]")
    v = verify_pda_certificate(bad, JSON_PDA)
    assert v.valid and not v.accepted and bad.trace[-1] is None
    schema_pda = PDA(schema_to_grammar(SCHEMA))
    assert "fingerprint" in verify_pda_certificate(good, schema_pda).reason
    assert verify_pda_certificate(make_pda_certificate(JSON_PDA, "json", ""), JSON_PDA).valid


@settings(max_examples=100, deadline=None)
@given(pattern=regex_strategy(), text=st.text("abc", max_size=8))
def test_dfa_certificates_always_verify_for_any_text(pattern, text):
    """For any text, the honestly produced certificate is valid, and 'accepted' agrees with the DFA."""
    d = regex_to_dfa(pattern)
    cert = make_dfa_certificate(d, pattern, text)
    v = verify_dfa_certificate(cert, d)
    assert v.valid and v.accepted == d.accepts(text)


# ---------------------------------------------------------------- cancel token


def test_cancel_token():
    """The token is falsy until cancelled, then truthy."""
    t = CancelToken()
    assert not t()
    t.cancel()
    assert t()


# ---------------------------------------------------------------- slow: real GPT-2

slow = pytest.mark.slow


@slow
@pytest.mark.parametrize("pattern", PATTERNS[:6])
def test_real_vocab_trie_equals_loop(pattern):
    """Trie index == reference loop on the real 50k-token GPT-2 vocabulary."""
    strings, trie = vocab_trie(load_model("gpt2")[0])
    d = regex_to_dfa(pattern)
    ref, fast = build_index_loop(d, strings), build_index_trie(d, trie)
    assert ref.allowed == fast.allowed and ref.next_state == fast.next_state


@slow
def test_real_valid_tokens_equals_scan():
    """PDA trie walk == per-token scan over the real vocabulary, at several stacks."""
    strings, trie = vocab_trie(load_model("gpt2")[0])
    for prefix in ["", "{", '{"name":', '{"name":"bo', "[1,"]:
        stack = JSON_PDA.run(JSON_PDA.initial(), prefix)
        scan = {t: r for t, s in enumerate(strings) if s and (r := JSON_PDA.run(stack, s)) is not None}
        assert valid_tokens(JSON_PDA, stack, trie) == scan, prefix


@slow
def test_telemetry_in_real_run_tells_the_story():
    """A prompt that already shows the format costs far fewer bits than one that fights it."""
    d = regex_to_dfa("[0-9]{4}-[0-9]{2}-[0-9]{2}")
    _, easy, why1 = collect(generate_constrained(d, "Today is 2024-05-17. Tomorrow is 2024-05-18. Date: "))
    _, hard, why2 = collect(generate_constrained(d, "My favourite animal is the "))
    assert why1 == why2 == "eos"
    for steps in (easy, hard):
        for s in steps:
            t = s.telemetry
            assert 0 < t.mass_kept <= 1 and t.pressure == pytest.approx(1 - t.mass_kept)
            assert t.entropy_after <= math.log2(s.num_allowed) + 1e-6
    e, h = summarize(easy), summarize(hard)
    assert e["total_cost_bits"] < h["total_cost_bits"] / 3 and e["overrides"] < h["overrides"]
    assert all(s.telemetry is None for s in collect(generate_unconstrained("Date: ", max_tokens=3))[1])


@slow
def test_cancellation_stops_generation():
    """should_stop is polled between tokens: cancelling after two tokens ends the run as 'cancelled'."""
    d = regex_to_dfa("[a-z]+")
    token = CancelToken()
    gen = generate_constrained(d, "Word: ", max_tokens=30, should_stop=token)
    steps = []
    while True:
        try:
            steps.append(next(gen))
        except StopIteration as stop:
            reason = stop.value
            break
        if len(steps) == 2:
            token.cancel()
    assert reason == "cancelled" and len(steps) == 2


@slow
def test_certificate_from_real_run_verifies_independently():
    """The verifier rebuilds the DFA from the constraint text alone and accepts the certificate."""
    constraint = "email & !contains(/spam/) & length(8, 30)"
    d = evaluate(constraint).dfa
    text, steps, why = collect(generate_constrained(d, "Email: ", max_tokens=25, top_k=5, seed=3))
    cert = make_dfa_certificate(d, constraint, text, tokens=[s.token_text for s in steps])
    cert = Certificate.from_dict(json.loads(json.dumps(cert.to_dict())))  # as if received over the wire
    v = verify_dfa_certificate(cert, evaluate(cert.constraint).dfa)
    assert v.valid and v.accepted == (why == "eos")


@slow
def test_schema_guided_generation():
    """With a schema PDA the output has the right keys in order and the right types."""
    pda = PDA(schema_to_grammar(SCHEMA))
    prompt = '{"name":"ann","age":31,"color":"red","tags":["a"],"ok":true,"extra":null}\n'
    text, steps, why = collect(generate_json(prompt, pda=pda, max_tokens=90, top_k=8, seed=5))
    assert pda.is_viable_prefix(text)
    if why == "eos":
        assert pda.accepts(text) and conforms(json.loads(text))
    cert = make_pda_certificate(pda, "schema", text, tokens=[s.token_text for s in steps])
    assert verify_pda_certificate(cert, pda).valid


@slow
def test_pda_steps_have_telemetry_and_cancel():
    """PDAStep carries mass/legality; cancellation works for the JSON generator too."""
    token = CancelToken()
    gen = generate_json('{"a":1}\n', max_tokens=30, should_stop=token)
    steps = []
    for step in gen:
        steps.append(step)
        assert 0 < step.mass_kept <= 1 + 1e-9 and isinstance(step.preferred_legal, bool)
        if len(steps) == 3:
            token.cancel()
    assert len(steps) == 3


@slow
def test_engine_index_cache_and_lock():
    """Repeated constraints hit the index cache; generations never overlap; stats are reported."""
    engine = Engine("gpt2")
    d = regex_to_dfa("EMP-[0-9]{3}")
    collect(engine.constrained(d, "ID: "))
    collect(engine.constrained(d, "ID: "))
    s = engine.stats()
    assert s["loaded"] and s["index_misses"] == 1 and s["index_hits"] == 1 and s["cached_indexes"] == 1

    running, overlap = [], []

    def work():
        gen = engine.constrained(d, "ID: ", max_tokens=6)
        for _ in gen:
            running.append(1)
            if not engine.busy:  # the lock must be held for the whole run
                overlap.append(1)
            time.sleep(0.01)

    threads = [threading.Thread(target=work) for _ in range(3)]
    [t.start() for t in threads]
    time.sleep(0.05)
    assert engine.busy
    [t.join() for t in threads]
    assert not overlap and not engine.busy and len(running) >= 3


@slow
def test_engine_abandoned_generator_releases_lock():
    """Closing a half-consumed generator frees the lock (a disconnected client cannot block others)."""
    engine = Engine("gpt2")
    gen = engine.unconstrained("Hello", max_tokens=20)
    next(gen)
    assert engine.busy
    gen.close()
    assert not engine.busy
