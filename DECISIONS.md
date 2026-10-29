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

## 2.0 Phase C — decoding engine

1. **Trie index, measured** (GPT-2 vocab: 49 912 tokens → 97 317 trie nodes, built once in 0.33 s).
   Output is identical to the per-token reference loop (`build_index_loop`, kept as the oracle) on every
   constraint tested, but the speed-up depends on how restrictive the constraint is:

   | constraint | live states | loop | trie | speed-up |
   |---|---|---|---|---|
   | ISO date | 11 | 0.48 s | 0.00 s | 114× |
   | integer | 4 | 0.15 s | 0.00 s | 37× |
   | email | 10 | 0.48 s | 0.08 s | 5.8× |
   | username | 17 | 0.70 s | 0.18 s | 3.8× |
   | `!contains(/spam/)` | 4 | 0.17 s | 0.29 s | **0.6× (slower)** |
   | email & !contains & length | 1265 | 50 s | 18.6 s | 2.7× |

   Pruning only helps when most tokens die early. A complement-style constraint allows almost every token,
   so nothing is pruned and the trie's bookkeeping makes it slower. We keep the trie as the default anyway
   (restrictive constraints are the common case) and report the exception rather than hide it.
2. **Lazy index for big DFAs.** The 1265-state policy took 18.6 s to index eagerly, but generation visits
   only a few dozen states. DFAs with more than 64 live states get a `LazyTokenIndex` that walks the trie
   for a state only when generation first reaches it (a 20-token run took 1.3 s in total). Small DFAs keep
   the eager index and the disk cache. Lazy rows live only in the Engine's memory LRU.
3. **Constraint pressure.** Per step, with p the raw model distribution and A the allowed set:
   `mass_kept = p(A)`, `pressure = 1 − p(A)`, `cost_bits = −log2 p(A)`. The cost is exactly
   KL(q‖p) in bits for q = p restricted to A and renormalised, i.e. the information the constraint adds.
   Measured: a prompt that already shows dates costs 7.7 bits and 1 override; "My favourite animal is the"
   costs 63.6 bits and 7 overrides. Computed in log space (`logsumexp`) so tiny masses do not underflow.
   The EOS decision that ends a run is not included in the totals.
4. **PDA telemetry is a lower bound.** After a top-100 check, valid tokens outside the top 100 are not
   known, so `mass_kept` counts only the checked ones (exact after a fallback). `preferred_legal` is
   always exact because the model's argmax is always among the checked tokens.
5. **PDA fallback is a trie walk** (`valid_tokens`) instead of running the PDA over every token string.
   Measured 2–118× faster, but the old scan was already only 20–40 ms, so this is a tidy-up, not a rescue.
6. **Certificates are stdlib-only** (`core/certificate.py`) and verified without the model: the verifier
   rebuilds the automaton from the constraint text, checks a SHA-256 fingerprint, and replays the run.
   "valid" (the certificate is truthful) is deliberately separate from "accepted" (the text is in the
   language): a certificate can honestly prove that a text is *not* accepted. The DFA fingerprint is the
   canonical minimal-DFA table, so it is only meaningful for minimal DFAs (the algebra always minimizes).
7. **Engine**: one lock, so generations run one at a time (CPU GPT-2 is not safe to drive from several
   threads). `CancelToken` is polled between tokens; closing a half-consumed generator releases the lock.
8. **Schema mode** needed no new generator: `generate_json(pda=PDA(schema_to_grammar(schema)))`.
9. **Not done in this phase:** the lazy index is not persisted to disk.

## 2.0 Phase D — FastAPI backend

1. **16 endpoints under `/api/v1`**, documented in the generated OpenAPI (`/docs`): compile, membership,
   certificate/verify, builtins, algebra, equivalence, count, validators/{diff,testgen}, learn/{subset,minimize},
   generate, generate/json, generate/batch, system/{health,models}. Settings come from `GRAMMARGUARD_*`
   environment variables (`api/settings.py`).
2. **One error envelope** `{"error": {code, message, position, details}}` for every failure. Codes:
   `regex_syntax`, `algebra`, `schema`, `limit_exceeded`, `not_expressible`, `validation`, `internal`.
   User mistakes are 422 with a character position where one exists; only genuine bugs are 500.
3. **Counts of strings are JSON strings**, never numbers: a policy language had 3.6 × 10^45 strings and
   JavaScript numbers lose precision above 2^53.
4. **Streaming = Server-Sent Events from a worker thread.** The model is blocking CPU code, so each
   request runs in its own thread feeding an asyncio queue. If the client disconnects, the response is
   cancelled, a `CancelToken` is set, and the worker stops at the next token and releases the model lock.
   A timer stops runs longer than `generation_timeout` and reports `stop_reason: "timeout"`. Request
   validation (bad regex, limits) happens BEFORE the stream opens, so errors are normal HTTP 422s.
5. **Limits protect a shared server**: regex/expression/text/prompt lengths, `max_states`, `max_tokens`,
   batch size, count lengths, and a cap on unminimized DFA size. The cap needed new core support:
   `subset_construction(max_states=)` and `product(limit=)` raise `StateLimitError`.
   `(a|b)*a(a|b){20}` (needs 2^21 states) is refused in 0.03 s instead of hanging.
6. **Never ask for a regex from a big automaton.** `/algebra` converts the result back to a regex (state
   elimination) only for automata of at most 40 states, and elimination itself has a size budget.
   Trying it on a 649-state policy took 13.7 s even to give up.
7. **Bug found and fixed during this phase: `shortest_strings` could exhaust memory.** It was a BFS over
   all viable prefixes; for a dense language whose shortest match has length 8 over a 40-symbol alphabet
   that is about 40^7 queue entries. It crashed the WSL VM several times (the API's example-string
   feature exposed it; the earlier tests only used sparse languages). It now uses a feasibility table
   (`can[r]` = states that reach acceptance in exactly r more symbols) plus depth-first search that only
   follows guaranteed branches: 4.5 ms on the same input. Regression tests include a brute-force
   comparison of the output order and a dense-language timing test.
8. **Tests use a `FakeEngine`** (same interface as `Engine`, no model) so the SSE protocol, queueing,
   timeout, disconnect and error paths are fast and deterministic. Two tests drive the real GPT-2.
   A review of my own tests found two weak ones (a disconnect test that passed without generating
   anything because the fake language was too long for the example finder); both were tightened.
9. **Certificates are self-contained**: the stored constraint is an algebra expression (a plain regex is
   wrapped as `/.../`) plus any user-defined `names`, so `/certificate/verify` needs only the certificate.
10. **Not done yet:** the React frontend (Phase E); `python -m grammarguard.api` serves the API and will
    also serve `frontend/dist` once it exists.

## 2.0 Phase E — React frontend

1. **Stack:** React 18 + TypeScript (strict) + Vite + Tailwind 4, in `frontend/`. Node 20 was installed
   user-level from the official tarball (SHA-256 verified), not through a `curl | bash` installer.
   One process in production: `npm run build` writes `frontend/dist`, which FastAPI serves at `/`
   (API routes win; a test checks the static mount does not shadow `/api` or `/docs`). In development the
   Vite server proxies `/api` to the backend.
2. **Types come from the backend.** `npm run gen:api` dumps the live OpenAPI schema and generates
   `src/api/schema.d.ts`, so a renamed field breaks the frontend build instead of production. The SSE event
   payloads are not in OpenAPI and are typed by hand in `api/types.ts`.
3. **npm workaround:** npm 10.8's resolver crashes (`edgesOut`, in `#loadPeerSet`) on this dependency set, so
   `frontend/.npmrc` sets `legacy-peer-deps=true`. Consequence: peer dependencies are not installed
   automatically, so `@testing-library/dom` is listed explicitly and peer ranges were checked by hand
   (vite 8 satisfies every plugin). TypeScript is pinned to 5.x because `openapi-typescript` needs the compiler's
   JavaScript API, which TypeScript 7 no longer ships.
4. **Streaming:** `EventSource` cannot POST, so `api/sse.ts` reads `fetch` response streams with its own SSE parser
   (handles CRLF, keep-alive comments, events split across chunks; unit-tested). Aborting the request is what
   triggers the server-side cancellation.
5. **Graphs:** our DOT text is rendered in the browser with Graphviz compiled to WebAssembly (`@viz-js/viz`),
   loaded lazily (a separate 1.4 MB chunk, fetched only when the first graph is drawn). The white page
   background is removed and strokes use `currentColor` so graphs work in dark mode. Highlighting the current
   state is done by appending one node statement to the DOT, not by a second layout engine.
6. **Pages:** Studio, Generate, Constraint Algebra, Validator Lab, JSON & Schema, Learn. The **Benchmarks**
   page from the plan moves to Phase F, where the evaluation results it displays are produced.
7. **Theme:** CSS variables for light and dark (system preference by default, overridable in the sidebar);
   charts and graphs use the same tokens. Layout collapses to a top bar below the `md` breakpoint.
8. **Verified in a real browser** (headless Windows Chrome via WSL), not only in jsdom: all six pages were
   screenshotted. Interactive states used a throwaway same-origin harness that replays *recorded real server
   streams* (captured with curl) because headless Chrome's virtual clock outruns real model time. Findings
   fixed from those screenshots: a misleading "table below" message when a graph is too large, count columns
   overflowing the chart, double brackets in the parsed-AST line (`((a|b))*`), and `[0-9]` printing as
   `[0123456789]` (the range printer was moved from `elimination.py` into `regex_parser.class_text`).
9. **Tests:** 31 Vitest tests (SSE parser, API client errors, helpers, and every page against a mocked backend,
   including error paths, stream events, the PDA stack view and the step players).
10. **Not done:** the Playwright suite from the plan (browsers cannot be installed without sudo here). The
    real-browser checks above were manual screenshots, not an automated end-to-end test.

## 2.0 Phase F — evaluation, packaging, documentation

1. **Streamlit prototype retired** (`app.py` and its tests removed, `ui` extra dropped) once the React app reached parity. The two
   DOT-generation tests that were not Streamlit-specific moved to `tests/test_dot.py`.
2. **Benchmark design (`grammarguard/eval/`)**: 18 validators in six categories (dates, phones, ids, emails, numbers, policies), each
   with a bare prompt and a few-shot prompt, run greedy and sampled (top-20, two seeds). Three methods are compared: plain GPT-2,
   **best-of-5 rejection sampling** (the usual workaround), and the constrained decoder. The unconstrained baseline is judged just as
   strictly (the *whole* output must match, because a validator rejects text that merely starts correctly); a second figure reports
   whether any valid *prefix* exists, which is what regex post-processing could rescue. This avoids comparing against a strawman.
3. **Statistics**: 95% Wilson intervals (valid near 0% and 100%, where the usual normal approximation fails). The endpoints are made exact at
   0/n and n/n (floating-point fuzz gave 0.99999999 for 100/100, which a test caught).
4. **Independent judge**: every regex output is also checked with Python's `re.fullmatch`; the benchmark records whether it agrees with
   our DFA (it must, and the Benchmarks page shows it).
5. **Timing** is wall-clock on CPU with the server stopped (so it is not competing for cores) and excludes the one-off token-index
   build, which is reported separately per validator. Results, including every generated text, are stored in
   `eval/results/latest.json` and served at `GET /api/v1/benchmarks`; the Benchmarks page renders them with a run explorer.
6. **Docker**: multi-stage (Node builds the frontend; Python 3.11-slim with CPU-only torch runs it), GPT-2 baked into the image so it starts
   offline, unprivileged user, health check, volume for the token-index cache. **Not built or run** in the development environment
   (Docker is not installed there): the Dockerfile and compose file were written, the compose file parses and every `COPY` source exists.
7. **Documentation**: README (quick start, results, syllabus mapping), `docs/THEORY.md` (every algorithm with its guarantee and
   complexity), `docs/API.md` (reference with examples, verified against a running server), `docs/VIVA.md` (35 likely questions).
