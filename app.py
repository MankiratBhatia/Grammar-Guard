"""GrammarGuard Streamlit UI.  Run with:  streamlit run app.py"""

import streamlit as st

from grammarguard.core.dfa import equivalent, minimize, regex_to_dfa, subset_construction
from grammarguard.core.dot import dfa_to_dot, nfa_to_dot
from grammarguard.core.nfa import regex_to_nfa
from grammarguard.core.regex_parser import RegexSyntaxError, parse, to_string

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


JSON_PROMPT = '{"name":"alice","age":30,"tags":["a","b"]}\n{"name":"bob","age":25,"tags":["c"]}\n'


def run_json_generation(prompt: str, max_tokens: int, top_k: int, seed: int) -> None:
    """Stream unconstrained vs PDA-constrained JSON side by side, showing the PDA stack per step."""
    from grammarguard.core.pda import JSON_PDA
    from grammarguard.decoding.generate import generate_unconstrained
    from grammarguard.decoding.pda_generate import generate_json

    opts = dict(max_tokens=max_tokens, top_k=top_k if top_k > 1 else None, seed=seed)
    left, right = st.columns(2)
    left.subheader("Unconstrained")
    right.subheader("Constrained (PDA)")
    lt, rt, lstat, rstat, rtable = left.empty(), right.empty(), left.empty(), right.empty(), right.empty()
    text, gen = "", generate_unconstrained(prompt, **opts)
    while True:
        try:
            step = next(gen)
        except StopIteration as stop:
            reason = stop.value
            break
        text += step.token_text
        lt.code(text or " ", language=None)
    lstat.write(f"stopped: {reason} · valid JSON in our subset: **{JSON_PDA.accepts(text)}**")

    rows, text, gen = [], "", generate_json(prompt, **opts)
    while True:
        try:
            step = next(gen)
        except StopIteration as stop:
            reason = stop.value
            break
        text += step.token_text
        rows.append(
            {
                "token": repr(step.token_text),
                "stack (top first)": " ".join(step.stack) or "(empty)",
                "valid / checked": f"{step.num_valid} / {step.num_checked}",
                "full-vocab fallback": step.fallback,
            }
        )
        rt.code(text, language=None)
        rstat.write(
            f"stack: `{' '.join(step.stack) or '(empty)'}` · valid tokens: **{step.num_valid}** "
            f"of {step.num_checked} checked"
        )
        rtable.dataframe(rows, use_container_width=True)
    rstat.write(f"stopped: {reason} · valid JSON in our subset: **{JSON_PDA.accepts(text)}**")


# ------------------------------------------------------------------ tab 1
with tab_pipeline:
    regex = st.text_input(
        "Regex", "(a|b)*abb", key="pipe_regex", help="Supports | * + ? ( ) [a-z] {n} {n,m}; escape specials with \\"
    )
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
        st.caption(
            "DFA counts include the dead state ∅, which is hidden in the drawings: "
            "every missing edge goes to it. Dashed nodes are non-live."
        )
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
    mode = gc1.radio("Constraint", ["Regex (DFA)", "JSON (PDA)"], horizontal=True, key="gen_mode")
    is_json = mode.startswith("JSON")
    prompt = gc1.text_area(
        "Prompt",
        JSON_PROMPT if is_json else "Date: ",
        key="gen_prompt_json" if is_json else "gen_prompt",
        height=100 if is_json else 68,
    )
    gen_regex = "" if is_json else gc1.text_input("Regex", "[0-9]{4}-[0-9]{2}-[0-9]{2}", key="gen_regex")
    max_tokens = gc2.slider("Max tokens", 5, 60, 30)
    top_k = gc2.slider("Top-k (1 = greedy)", 1, 50, 1)
    seed = gc2.number_input("Seed", 0, 10_000, 0)
    if st.button("Generate", type="primary"):
        gdfa = None if is_json else build_or_error(gen_regex)
        if is_json:
            run_json_generation(prompt, max_tokens, top_k, int(seed))
        elif gdfa is not None:
            from grammarguard.decoding.generate import generate_constrained, generate_unconstrained

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
                rows.append(
                    {
                        "token": repr(step.token_text),
                        "DFA state": step.dfa_state,
                        "allowed tokens": step.num_allowed,
                        "accepting": step.dfa_state in gdfa.accept,
                    }
                )
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
