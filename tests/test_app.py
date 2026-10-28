"""Phase 3 tests: DOT generation and the Streamlit app (headless via AppTest)."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

from core.dfa import minimize, regex_to_dfa, subset_construction
from core.dot import compress_symbols, dfa_to_dot, nfa_to_dot
from core.nfa import regex_to_nfa


APP = str(Path(__file__).resolve().parent.parent / "app.py")


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


def test_pipeline_and_equivalence_tabs():
    """Default inputs render graphs, counts and the equivalence verdict; bad regexes show an error."""
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert [m.value for m in at.metric] == ["14", "6", "5"]
    assert "Equivalent" in at.success[0].value
    at.text_input(key="eq2").set_value("(a|b)*a").run()
    assert "Shortest counterexample: ε" in at.error[0].value
    at.text_input(key="pipe_regex").set_value("(a").run()
    assert any("Invalid regex" in e.value for e in at.error)


def test_generate_tab():
    """Clicking Generate streams both columns and the constrained text matches the regex."""
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.text_input(key="gen_regex").set_value("EMP-[0-9]{3}").run()
    at.text_input(key="gen_prompt").set_value("ID: ").run()
    at.button[0].click().run()
    assert not at.exception
    texts = " ".join(m.value for m in at.markdown)
    assert "matches regex: **True**" in texts
