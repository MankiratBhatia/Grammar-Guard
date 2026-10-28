"""Phase 1 tests: my regex -> NFA -> DFA pipeline vs Python's `re` as an oracle.

`re` is used ONLY here, as a reference implementation.
"""

import itertools
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grammarguard.core import regex_parser as rp
from grammarguard.core.dfa import equivalent, minimize, product, regex_to_dfa, shortest_accepted, subset_construction
from grammarguard.core.nfa import regex_to_nfa

# ~15 realistic patterns (dates, phone numbers, IDs) plus classic textbook ones.
PATTERNS = [
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}",  # ISO date
    r"(0[1-9]|[12][0-9]|3[01])/(0[1-9]|1[0-2])/[0-9]{4}",  # dd/mm/yyyy
    r"([01][0-9]|2[0-3]):[0-5][0-9]",  # 24h time
    r"\([0-9]{3}\) [0-9]{3}-[0-9]{4}",  # US phone
    r"\+?[0-9]{1,3}-[0-9]{3,4}-[0-9]{4}",  # international phone
    r"[0-9]{3}-[0-9]{2}-[0-9]{4}",  # SSN-style ID
    r"[A-Z]{2}[0-9]{6}",  # passport-style ID
    r"EMP-[0-9]{3,5}",  # employee ID
    r"[a-z][a-z0-9_]{2,7}",  # username
    r"[a-z]+@[a-z]+\.(com|org|edu)",  # simple email
    r"-?[0-9]+(\.[0-9]+)?",  # decimal number
    r"(true|false|null)",
    r"(a|b)*abb",
    r"(a*b*)*c?",
    r"x(y|z){0,3}x?",
    r"(ab|a)*b{2,}",
]


def sigma_of(pattern: str) -> str:
    """Characters worth fuzzing with: the regex alphabet plus two outsiders."""
    return "".join(sorted(rp.alphabet(rp.parse(pattern)))) + "Q "


def strings_for(pattern: str):
    """Mix of strings that match (generated from the regex) and random ones."""
    sigma = sigma_of(pattern)
    matching = st.from_regex(pattern, fullmatch=True)
    random = st.text(alphabet=sigma, max_size=14)
    # Near-misses: a matching string with one character changed/dropped/added.
    mutated = st.tuples(matching, st.integers(0, 20), st.sampled_from(sigma), st.integers(0, 2)).map(
        lambda t: _mutate(*t)
    )
    return st.one_of(matching, random, mutated)


def _mutate(s: str, i: int, ch: str, kind: int) -> str:
    """Apply one edit (replace / delete / insert) at position i."""
    i = i % (len(s) + 1)
    if kind == 0:
        return s[:i] + ch + s[i + 1 :]
    if kind == 1:
        return s[:i] + s[i + 1 :]
    return s[:i] + ch + s[i:]


@pytest.fixture(scope="module", params=PATTERNS)
def machines(request):
    """Build NFA, DFA and minimal DFA once per pattern."""
    pattern = request.param
    nfa = regex_to_nfa(pattern)
    dfa = subset_construction(nfa)
    return pattern, nfa, dfa, minimize(dfa)


@pytest.mark.parametrize("pattern", PATTERNS)
@settings(max_examples=300, deadline=None)
@given(data=st.data())
def test_matches_python_re(pattern, data):
    """NFA simulation, DFA and minimal DFA all agree with re.fullmatch."""
    nfa = regex_to_nfa(pattern)
    dfa = subset_construction(nfa)
    mdfa = minimize(dfa)
    s = data.draw(strings_for(pattern))
    expected = re.fullmatch(pattern, s) is not None
    assert nfa.accepts(s) == expected
    assert dfa.accepts(s) == expected
    assert mdfa.accepts(s) == expected


def test_minimized_equivalent_to_unminimized(machines):
    """Minimization preserves the language and never adds states."""
    _, _, dfa, mdfa = machines
    assert equivalent(dfa, mdfa) == (True, None)
    assert mdfa.num_states() <= dfa.num_states()


def test_minimization_is_idempotent(machines):
    """A minimal DFA is already minimal: minimizing again gives the same table."""
    _, _, _, mdfa = machines
    again = minimize(mdfa)
    assert again.num_states() == mdfa.num_states()
    assert again.signature() == mdfa.signature()


def test_dfa_is_complete_and_has_dead_state(machines):
    """delta is total over Sigma and the dead state is a non-accepting trap."""
    _, _, dfa, mdfa = machines
    for d in (dfa, mdfa):
        for q in d.states:
            for a in d.alphabet:
                assert (q, a) in d.delta
        assert d.dead not in d.accept
        assert all(d.step(d.dead, a) == d.dead for a in d.alphabet)
        assert d.step(d.start, "☃") == d.dead  # symbol outside Sigma


def test_live_states(machines):
    """q is live iff some string from q is accepted (checked by brute force)."""
    _, _, _, mdfa = machines
    live = mdfa.live_states()
    assert mdfa.dead not in live
    for q in mdfa.states:
        sub = _with_start(mdfa, q)
        assert (q in live) == (shortest_accepted(sub) is not None)


def _with_start(d, q):
    """Same DFA but started in state q."""
    from dataclasses import replace

    return replace(d, start=q)


# ---------------------------------------------------------------- textbook cases


def test_known_minimal_sizes():
    """Sizes from the textbook (+1 for the explicit dead state)."""
    assert regex_to_dfa("(a|b)*abb").num_states() == 4 + 1
    assert regex_to_dfa("a*").num_states() == 1 + 1
    assert regex_to_dfa("(a*b*)*").num_states() == 1 + 1
    assert regex_to_dfa("[0-9]{4}-[0-9]{2}-[0-9]{2}").num_states() == 11 + 1


def test_epsilon_closure():
    """E(start) of a* contains the start, the inner start and the final state."""
    nfa = regex_to_nfa("a*")
    closure = nfa.epsilon_closure({nfa.start})
    assert nfa.start in closure and nfa.accept <= closure
    assert nfa.epsilon_closure(set()) == frozenset()


def test_thompson_shape():
    """Thompson NFA: one accept state with no outgoing edges; <= 2 states per AST node."""
    nfa = regex_to_nfa("(a|b)*abb")
    (f,) = nfa.accept
    assert not any(src == f for src, _, _ in nfa.edges())
    assert len(nfa.states) == 14  # union 6 + star 2 + 3 literals * 2 = 14


@pytest.mark.parametrize(
    "r1,r2,expected",
    [
        ("(a|b)*", "(a*b*)*", (True, None)),
        ("a+", "aa*", (True, None)),
        ("a{2,3}", "aa|aaa", (True, None)),
        ("(a|b)*abb", "(a|b)*bb", (False, "bb")),
        ("a*", "a+", (False, "")),
        ("[0-9]{2}", "[0-9]{2,3}", (False, "000")),
        ("ab", "ba", (False, "ab")),
    ],
)
def test_equivalence(r1, r2, expected):
    """Equivalence check returns the expected verdict / shortest counterexample."""
    assert equivalent(regex_to_dfa(r1), regex_to_dfa(r2)) == expected


@pytest.mark.parametrize("r1,r2", [("(a|b)*abb", "(a|b)*bb"), ("a*b", "ab*"), ("(ab)*", "a(ba)*b|"), ("x?y", "xy?")])
def test_counterexample_is_shortest(r1, r2):
    """Brute force: no shorter string is accepted by exactly one of the two."""
    d1, d2 = regex_to_dfa(r1), regex_to_dfa(r2)
    eq, w = equivalent(d1, d2)
    sigma = sorted(d1.alphabet | d2.alphabet)
    if eq:
        for n in range(6):
            for t in itertools.product(sigma, repeat=n):
                assert d1.accepts("".join(t)) == d2.accepts("".join(t))
        return
    assert d1.accepts(w) != d2.accepts(w)
    for n in range(len(w)):
        for t in itertools.product(sigma, repeat=n):
            s = "".join(t)
            assert d1.accepts(s) == d2.accepts(s)


@pytest.mark.parametrize("mode", ["intersection", "union", "difference", "symmetric_difference"])
def test_product_construction(mode):
    """Product DFA accepts exactly the boolean combination of the two languages."""
    d1, d2 = regex_to_dfa("(a|b)*a"), regex_to_dfa("[ab]{0,3}c?")
    p = product(d1, d2, mode)
    ops = {
        "intersection": lambda x, y: x and y,
        "union": lambda x, y: x or y,
        "difference": lambda x, y: x and not y,
        "symmetric_difference": lambda x, y: x != y,
    }
    for n in range(6):
        for t in itertools.product("abcz", repeat=n):
            s = "".join(t)
            assert p.accepts(s) == ops[mode](d1.accepts(s), d2.accepts(s))


# ---------------------------------------------------------------- parser


@pytest.mark.parametrize("bad", ["(a", "a)", "*a", "a|*", "[a-", "[]", "[z-a]", "a{3,1}", "a{x}", "a.b", "\\", "[^a]"])
def test_parser_rejects(bad):
    """Malformed or unsupported syntax raises RegexSyntaxError."""
    with pytest.raises(rp.RegexSyntaxError):
        rp.parse(bad)


def test_desugaring_uses_only_core_operators():
    """After parsing, +, ? and {n,m} are gone: only the 6 core node types remain."""
    core = (rp.Epsilon, rp.Literal, rp.CharClass, rp.Concat, rp.Union, rp.Star)

    def walk(n):
        assert isinstance(n, core)
        for child in (getattr(n, "left", None), getattr(n, "right", None), getattr(n, "child", None)):
            if child is not None:
                walk(child)

    walk(rp.parse("a+b?(cd){2,4}[x-z]{1,}"))
    assert rp.parse("a+") == rp.Concat(rp.Literal("a"), rp.Star(rp.Literal("a")))
    assert rp.parse("a?") == rp.Union(rp.Literal("a"), rp.Epsilon())


# ---------------------------------------------------------------- random regexes


def regex_strategy():
    """Random regexes over {a,b,c} built from the supported syntax."""
    leaf = st.sampled_from(["a", "b", "c", "[ab]", "[a-c]"])

    def extend(inner):
        return st.one_of(
            st.tuples(inner, inner).map(lambda t: t[0] + t[1]),
            st.tuples(inner, inner).map(lambda t: f"({t[0]}|{t[1]})"),
            inner.map(lambda r: f"({r})*"),
            inner.map(lambda r: f"({r})+"),
            inner.map(lambda r: f"({r})?"),
            st.tuples(inner, st.integers(0, 2), st.integers(0, 2)).map(lambda t: f"({t[0]}){{{t[1]},{t[1] + t[2]}}}"),
        )

    return st.recursive(leaf, extend, max_leaves=6)


@settings(max_examples=300, deadline=None)
@given(pattern=regex_strategy(), strings=st.lists(st.text(alphabet="abcd", max_size=8), min_size=10, max_size=10))
def test_random_regexes_match_re(pattern, strings):
    """Random regex + random strings: minimal DFA agrees with re.fullmatch."""
    d = regex_to_dfa(pattern)
    for s in strings:
        assert d.accepts(s) == (re.fullmatch(pattern, s) is not None), (pattern, s)
