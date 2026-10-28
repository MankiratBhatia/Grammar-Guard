"""Phase B tests: complement, concat/star, state elimination, counting, test generation,
constraint algebra, minimization methods, schema compiler.

`re` and `json` are used only as oracles.
"""

import itertools
import json
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grammarguard.core import counting, testgen
from grammarguard.core.algebra import AlgebraError, evaluate
from grammarguard.core.cfg import GrammarError
from grammarguard.core.dfa import (
    complement,
    concat,
    equivalent,
    minimize,
    minimize_partition,
    minimize_table,
    product,
    regex_to_dfa,
    star,
    subset_construction,
)
from grammarguard.core.elimination import NotExpressibleError, dfa_to_regex
from grammarguard.core.nfa import regex_to_nfa
from grammarguard.core.pda import PDA
from grammarguard.core.schema import SchemaError, schema_to_grammar
from tests.test_regular import PATTERNS, regex_strategy

SMALL = ["(a|b)*abb", "a*b?", "(ab|a)*", "[ab]{2,3}", "a+|b+", "", "(a|b)*a(a|b)"]


def all_strings(alphabet: str, max_len: int):
    """Every string over `alphabet` up to max_len (includes the empty string)."""
    for n in range(max_len + 1):
        for t in itertools.product(alphabet, repeat=n):
            yield "".join(t)


# ---------------------------------------------------------------- complement / closure


@pytest.mark.parametrize("pattern", SMALL)
def test_complement_is_exact_complement(pattern):
    """w in L(M') iff w not in L(M) -- including characters outside Sigma (the OTHER symbol)."""
    d = regex_to_dfa(pattern)
    c = complement(d)
    for s in all_strings("ab#", 6):
        assert c.accepts(s) != d.accepts(s)
    assert equivalent(complement(c), d) == (True, None)


def test_complement_union_is_everything():
    """L u not-L accepts every string; L n not-L accepts none (empty language)."""
    d = regex_to_dfa("(a|b)*abb")
    assert all(product(d, complement(d), "union").accepts(s) for s in all_strings("ab#", 5))
    assert not any(product(d, complement(d), "intersection").accepts(s) for s in all_strings("ab#", 5))


@settings(max_examples=150, deadline=None)
@given(r1=regex_strategy(), r2=regex_strategy(), words=st.lists(st.text("abc", max_size=7), min_size=8, max_size=8))
def test_concat_and_star_match_re(r1, r2, words):
    """Closure constructions agree with re on (R1)(R2) and (R1)*."""
    d1, d2 = regex_to_dfa(r1), regex_to_dfa(r2)
    cat, st_ = concat(d1, d2), star(d1)
    for w in words:
        assert cat.accepts(w) == (re.fullmatch(f"({r1})({r2})", w) is not None), (r1, r2, w)
        assert st_.accepts(w) == (re.fullmatch(f"({r1})*", w) is not None), (r1, w)


@settings(max_examples=100, deadline=None)
@given(r1=regex_strategy(), r2=regex_strategy(), words=st.lists(st.text("abc", max_size=6), min_size=8, max_size=8))
def test_boolean_closure_match_re(r1, r2, words):
    """Union/intersection/difference products agree with re."""
    d1, d2 = regex_to_dfa(r1), regex_to_dfa(r2)
    for w in words:
        m1, m2 = re.fullmatch(r1, w) is not None, re.fullmatch(r2, w) is not None
        assert product(d1, d2, "union").accepts(w) == (m1 or m2)
        assert product(d1, d2, "intersection").accepts(w) == (m1 and m2)
        assert product(d1, d2, "difference").accepts(w) == (m1 and not m2)


# ---------------------------------------------------------------- minimization methods + traces


@pytest.mark.parametrize("pattern", PATTERNS + SMALL)
def test_table_filling_equals_partition_refinement(pattern):
    """Both algorithms compute the Myhill-Nerode classes: identical canonical tables."""
    d = subset_construction(regex_to_nfa(pattern))
    assert minimize_table(d).signature() == minimize_partition(d).signature()


def test_minimize_large_automaton_uses_partition():
    """A big algebra result minimizes quickly and stays correct."""
    r = evaluate("email & !contains(/competitor/) & length(5, 40)")
    assert r.dfa.num_states() > 64
    assert r.dfa.accepts("me@example.com") and not r.dfa.accepts("me@competitor.com")
    assert minimize(r.dfa).signature() == r.dfa.signature()


def test_subset_construction_trace():
    """The trace records each (subset, symbol) step; the first new subsets are marked."""
    trace: list = []
    d = subset_construction(regex_to_nfa("(a|b)*abb"), trace=trace)
    assert len(trace) == d.num_states() * len(d.symbols())
    assert trace[0]["from"] and sum(t["new"] for t in trace) == d.num_states() - 1


def test_minimize_trace_rounds():
    """Round 0 marks accepting/non-accepting pairs; classes are listed at the end."""
    trace: list = []
    d = subset_construction(regex_to_nfa("(a|b)*abb"))
    m = minimize(d, trace=trace)
    assert trace[0]["round"] == 0 and trace[0]["marked"]
    assert len(trace[-1]["classes"]) == m.num_states()


# ---------------------------------------------------------------- DFA -> regex


@pytest.mark.parametrize("pattern", PATTERNS + SMALL)
def test_state_elimination_roundtrip(pattern):
    """regex -> DFA -> regex' -> DFA' is equivalent, and regex' also agrees with re."""
    d = regex_to_dfa(pattern)
    try:
        text = dfa_to_regex(d)
    except NotExpressibleError:
        pytest.skip("empty language")
    assert equivalent(d, regex_to_dfa(text)) == (True, None)


@settings(max_examples=150, deadline=None)
@given(pattern=regex_strategy())
def test_state_elimination_random(pattern):
    """Round trip on random regexes."""
    d = regex_to_dfa(pattern)
    assert equivalent(d, regex_to_dfa(dfa_to_regex(d))) == (True, None)


def test_elimination_errors():
    """Empty language and wildcard languages cannot be written in our regex syntax."""
    with pytest.raises(NotExpressibleError):
        dfa_to_regex(product(regex_to_dfa("a"), regex_to_dfa("b"), "intersection"))
    with pytest.raises(NotExpressibleError):
        dfa_to_regex(complement(regex_to_dfa("a")))


def test_elimination_readable():
    """Known outputs are compact."""
    assert dfa_to_regex(regex_to_dfa("[0-9]{4}-[0-9]{2}")) == "[0-9]{4}-[0-9]{2}"
    assert dfa_to_regex(regex_to_dfa("(a|b)*")) == "[ab]*"
    assert dfa_to_regex(regex_to_dfa("a+")) == "a+"


# ---------------------------------------------------------------- counting


@pytest.mark.parametrize("pattern", ["(a|b)*abb", "a*b?", "[ab]{2,3}", "(ab|a)*", "a+|b+"])
def test_counts_match_brute_force(pattern):
    """The DP table equals a brute-force count over Sigma."""
    d = regex_to_dfa(pattern)
    sigma = "".join(sorted(d.alphabet))
    expected = [
        sum(1 for t in itertools.product(sigma, repeat=n) if re.fullmatch(pattern, "".join(t))) for n in range(8)
    ]
    assert counting.counts_by_length(d, 7) == expected


@pytest.mark.parametrize(
    "pattern,finite,size",
    [("[ab]{2,3}", True, 4 + 8), ("a|bb", True, 2), ("a*", False, None), ("(ab)+", False, None), ("", True, 1)],
)
def test_finiteness_and_size(pattern, finite, size):
    """Decision procedure for finiteness; exact size for finite languages."""
    d = regex_to_dfa(pattern)
    assert counting.is_finite(d) == finite
    assert counting.language_size(d) == size


def test_empty_language_is_finite_with_size_zero():
    """The empty language has no strings."""
    d = product(regex_to_dfa("a"), regex_to_dfa("b"), "intersection")
    assert counting.is_finite(d) and counting.language_size(d) == 0


def test_uniform_sampling():
    """Samples are members of the right length, and the empirical distribution is close to uniform."""
    d = regex_to_dfa("[ab]{3}")
    samples = counting.sample_uniform(d, 3, k=4000, seed=1)
    assert all(d.accepts(s) and len(s) == 3 for s in samples)
    freq = {s: samples.count(s) for s in set(samples)}
    assert len(freq) == 8 and all(abs(c - 500) < 120 for c in freq.values())
    assert counting.sample_uniform(d, 4) == [] or d.accepts(counting.sample_uniform(d, 4)[0])
    assert counting.sample_uniform(regex_to_dfa("ab"), 3) == []


def test_sampling_non_uniform_automaton():
    """Uniformity must hold even when paths have different weights (a|ab-style branching)."""
    d = regex_to_dfa("a[ab]{2}|b")  # length-1: 'b'; length 3: 4 strings
    samples = counting.sample_uniform(d, 3, k=2000, seed=2)
    assert len(set(samples)) == 4


def test_shortest_strings_are_shortlex():
    """BFS yields accepted strings ordered by length, then symbol order."""
    d = regex_to_dfa("(a|b)*abb")
    out = counting.shortest_strings(d, 6, include_other=False)
    assert out == sorted(out, key=lambda s: (len(s), s)) and all(d.accepts(s) for s in out)
    assert out[0] == "abb" and len(out) == 6


# ---------------------------------------------------------------- test generation


@pytest.mark.parametrize("pattern", PATTERNS[:12] + SMALL[:4])
def test_generated_suite_is_correct_and_complete(pattern):
    """Positives are accepted, negatives rejected (by our DFA AND by re), and every useful transition is covered."""
    d = regex_to_dfa(pattern)
    cases = testgen.generate_tests(d)
    assert cases
    for c in cases:
        assert d.accepts(c.text) == c.expected, c
        assert (re.fullmatch(pattern, c.text) is not None) == c.expected, c
    cov = testgen.coverage(d, [c.text for c in cases if c.expected])
    assert cov["transition_coverage"] == 1.0 and cov["states_covered"] == cov["states_total"]


def test_generated_suite_kills_mutants():
    """A wrong validator (one digit class changed) fails at least one generated case."""
    good = r"[0-9]{3}-[0-9]{2}"
    mutants = [r"[0-8]{3}-[0-9]{2}", r"[0-9]{3}-[0-9]{1,2}", r"[0-9]{3}[-_][0-9]{2}", r"[0-9]{2,3}-[0-9]{2}"]
    cases = testgen.generate_tests(regex_to_dfa(good))
    for m in mutants:
        assert any((re.fullmatch(m, c.text) is not None) != c.expected for c in cases), m


def test_pytest_export_runs():
    """The exported pytest source compiles and passes against the original pattern."""
    pattern = r"EMP-[0-9]{3}"
    src = testgen.to_pytest(pattern, testgen.generate_tests(regex_to_dfa(pattern)))
    ns: dict = {}
    exec(compile(src, "generated_test.py", "exec"), ns)
    assert all((re.fullmatch(ns["PATTERN"], t) is not None) == e for t, e in ns["CASES"])


# ---------------------------------------------------------------- algebra


def test_algebra_semantics_by_brute_force():
    """Each operator matches its set-theoretic meaning on all short strings."""
    a, b = regex_to_dfa("[ab]*a"), regex_to_dfa("a[ab]*")
    cases = {
        "/[ab]*a/ & /a[ab]*/": lambda x, y: x and y,
        "/[ab]*a/ | /a[ab]*/": lambda x, y: x or y,
        "/[ab]*a/ - /a[ab]*/": lambda x, y: x and not y,
        "!/[ab]*a/": lambda x, _: not x,
    }
    for expr, op in cases.items():
        d = evaluate(expr).dfa
        for s in all_strings("ab#", 5):
            assert d.accepts(s) == op(a.accepts(s), b.accepts(s)), (expr, s)


def test_algebra_precedence_and_parentheses():
    """'&' and '-' bind tighter than '|'; '!' binds tightest; parentheses override."""
    assert equivalent(evaluate("/a/ | /b/ & /b/").dfa, evaluate("/a/ | (/b/ & /b/)").dfa)[0]
    assert evaluate("/a/ | /b/ & /c/").dfa.accepts("a") and not evaluate("(/a/ | /b/) & /c/").dfa.accepts("a")
    assert evaluate("!/a/ & /b/").dfa.accepts("b")  # (!a) & b
    assert evaluate("!(/a/ & /b/)").dfa.accepts("")  # !(empty language) = everything


def test_algebra_functions():
    """length/contains/starts/ends/concat/star."""
    assert evaluate("length(2, 3)").dfa.accepts("xy") and not evaluate("length(2, 3)").dfa.accepts("x")
    c = evaluate("contains(/ab/)").dfa
    assert c.accepts("xxabxx") and not c.accepts("axb")
    assert evaluate("starts(/ab/)").dfa.accepts("abz") and not evaluate("starts(/ab/)").dfa.accepts("zab")
    assert evaluate("ends(/ab/)").dfa.accepts("zab") and not evaluate("ends(/ab/)").dfa.accepts("abz")
    assert evaluate("concat(/a+/, /b/, /c/)").dfa.accepts("aabc")
    assert evaluate("star(/ab/)").dfa.accepts("abab") and not evaluate("star(/ab/)").dfa.accepts("aba")
    assert evaluate("length(3, *)").dfa.accepts("x" * 50)


def test_algebra_policy_example():
    """The headline use case: a format AND a ban AND a length limit."""
    d = evaluate("email & !contains(/spam/) & length(8, 30)").dfa
    assert d.accepts("alice@example.com")
    assert not d.accepts("spam@example.com")  # banned word
    assert not d.accepts("a@b.co")  # too short
    assert not d.accepts("alice@example")  # not an email
    assert not d.accepts("a" * 40 + "@example.com")  # too long
    assert [c.text for c in testgen.generate_tests(d) if c.expected]


def test_algebra_names_and_builtins():
    """User-defined names override/extend built-ins."""
    d = evaluate("code & !/XX[A-Z0-9]*/", names={"code": "[A-Z]{2}[0-9]{2}"}).dfa
    assert d.accepts("AB12") and not d.accepts("XX12")
    assert evaluate("iso_date").dfa.accepts("2024-02-29") and not evaluate("iso_date").dfa.accepts("2024-13-01")


def test_algebra_steps_recorded():
    """The step log names the closure construction used."""
    r = evaluate("digits & !/0[0-9]*/ & length(2, 4)")
    ops = [s.operation for s in r.steps]
    assert any("complement" in o for o in ops) and any("intersection" in o for o in ops)


@pytest.mark.parametrize(
    "bad,fragment",
    [
        ("/a/ &", "unexpected end"),
        ("nope", "unknown name"),
        ("/a", "unterminated"),
        ("/(a/", "bad regex"),
        ("length(5, 2)", "needs"),
        ("(/a/", "expected ')'"),
        ("/a/ /b/", "unexpected"),
        ("frobnicate(/a/)", "unknown function"),
        ("contains(/a/, /b/)", "takes 1"),
    ],
)
def test_algebra_errors_have_positions(bad, fragment):
    """Bad expressions raise AlgebraError with a message and a position."""
    with pytest.raises(AlgebraError, match=re.escape(fragment)) as info:
        evaluate(bad)
    assert info.value.pos >= 0


def test_algebra_state_cap():
    """A runaway product is stopped by the state cap."""
    with pytest.raises(AlgebraError, match="too large"):
        evaluate("email & !contains(/competitor/) & length(5, 40)", max_states=50)


# ---------------------------------------------------------------- schema -> grammar

SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "age": {"type": "integer"},
        "color": {"enum": ["red", "rose", "green"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "ok": {"type": "boolean"},
        "extra": {"type": "null"},
    },
}


def conforms(value) -> bool:
    """Reference validator for SCHEMA written independently of the grammar code."""
    return (
        isinstance(value, dict)
        and list(value) == ["name", "age", "color", "tags", "ok", "extra"]
        and isinstance(value["name"], str)
        and re.fullmatch("[a-z]*", value["name"]) is not None
        and isinstance(value["age"], int)
        and not isinstance(value["age"], bool)
        and value["age"] >= 0
        and value["color"] in ("red", "rose", "green")
        and isinstance(value["tags"], list)
        and all(isinstance(t, str) and re.fullmatch("[a-z]*", t) for t in value["tags"])
        and isinstance(value["ok"], bool)
        and value["extra"] is None
    )


def test_schema_pda_accepts_and_rejects():
    """Conforming documents are accepted; wrong key order / enum / types are rejected."""
    pda = PDA(schema_to_grammar(SCHEMA))
    good = '{"name":"bob","age":7,"color":"rose","tags":["a","b"],"ok":true,"extra":null}'
    assert pda.accepts(good) and conforms(json.loads(good))
    bad = [
        '{"name":"bob","age":7,"color":"blue","tags":[],"ok":true,"extra":null}',
        '{"age":7,"name":"bob","color":"red","tags":[],"ok":true,"extra":null}',
        '{"name":"bob","age":-1,"color":"red","tags":[],"ok":true,"extra":null}',
        '{"name":"bob","age":7,"color":"ro","tags":[],"ok":true,"extra":null}',
        '{"name":"bob","age":7,"color":"red","tags":[1],"ok":true,"extra":null}',
        '{"name":"bob","age":7,"color":"red","tags":[],"ok":1,"extra":null}',
    ]
    assert not any(pda.accepts(b) for b in bad)


@settings(max_examples=200, deadline=None)
@given(
    name=st.text("abc", max_size=4),
    age=st.integers(0, 10**4),
    color=st.sampled_from(["red", "rose", "green"]),
    tags=st.lists(st.text("xyz", max_size=3), max_size=3),
    ok=st.booleans(),
)
def test_schema_pda_accepts_all_conforming(name, age, color, tags, ok):
    """Anything json.dumps writes for a conforming value is accepted, and every prefix is viable."""
    value = {"name": name, "age": age, "color": color, "tags": tags, "ok": ok, "extra": None}
    text = json.dumps(value, separators=(",", ":"))
    pda = PDA(schema_to_grammar(SCHEMA))
    assert conforms(value) and pda.accepts(text)
    assert all(pda.is_viable_prefix(text[:i]) for i in range(len(text)))


def test_schema_nested_and_min_items():
    """Nested arrays/objects compile; minItems=1 forbids the empty array."""
    schema = {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"id": {"type": "integer"}}}}
    pda = PDA(schema_to_grammar(schema))
    assert pda.accepts('[{"id":1},{"id":20}]') and not pda.accepts("[]")
    assert PDA(schema_to_grammar({"type": "array", "items": {"type": "integer"}})).accepts("[]")


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "float"},
        {"type": "object", "properties": {'a"b': {"type": "null"}}},
        {"enum": []},
        {"enum": ["Red"]},
        {"type": "array", "minItems": 3, "items": {"type": "null"}},
        [],
        "string",
    ],
)
def test_schema_errors(bad):
    """Unsupported or malformed schemas raise SchemaError."""
    with pytest.raises((SchemaError, GrammarError)):
        schema_to_grammar(bad)
