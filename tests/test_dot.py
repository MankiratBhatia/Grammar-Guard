"""Graphviz DOT generation (used by the API to draw automata)."""

from grammarguard.core.dfa import minimize, subset_construction
from grammarguard.core.dot import compress_symbols, dfa_to_dot, nfa_to_dot
from grammarguard.core.nfa import regex_to_nfa


def test_compress_symbols():
    """Runs of consecutive symbols collapse into ranges."""
    assert compress_symbols("0123456789") == "0-9"
    assert compress_symbols("ab") == "a,b"
    assert compress_symbols("abcx") == "a-c,x"
    assert compress_symbols(" ") == "␣"


def test_dot_structure():
    """DOT output is well formed: balanced braces, one node line per drawn state, accept = doublecircle."""
    nfa = regex_to_nfa("(a|b)*abb")
    dot = nfa_to_dot(nfa)
    assert dot.startswith("digraph") and dot.rstrip().endswith("}")
    assert dot.count("doublecircle") == 1 and "ε" in dot
    m = minimize(subset_construction(nfa))
    dot = dfa_to_dot(m)
    assert dot.count("[label=") == (m.num_states() - 1) + dot.count("->") - 1  # nodes + non-start edges
    assert f"q{m.dead}" not in dot and f"q{m.dead}" in dfa_to_dot(m, show_dead=True)
    assert "#ffd966" in dfa_to_dot(m, highlight=m.start)
