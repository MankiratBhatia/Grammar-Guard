"""Phase 4 tests: CFG (FIRST/FOLLOW/LL(1)), the PDA, and PDA-constrained generation.

`json` is used only as a reference oracle for the JSON subset.
"""

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from grammarguard.core.cfg import EOF, Grammar, GrammarError, Term, char_range, json_grammar, lit
from grammarguard.core.pda import JSON_PDA, PDA, format_stack
from grammarguard.decoding.generate import collect
from grammarguard.decoding.pda_generate import generate_json

# ---------------------------------------------------------------- grammar theory


def textbook_grammar() -> Grammar:
    """E -> T E'; E' -> + T E' | eps; T -> F T'; T' -> * F T' | eps; F -> ( E ) | id(a)."""
    return Grammar(
        "E",
        {
            "E": [["T", "E1"]],
            "E1": [[lit("+"), "T", "E1"], []],
            "T": [["F", "T1"]],
            "T1": [[lit("*"), "F", "T1"], []],
            "F": [[lit("("), "E", lit(")")], [lit("a")]],
        },
    )


def test_first_follow_textbook():
    """FIRST/FOLLOW/nullable on the classic expression grammar."""
    g = textbook_grammar()
    assert g.first["E"] == g.first["T"] == g.first["F"] == {"(", "a"}
    assert g.first["E1"] == {"+"} and g.first["T1"] == {"*"}
    assert g.nullable == {"E1", "T1"}
    assert g.follow["E"] == g.follow["E1"] == {")", EOF}
    assert g.follow["T"] == g.follow["T1"] == {"+", ")", EOF}
    assert g.follow["F"] == {"*", "+", ")", EOF}


def test_conflict_detected():
    """A -> a b | a c is not LL(1): both productions start with 'a'."""
    g = Grammar("A", {"A": [[lit("a"), lit("b")], [lit("a"), lit("c")]]})
    with pytest.raises(GrammarError, match="not LL"):
        g.parse_table()


def test_undefined_nonterminal_rejected():
    """Referencing a nonterminal with no productions is an error."""
    with pytest.raises(GrammarError):
        Grammar("S", {"S": [["X"]]})


def test_json_grammar_is_ll1_and_productive():
    """No table conflicts, and every nonterminal can derive a terminal string."""
    g = json_grammar()
    table = g.parse_table()
    assert table[("Value", "7")] == (char_range("1", "9"), "Digits") and ("Value", "}") not in table
    productive, changed = set(), True
    while changed:
        changed = False
        for a, alts in g.productions.items():
            if a not in productive and any(all(isinstance(s, Term) or s in productive for s in rhs) for rhs in alts):
                productive.add(a)
                changed = True
    assert productive == set(g.productions)


def test_textbook_pda_accepts_expressions():
    """The PDA machinery works for any LL(1) grammar, not just JSON."""
    pda = PDA(textbook_grammar())
    for good in ["a", "a+a", "a*(a+a)", "((a))"]:
        assert pda.accepts(good)
    for bad in ["", "a+", "(a", "a)", "aa", "+a"]:
        assert not pda.accepts(bad)


# ---------------------------------------------------------------- PDA vs json


@pytest.mark.parametrize(
    "good", ["{}", "[]", '{"a":1}', '[0,10,"x",true,false,null]', '{"ab":[1,[2,[]],{"c":{}}],"d":"e"}', '[""]']
)
def test_pda_accepts_valid_json(good):
    """Valid subset documents are accepted (and json agrees)."""
    json.loads(good)
    assert JSON_PDA.accepts(good)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "1",
        '"a"',
        "{",
        "[1,]",
        "[01]",
        '{"A":1}',
        '{"a" :1}',
        "[1 ]",
        "{a:1}",
        "[1,2]x",
        '{"a":-1}',
        "[1.5]",
        "['a']",
        "[true,nul]",
        '{"a":1,}',
    ],
)
def test_pda_rejects_invalid(bad):
    """Invalid or out-of-subset documents are rejected."""
    assert not JSON_PDA.accepts(bad)


json_values = st.recursive(
    st.one_of(st.integers(0, 10**6), st.booleans(), st.none(), st.text("abcxyz", max_size=5)),
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text("abcxyz", max_size=4), inner, max_size=3)
    ),
    max_leaves=12,
)


@settings(max_examples=300, deadline=None)
@given(value=json_values.filter(lambda v: isinstance(v, (list, dict))))
def test_pda_accepts_everything_json_dumps_in_subset(value):
    """Any in-subset value serialised compactly by json.dumps is accepted, and every prefix is viable."""
    text = json.dumps(value, separators=(",", ":"))
    assert JSON_PDA.accepts(text)
    for i in range(len(text)):
        assert JSON_PDA.is_viable_prefix(text[:i])


@settings(max_examples=400, deadline=None)
@given(text=st.text(alphabet='{}[]",:0123456789abtrulefsn ', max_size=14))
def test_pda_acceptance_implies_json_loads(text):
    """Soundness: whatever the PDA accepts, json.loads parses (as a dict/list)."""
    if JSON_PDA.accepts(text):
        assert isinstance(json.loads(text), (dict, list))


def test_stack_contents():
    """After '{"a":[1,' the PDA is waiting for a value, then the rest of the array and object."""
    stack = JSON_PDA.run(JSON_PDA.initial(), '{"a":[1,')
    assert format_stack(stack) == ("Value", "Vals", "]", "Pairs", "}")
    assert JSON_PDA.run(JSON_PDA.initial(), "{}") == ()


# ---------------------------------------------------------------- generation


FEW_SHOT = '{"name":"alice","age":30}\n{"name":"bob","age":25}\n'


@pytest.mark.slow
@pytest.mark.parametrize("opts", [dict(), dict(top_k=10, seed=1), dict(top_k=10, seed=2), dict(top_k=30, seed=3)])
def test_generated_json_is_accepted(opts):
    """Every output that ends with EOS is a complete document; a cut-off one is a viable prefix."""
    text, steps, why = collect(generate_json(FEW_SHOT, max_tokens=60, **opts))
    assert why in ("eos", "max_tokens")
    assert JSON_PDA.is_viable_prefix(text)
    if why == "eos":
        assert JSON_PDA.accepts(text) and steps[-1].stack == ()
    assert all(s.num_valid >= 1 for s in steps)


@pytest.mark.slow
def test_fallback_to_full_vocab():
    """With top_n=1 the best token is usually invalid, so the full-vocabulary fallback must kick in
    and still produce valid output."""
    text, steps, why = collect(generate_json(FEW_SHOT, max_tokens=60, top_n=1))
    assert any(s.fallback for s in steps)
    assert all(s.num_checked > 1 for s in steps if s.fallback)
    assert JSON_PDA.is_viable_prefix(text)


@pytest.mark.slow
def test_greedy_top100_equals_greedy_full_vocab():
    """Greedy over the top-100 equals greedy over the full vocabulary (top_n = whole vocab)."""
    a, _, _ = collect(generate_json(FEW_SHOT, max_tokens=40))
    b, _, _ = collect(generate_json(FEW_SHOT, max_tokens=40, top_n=50257))
    assert a == b
