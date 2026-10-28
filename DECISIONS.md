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
