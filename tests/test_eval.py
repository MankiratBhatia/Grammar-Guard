"""Benchmark harness tests: statistics, aggregation, report, suite sanity (fast), and one tiny real run (slow)."""

import math

import pytest

from grammarguard.core.dfa import regex_to_dfa
from grammarguard.decoding.generate import Step
from grammarguard.eval.report import markdown, pct
from grammarguard.eval.runner import RETRIES, _retry, compile_case, has_valid_prefix
from grammarguard.eval.stats import mean, rate, summarize, summarize_group, wilson
from grammarguard.eval.suite import CASES, QUICK_IDS, Case


@pytest.mark.parametrize(
    "k,n,lo,hi",
    [(0, 10, 0.0, 0.2775), (10, 10, 0.7225, 1.0), (5, 10, 0.2366, 0.7634), (1, 2, 0.0945, 0.9055)],
)
def test_wilson_matches_textbook_values(k, n, lo, hi):
    """Known 95% Wilson intervals (hand-checkable: 0/10 -> upper bound 3.84 / (10 + 3.84) = 0.2775)."""
    got = wilson(k, n)
    assert got[0] == pytest.approx(lo, abs=1e-3) and got[1] == pytest.approx(hi, abs=1e-3)


def test_wilson_edge_cases():
    """n = 0 is handled; the interval always contains the observed rate and stays inside [0, 1]."""
    assert wilson(0, 0) == (0.0, 0.0)
    for n in (1, 7, 100):
        for k in range(n + 1):
            lo, hi = wilson(k, n)
            assert 0 <= lo <= k / n <= hi <= 1


def test_wilson_interval_narrows_with_more_data():
    """Same rate, ten times the data: a tighter interval."""
    a, b = wilson(5, 10), wilson(50, 100)
    assert (b[1] - b[0]) < (a[1] - a[0])


def test_rate_and_mean():
    assert rate([True, True, False, False]) == {
        "k": 2,
        "n": 4,
        "rate": 0.5,
        "lo": wilson(2, 4)[0],
        "hi": wilson(2, 4)[1],
    }
    assert rate([])["rate"] == 0.0
    assert mean([1, 2, 3]) == 2 and mean([]) is None


def run(mode, valid, tokens=10, seconds=1.0, case="a", sampling="greedy", **extra):
    base = {
        "case": case,
        "prompt": 0,
        "sampling": sampling,
        "seed": 0,
        "mode": mode,
        "valid": valid,
        "valid_prefix": extra.pop("valid_prefix", valid),
        "stop_reason": "eos",
        "tokens": tokens,
        "seconds": seconds,
    }
    return {**base, **extra}


def test_summarize_group_numbers():
    """Hand-computed aggregates: validity rates, prefix validity, attempts, pressure, slowdown."""
    runs = [
        run("unconstrained", False, tokens=30, seconds=3.0, valid_prefix=True),
        run("unconstrained", False, tokens=30, seconds=3.0, valid_prefix=False),
        run("unconstrained", True, tokens=10, seconds=1.0),
        run("retry5", True, tokens=60, seconds=6.0, attempts=2),
        run("retry5", False, tokens=150, seconds=15.0, attempts=5),
        run("constrained", True, tokens=5, seconds=1.0, mean_pressure=0.2, total_cost_bits=4.0, overrides=1),
        run("constrained", True, tokens=5, seconds=1.0, mean_pressure=0.4, total_cost_bits=8.0, overrides=3),
    ]
    g = summarize_group(runs)
    assert g["unconstrained"]["validity"]["k"] == 1 and g["unconstrained"]["validity"]["n"] == 3
    assert g["unconstrained"]["prefix_validity"]["k"] == 2  # the valid run counts too
    assert g["retry5"]["mean_attempts"] == 3.5 and g["retry5"]["validity"]["rate"] == 0.5
    assert g["retry5"]["prefix_validity"]["k"] == 1  # only the strictly valid retry run counts as a valid prefix here
    c = g["constrained"]
    assert c["validity"]["rate"] == 1.0 and c["mean_pressure"] == pytest.approx(0.3)
    assert c["mean_cost_bits"] == 6.0 and c["mean_overrides"] == 2.0
    # unconstrained: mean of (10, 10, 10) tokens/s = 10; constrained: 5 tokens/s -> twice as slow
    assert g["slowdown"] == pytest.approx(2.0)


def test_summarize_groups_by_category_case_and_sampling():
    cases = [{"id": "a", "category": "dates"}, {"id": "b", "category": "ids"}]
    runs = [
        run("constrained", True, case="a", sampling="greedy"),
        run("constrained", False, case="b", sampling="sample"),
    ]
    s = summarize(runs, cases)
    assert set(s["by_category"]) == {"dates", "ids"} and set(s["by_case"]) == {"a", "b"}
    assert set(s["by_sampling"]) == {"greedy", "sample"}
    assert s["by_category"]["dates"]["constrained"]["validity"]["rate"] == 1.0
    assert s["by_category"]["ids"]["constrained"]["validity"]["rate"] == 0.0
    assert s["overall"]["constrained"]["validity"]["n"] == 2


def test_markdown_report_contains_the_headline_numbers():
    cases = [
        {
            "id": "a",
            "category": "dates",
            "dfa_states": 12,
            "live_states": 11,
            "index_build_ms": 5.0,
            "kind": "regex",
            "text": "x",
        }
    ]
    runs = [
        run("unconstrained", False, valid_prefix=False),
        run("retry5", True, attempts=2),
        run("constrained", True, mean_pressure=0.5, total_cost_bits=3.0, overrides=1),
    ]
    results = {
        "meta": {
            "date": "2026-10-28T00:00:00+00:00",
            "model": "gpt2",
            "commit": "abc",
            "cpus": 4,
            "torch": "2",
            "wall_seconds": 10,
            "retries": 5,
        },
        "cases": cases,
        "runs": runs,
        "summary": summarize(runs, cases),
    }
    md = markdown(results)
    assert (
        "GrammarGuard (DFA-constrained) | 100%" in md
        and "Unconstrained GPT-2, as is" in md
        and "Unconstrained + extract a valid prefix" in md
        and "Best-of-5 retries + extract a valid prefix" in md
        and "| dates |" in md
        and "| a | 12 | 5 ms |" in md
    )
    assert "0% (0–" in md  # unconstrained never valid
    assert pct(rate([True] * 3)).startswith("100%")


def test_suite_is_sane():
    """Unique ids, two prompts each, every constraint compiles to a non-empty language."""
    ids = [c.id for c in CASES]
    assert len(ids) == len(set(ids)) and set(QUICK_IDS) <= set(ids)
    for case in CASES:
        assert len(case.prompts) == 2 and all(p.strip() for p in case.prompts), case.id
        dfa = compile_case(case)
        assert dfa.start in dfa.live_states(), f"{case.id} accepts nothing"


def test_has_valid_prefix():
    d = regex_to_dfa("[0-9]{4}")
    assert has_valid_prefix(d, "2024 is the year") and not has_valid_prefix(d, "20 24")
    assert not has_valid_prefix(d, "")
    # only NON-empty prefixes count: "b" has none that matches a*, although the empty string does
    assert not has_valid_prefix(regex_to_dfa("a*"), "b") and has_valid_prefix(regex_to_dfa("a*"), "ab")


def test_has_valid_prefix_matches_brute_force():
    d = regex_to_dfa("(ab|a)*b")
    for text in ["", "b", "abx", "xab", "aab", "aaa", "abab!"]:
        assert has_valid_prefix(d, text) == any(d.accepts(text[:i]) for i in range(1, len(text) + 1)), text


@pytest.mark.slow
def test_tiny_real_benchmark_run(tmp_path):
    """End to end with GPT-2 on one validator with few tokens: structure, validity and oracle agreement."""
    import json

    from grammarguard.decoding.engine import Engine
    from grammarguard.eval.runner import run_suite, selected_cases

    results = run_suite(Engine("gpt2"), tuple(c for c in selected_cases(True) if c.id == "employee_id"), max_tokens=12)
    json.dumps(results)  # serializable
    runs = results["runs"]
    assert {r["mode"] for r in runs} == {"unconstrained", "constrained", "retry5"}
    assert all(r["valid"] for r in runs if r["mode"] == "constrained" and r["stop_reason"] == "eos")
    assert all(r["oracle_agrees"] for r in runs), "our DFA and Python's re must agree on every output"
    assert math.isfinite(results["summary"]["overall"]["constrained"]["validity"]["rate"])
    assert results["cases"][0]["index_build_ms"] > 0 and "slowdown" in results["summary"]["overall"]
    assert "Benchmark results" in markdown(results)


# ---------------------------------------------------------------- fairness of the baselines


class ScriptedEngine:
    """Stands in for Engine.unconstrained: each call returns the next scripted text as a one-token generation."""

    def __init__(self, texts):
        self.texts = iter(texts)
        self.calls = 0

    def unconstrained(self, prompt, **opts):
        self.calls += 1
        text = next(self.texts)

        def gen():
            yield Step(text, None, 1, None)
            return "max_tokens"

        return gen()


CASE = Case("four_digits", "ids", "regex", "[0-9]{4}", ("Number: ", "Numbers: 1234, 5678, "))
OPTS = {"max_tokens": 5, "top_k": 20, "temperature": 1.0, "seed": 1}


def test_retry_stops_at_the_first_strict_match():
    """Best-of-N keeps the first output that matches completely and does not spend more attempts."""
    engine = ScriptedEngine(["xx", "2024 rest", "2024", "never used", "never used"])
    rec = _retry(engine, regex_to_dfa(CASE.text), CASE, "p", OPTS)
    assert (rec["valid"], rec["attempts"], rec["tokens"], engine.calls) == (True, 3, 3, 3)
    assert rec["valid_prefix"] is True and rec["text"] == "2024"


def test_retry_counts_a_valid_prefix_from_any_attempt():
    """Extraction credit must not depend on which attempt happened to be last: an early attempt with a valid
    prefix counts even though the final attempt does not (otherwise the baseline would be understated)."""
    engine = ScriptedEngine(["xx", "2024 is the year", "yy", "yy", "yy"])
    rec = _retry(engine, regex_to_dfa(CASE.text), CASE, "p", OPTS)
    assert rec["valid"] is False and rec["attempts"] == RETRIES == 5
    assert rec["valid_prefix"] is True and rec["text"] == "yy"


def test_retry_with_no_valid_attempt():
    engine = ScriptedEngine(["a"] * 5)
    rec = _retry(engine, regex_to_dfa(CASE.text), CASE, "p", OPTS)
    assert (rec["valid"], rec["valid_prefix"], rec["attempts"]) == (False, False, 5)


def test_prompts_end_at_an_item_boundary():
    """A prompt like '... EMP-' would make the model finish a half-written item, so the baseline would fail
    because of the prompt, not the model. Every prompt must end with whitespace (a fresh item starts next)."""
    for case in CASES:
        for prompt in case.prompts:
            assert prompt[-1] in " \n", f"{case.id}: prompt ends mid-item: {prompt!r}"
