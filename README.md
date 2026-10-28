# GrammarGuard

Constrained LLM decoding using automata implemented from scratch. Give it a regex
(or a JSON grammar) and GPT-2 can only produce text that matches it: at every step,
tokens that would make a match impossible are removed before the next token is chosen.

Theory of Computation course project. The core automata code (`grammarguard/core/`) uses only the
Python standard library: no automata or regex-engine libraries, and no `re` (it appears
only in tests, as a reference oracle, together with `json`).

## Setup

Python 3.11.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[ml,api,ui,dev]"       # or: make install
```

For a CPU-only torch (smaller download), install it first:
`pip install torch --index-url https://download.pytorch.org/whl/cpu`.
The first run downloads GPT-2 (~500 MB) from the Hugging Face hub.

## How to run

Command line (prints unconstrained vs constrained output and whether each matches):

```bash
python -m grammarguard.cli --regex "[0-9]{4}-[0-9]{2}-[0-9]{2}" --prompt "Date: " --verbose
```

Options: `--top-k K` (sample instead of greedy), `--seed`, `--max-tokens`, `--temperature`.

Web UI:

```bash
streamlit run app.py
```

- **Pipeline**: regex → ε-NFA → DFA → minimal DFA, drawn with state counts.
- **Generate**: unconstrained vs constrained side by side, streaming, with the DFA state
  and allowed-token count per step. A *JSON (PDA)* mode shows the PDA stack instead.
- **Equivalence**: two regexes → equivalent, or a shortest counterexample.

Tests:

```bash
pytest -q          # ~175 tests, ~40 s (the decoding tests run real GPT-2 on CPU)
```

## How it works

```
regex ──parse──> AST ──Thompson──> ε-NFA ──subset──> DFA ──minimize──> minimal DFA
                                                                          │
              GPT-2 vocabulary ── run DFA over each token's characters ───┤
                                                                          ▼
                         token index: allowed[q], next[q][t]  (cached on disk)
                                                                          │
                logits ──mask disallowed tokens (EOS only if accepting)──> pick ──> advance DFA
```

A language model emits multi-character *tokens*, while a DFA reads *characters*. For
each live DFA state `q` (one from which an accepting state is reachable) and each token
`t`, we run the DFA over `t`'s characters. If it ends in a live state, `t` is allowed in
`q`. Allowed tokens can never lead to a dead end, so if generation stops with EOS (which
is allowed only in accepting states) the output is a string of the language.

### File → syllabus topic

| File | Syllabus topic |
|---|---|
| `grammarguard/core/regex_parser.py` | Regular expressions: syntax and semantics; the three core operations (union, concatenation, star); `+`, `?`, `{n,m}` as syntactic sugar. Tokenizer + recursive-descent parser. |
| `grammarguard/core/nfa.py` | **NFA with ε-moves**; Thompson's construction (regex → ε-NFA); **ε-closure**; NFA simulation. |
| `grammarguard/core/dfa.py` `subset_construction` | **NFA → DFA by subset construction**: each DFA state is the ε-closure of a set of NFA states; only reachable subsets; explicit dead state. |
| `grammarguard/core/dfa.py` `DFA`, `accepts` | **DFA**: δ, extended transition function δ*, language acceptance. |
| `grammarguard/core/dfa.py` `minimize` | **DFA minimization** by table filling (Myhill–Nerode distinguishability). |
| `grammarguard/core/dfa.py` `live_states` | Reachability / **emptiness** reasoning: states that can still reach acceptance. |
| `grammarguard/core/dfa.py` `product` | **Closure properties**: intersection, union, difference, symmetric difference via the product construction. |
| `grammarguard/core/dfa.py` `equivalent`, `shortest_accepted` | **Equivalence** of regular languages: L₁ = L₂ iff L₁ △ L₂ is empty; BFS gives a shortest counterexample. |
| `grammarguard/core/dot.py` | Drawing automata (state diagrams as Graphviz DOT). |
| `grammarguard/decoding/token_index.py` | Applying a DFA to token strings; per-state allowed sets (DFA as a prefix-viability checker). |
| `grammarguard/decoding/generate.py` | Constrained generation loop driven by a DFA. |
| `grammarguard/core/cfg.py` | **Context-free grammars**; nullable, **FIRST/FOLLOW**; **LL(1)** parse table and conflict detection. |
| `grammarguard/core/pda.py` | **Deterministic pushdown automaton**: stack, expand/match moves, acceptance by empty stack. |
| `grammarguard/decoding/pda_generate.py` | PDA-constrained generation: the stack is unbounded, so tokens are checked at runtime (top-100, full-vocabulary fallback). |
| `tests/` | Differential testing against `re` and `json`; property-based tests (Hypothesis). |

Design decisions and simplifications are recorded in [DECISIONS.md](DECISIONS.md).

### Supported regex syntax

Literals, `|`, concatenation, `*`, `+`, `?`, `( )`, classes like `[0-9]` / `[a-z_]`,
`{n}`, `{n,m}`, `{n,}`, and `\x` to escape a special character. Not supported: `.`,
`^`, `$`, `[^…]`, and shorthands such as `\d` (see DECISIONS.md for why).

## Not built (future work)

- **Chomsky / Greibach normal forms** (CNF/GNF): the grammar is used as-is in LL(1) form.
- **CYK parsing**: only the deterministic LL(1) PDA is implemented, so only
  deterministic context-free languages are supported, not arbitrary CFGs.
- **Pumping lemma** tooling (for regular or context-free languages).
- **Linear bounded automata** and context-sensitive languages.
- **Post correspondence problem** and undecidability demonstrations.
- Not covered either: general nondeterministic PDAs, Turing machines, and
  whitespace/escapes/negative numbers in the JSON subset.
