"""GrammarGuard Streamlit UI.  Run with:  streamlit run app.py"""

import streamlit as st

from core.dfa import equivalent, minimize, regex_to_dfa, subset_construction
from core.dot import dfa_to_dot, nfa_to_dot
from core.nfa import regex_to_nfa
from core.regex_parser import RegexSyntaxError, parse, to_string

st.set_page_config(page_title="GrammarGuard", layout="wide")
st.title("GrammarGuard")
st.caption("Regex → ε-NFA → DFA → minimal DFA, used to constrain GPT-2 so its output always matches the regex.")

tab_pipeline, tab_generate, tab_equiv = st.tabs(["Pipeline", "Generate", "Equivalence"])


def build_or_error(regex: str):
    """Compile a regex to a minimal DFA, or show the syntax error and return None."""
    try:
        return regex_to_dfa(regex)
    except RegexSyntaxError as e:
        st.error(f"Invalid regex: {e}")
        return None


# ------------------------------------------------------------------ tab 1
with tab_pipeline:
    regex = st.text_input("Regex", "(a|b)*abb", key="pipe_regex",
                          help="Supports | * + ? ( ) [a-z] {n} {n,m}; escape specials with \\")
    try:
        ast = parse(regex)
        nfa = regex_to_nfa(regex)
        dfa = subset_construction(nfa)
        mdfa = minimize(dfa)
    except RegexSyntaxError as e:
        st.error(f"Invalid regex: {e}")
    else:
        st.code(f"desugared AST: {to_string(ast)}", language=None)
        c1, c2, c3 = st.columns(3)
        c1.metric("ε-NFA states", len(nfa.states))
        c2.metric("DFA states (subset construction)", dfa.num_states())
        c3.metric("Minimal DFA states", mdfa.num_states())
        st.caption("DFA counts include the dead state ∅, which is hidden in the drawings: "
                   "every missing edge goes to it. Dashed nodes are non-live.")
        st.subheader("ε-NFA (Thompson)")
        st.graphviz_chart(nfa_to_dot(nfa), use_container_width=True)
        st.subheader("DFA (subset construction; nodes show the NFA state sets)")
        st.graphviz_chart(dfa_to_dot(dfa, use_labels=True), use_container_width=True)
        st.subheader("Minimal DFA (table-filling; nodes show the merged DFA states)")
        st.graphviz_chart(dfa_to_dot(mdfa, use_labels=True), use_container_width=True)
        test = st.text_input("Test a string", "aabb", key="pipe_test")
        st.write(f"NFA: **{nfa.accepts(test)}**, DFA: **{dfa.accepts(test)}**, minimal DFA: **{mdfa.accepts(test)}**")

# ------------------------------------------------------------------ tab 2
with tab_generate:
    gc1, gc2 = st.columns([2, 1])
    prompt = gc1.text_input("Prompt", "Date: ", key="gen_prompt")
    gen_regex = gc1.text_input("Regex", "[0-9]{4}-[0-9]{2}-[0-9]{2}", key="gen_regex")
    max_tokens = gc2.slider("Max tokens", 5, 60, 30)
    top_k = gc2.slider("Top-k (1 = greedy)", 1, 50, 1)
    seed = gc2.number_input("Seed", 0, 10_000, 0)
    if st.button("Generate", type="primary"):
        gdfa = build_or_error(gen_regex)
        if gdfa is not None:
            from decoding.generate import generate_constrained, generate_unconstrained

            opts = dict(max_tokens=max_tokens, top_k=top_k if top_k > 1 else None, seed=int(seed))
            left, right = st.columns(2)
            left.subheader("Unconstrained")
            right.subheader("Constrained")
            lt, rt = left.empty(), right.empty()
            lstat, rstat = left.empty(), right.empty()
            rtable = right.empty()

            text, gen = "", generate_unconstrained(prompt, **opts)
            while True:
                try:
                    step = next(gen)
                except StopIteration as stop:
                    reason = stop.value
                    break
                text += step.token_text
                lt.code(text or " ", language=None)
            lstat.write(f"stopped: {reason} · matches regex: **{gdfa.accepts(text)}**")

            with st.spinner("Building token index (cached afterwards)…"):
                gen = generate_constrained(gdfa, prompt, **opts)
                first = next(gen, None)
            rows, text = [], ""
            while first is not None:
                step = first
                text += step.token_text
                rows.append({"token": repr(step.token_text), "DFA state": step.dfa_state,
                             "allowed tokens": step.num_allowed, "accepting": step.dfa_state in gdfa.accept})
                rt.code(text, language=None)
                rstat.write(f"state **q{step.dfa_state}** · allowed tokens: **{step.num_allowed}**")
                rtable.dataframe(rows, use_container_width=True)
                try:
                    first = next(gen)
                except StopIteration as stop:
                    reason = stop.value
                    break
            else:
                reason = "dead_end"
            rstat.write(f"stopped: {reason} · matches regex: **{gdfa.accepts(text)}**")
            with right.expander("Minimal DFA (final state highlighted)"):
                st.graphviz_chart(dfa_to_dot(gdfa, highlight=gdfa.run(text)), use_container_width=True)

# ------------------------------------------------------------------ tab 3
with tab_equiv:
    e1, e2 = st.columns(2)
    r1 = e1.text_input("Regex 1", "(a|b)*", key="eq1")
    r2 = e2.text_input("Regex 2", "(a*b*)*", key="eq2")
    d1, d2 = build_or_error(r1), build_or_error(r2)
    if d1 is not None and d2 is not None:
        same, witness = equivalent(d1, d2)
        st.caption(f"Minimal DFAs: {d1.num_states()} and {d2.num_states()} states")
        if same:
            st.success("Equivalent: both regexes define the same language.")
        else:
            shown = "ε (the empty string)" if witness == "" else repr(witness)
            st.error(f"Not equivalent. Shortest counterexample: {shown}")
            st.write(f"Regex 1 accepts it: **{d1.accepts(witness)}** · Regex 2 accepts it: **{d2.accepts(witness)}**")
