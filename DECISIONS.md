# Design decisions

Ambiguities in the spec and the (simplest) choice taken for each.

## Phase 1 — regular core

1. **Alphabet and the dead state.** Sigma is the set of symbols that occur in the
   regex. Every other character (an LLM can emit anything) is treated as one
   extra symbol "other" that sends every state to the dead state. Those edges
   are not stored; `DFA.step()` applies the rule. So the DFA is complete over
   all characters and the dead state (the empty subset ∅) is always present and
   counted in state totals, e.g. `(a|b)*abb` minimizes to 4 + 1 = 5 states.
2. **`.`, `^`, `$` and `[^...]` are rejected**, not silently reinterpreted.
   `.` and negated classes would need an "any character" symbol; anchors are
   pointless because we always match the whole string (like `re.fullmatch`).
   Write `\.` for a literal dot.
3. **Escapes:** `\x` always means the literal character x. No `\d`, `\w`, `\n`
   shorthands — use `[0-9]` etc. This keeps the tokenizer trivial.
4. **`{` always starts a repetition.** A malformed `{...}` is an error (Python's
   `re` would treat it as literal text).
5. **Character classes stay as one AST leaf** (`CharClass`) instead of being
   desugared into a chain of unions. Thompson gives them one edge per symbol
   between two states, which is the same automaton a union of literals reduces
   to after ε-removal, but it keeps the drawn NFA readable.
6. **Minimized DFAs are renumbered in BFS order** from the start state
   (symbols in sorted order). Since the minimal DFA is unique up to
   isomorphism, this makes `DFA.signature()` a canonical key per language;
   Phase 2 hashes it for the token-index cache.
7. **Shortest counterexample** = first accepting state found by BFS on the
   symmetric-difference product, so it is shortest, and alphabetically first
   among the shortest ones.
8. **Repeated postfix operators** such as `a**` or `a{2}*` are accepted and
   nest naturally (Python's `re` rejects them). The tests avoid them.

## Phase 2 — constrained decoding

1. **Exact token text** comes from inverting GPT-2's byte→unicode map and
   decoding UTF-8 strictly. Tokens that are only a fragment of a multi-byte
   character, and special tokens, map to `None` and are never allowed (the
   regex alphabets are plain text). EOS is handled separately.
2. **EOS is allowed only in accepting states**; a state's `num_allowed`
   counts EOS when it is legal.
3. **Dead end** = current state has zero allowed tokens and is not accepting.
   With GPT-2 this cannot happen for ASCII regexes (every byte is a token),
   so it is tested with an artificially emptied index.
4. **Stop reasons** (`eos`, `dead_end`, `max_tokens`) are the generators'
   return value; `collect()` extracts it. If `max_tokens` is hit, the output
   is a live prefix, not necessarily a full match — the CLI reports this.
5. **Empty prompt** is replaced by the EOS token (GPT-2's start-of-text).
6. **Sampling:** greedy by default; `--top-k K` samples from the K most likely
   *allowed* tokens (masking happens before top-k), seeded for reproducibility.
7. **Cache:** `.cache/token_index_<sha256(model + minimal-DFA signature)>.json`.
   Equivalent regexes share a cache entry because the minimal DFA is unique.
8. Only live states are indexed; non-live states are never entered.

## Phase 3 — Streamlit UI

1. **DOT is hand-written** in `core/dot.py` (stdlib only); `st.graphviz_chart`
   renders it in the browser, so no system Graphviz install is needed.
2. **The dead state is hidden in drawings** (with every edge into it); the text
   says missing edges go there. Non-live states are dashed. State counts still
   include it.
3. **Parallel edges are merged** into one label, runs compressed (`0-9`).
4. **Generate tab runs the two generators one after the other**, not in
   parallel threads: unconstrained streams into the left column, then
   constrained into the right, with the current DFA state, allowed-token
   count and a step table updating per token. Simplest thing that works in
   Streamlit's script model.
5. **UI is tested headlessly** with Streamlit's `AppTest`.

## Phase 4 — CFG, PDA, JSON

1. **Character-level grammar.** Terminals are single characters (or sets such
   as `[0-9]`), since the decoder feeds text character by character. So
   strings, ints and `true/false/null` are spelled out in the grammar and the
   whole thing stays LL(1) with a table keyed by (nonterminal, character).
2. **No whitespace** in the JSON subset (`{"a":1,"b":2}`, compact form). Real
   JSON allows spaces; allowing them would add optional-whitespace
   nonterminals everywhere. GPT-2 likes `", "`, so the PDA simply forces the
   compact style.
3. **Top level must be an object or array** (`Doc`), as in the original JSON
   RFC. Otherwise greedy decoding often stops after a bare `0`.
4. **Ints:** `0` or `[1-9][0-9]*` (no leading zeros). **Strings:** lowercase
   letters only, no escapes, as the spec said.
5. **One-state PDA, accept by empty stack.** "Expand" moves read no input and
   are chosen by looking at the next character; this look-ahead is what makes
   the PDA deterministic. At end of input only epsilon-expansions
   (`M[A, EOF]`) may remain.
6. **Stacks are immutable tuples**, so trying a candidate token never disturbs
   the real stack.
7. **Top-100 then fallback.** Greedy over the top-100 valid tokens equals
   greedy over the whole vocabulary (tested). With top-k sampling the top-100
   cut is an approximation. The fallback scans all 50 257 tokens only when
   none of the top 100 is valid.
8. **Stop reasons** as in Phase 2. `max_tokens` leaves a viable prefix; EOS is
   forced exactly when the stack is empty and nothing else is legal.
9. `json` (stdlib) is used only in tests, as an oracle, like `re` in Phase 1.

## 2.0 Phase A — restructure

1. Code lives in the `grammarguard/` package (`core`, `decoding`, later `api`, `eval`);
   `pyproject.toml` replaces `requirements.txt`, with extras `ml`, `api`, `ui`, `dev`.
   `grammarguard.core` still has **no** dependencies.
2. Token-index cache moved to `~/.cache/grammarguard` (override with
   `GRAMMARGUARD_CACHE_DIR`), since the package directory may be read-only once installed.
3. Tests that load GPT-2 are marked `slow`; `make test-fast` / CI run `-m "not slow"`.
4. `mypy` is enforced on `grammarguard.core` only (the theory code); the ML glue is
   checked by ruff.

## 2.0 Phase B — core theory extensions

1. **OTHER is a real symbol.** Complement needs it: after `!A`, a character outside Σ must be able to
   lead to an *accepting* state. `DFA.symbols()` = Σ + [OTHER]; `OTHER` is the empty string (never a
   real input character). The dead state is now `Optional` and detected as a non-accepting trap.
   Consequence: DFA state counts are unchanged for plain regexes (the dead state was already counted).
2. **Two minimization algorithms, same result.** Table filling (O(|Q|²·|Σ|) per round) is kept for
   teaching and for the step-by-step trace; Moore's partition refinement is used for DFAs over 64
   states (an `email & !contains(..) & length(..)` product has ~1300 states and took seconds with
   table filling, 0.4 s with partitioning). A test checks the two give identical canonical tables.
3. **Counting is over Σ only** (OTHER is infinitely many characters). `language_size` therefore means
   "strings made of the constraint's own symbols".
4. **State elimination** prints with simplifications (merge single-char alternatives into classes,
   `R R*` → `R+`, `X{n}` runs). Languages that accept characters outside Σ (e.g. complements) or
   the empty language cannot be written in our regex syntax (no `.` or ∅) and raise
   `NotExpressibleError`.
5. **Test generation has four kinds of cases**: transition coverage (positives), boundary,
   substitution and deletion negatives, truncations. Substitution/deletion were added after a
   mutation test showed boundary negatives alone miss validators that over-accept a separator or
   a too-short length. "Any other character" is infinite, so only a few likely offenders
   (space `_ - . @ #`) are tried; this limit is documented.
6. **Constraint algebra syntax**: regex literals are written `/…/`; `&`, `|`, `-`, `!`, parentheses,
   named constraints, `length`, `contains`, `starts`, `ends`, `concat`, `star`. A state cap
   (default 2000) stops runaway products with an error that carries a position.
7. **No separate `trace.py`**: `subset_construction(trace=[...])` and `minimize(trace=[...])` record
   their own steps, so the Learn page replays the real algorithms rather than a copy.
8. **Schema compiler** supports object (fixed key order, all required), array (minItems 0/1),
   string, non-negative integer, boolean, null and enum (compiled to a trie so it stays LL(1)).
   Building the array rule exposed a genuine LL(1) conflict (`[` `]` vs `[` item…), fixed by
   left-factoring; `schema_to_grammar` always runs `parse_table()` so a conflict can never slip through.
