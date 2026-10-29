# GrammarGuard 2.0 — Implementation Plan

Status: **implemented** (kept as the original plan; the Streamlit prototype mentioned below has been replaced by the React frontend).
What was actually built, and every deviation from this plan with its reason, is recorded in `DECISIONS.md`; the Playwright suite and the Docker build verification are the two items that were not done.
The current project (phases 1–4, 174 tests, Streamlit UI) stays the foundation;
every new feature is either a new automata algorithm or a product feature built
directly on one, so all of it stays explainable in a ToC viva.

---

## 1. The positioning (what makes it unique)

**Tagline:** *Formal guarantees for LLM output, and formal QA for the validators behind them.*

Constrained decoding by itself is no longer new (Outlines, XGrammar, llama.cpp
grammars do it). Claiming novelty there would not survive a viva question. The
novel part is what we build *around* it, by turning textbook theorems into user-facing
features nobody bundles together:

| Theorem / algorithm (syllabus) | Becomes the product feature |
|---|---|
| Closure under ∩, ∪, complement, difference (product construction) | **Constraint Algebra**: compose policies such as `email & !contains(competitor) & len(5..40)` and generate text that satisfies all of them, *provably* |
| Equivalence + shortest counterexample | **Validator Diff**: "does my new form regex accept anything the old one rejected?", answered with witness strings before deploy |
| DFA path / run of the automaton | **Output certificates**: every generation ships with the state path that proves membership, re-checkable by anyone |
| DFA graph coverage, live/dead states | **Test-suite synthesis**: positive tests covering every transition, plus "boundary negatives" that die at each possible position |
| Counting paths in a DFA (dynamic programming) | **Language size and uniform sampling**: "how many valid IDs of length 8?", unbiased random samples |
| Kleene's theorem, DFA → regex (state elimination) | **Explain the result**: show the regex of any composed policy |
| Table filling / subset construction | **Learn mode**: step-by-step replay of the algorithms with the tables visible |
| LL(1) CFG + PDA | **Schema-guided JSON**: a mini JSON-schema compiles to an LL(1) grammar and a PDA, so outputs have the right keys and types, not just valid syntax |

**Use-case story for the demo and report:** a team has validation regexes
behind its forms (IDs, dates, phones, emails). GrammarGuard:
1. makes an LLM produce data that is guaranteed to pass those validators,
2. lets them add policies (format AND NOT forbidden pattern) with proofs,
3. diffs validator versions with counterexamples before release,
4. synthesises a test suite with formal coverage for the validators,
5. measures how hard the model had to be steered (telemetry below).

**Measurable research angle (novel metric): "constraint pressure".** At each
step we log how much probability mass the constraint removed, and whether the
model's own top choice was legal. Averaged over a run, this gives a number for
how much the grammar fought the model. We report it in an evaluation section:
validity rate, latency and pressure for unconstrained vs constrained, across a
benchmark of regexes and prompts. This gives the project results, not just a demo.

---

## 2. Target architecture

```
grammarguard/                     Python package (pip-installable, pyproject.toml)
  core/                           stdlib only, no re  (existing + extended)
    regex_parser.py  nfa.py  dfa.py  dot.py  cfg.py  pda.py      (existing)
    algebra.py        constraint-expression parser -> DFA via product/complement
    elimination.py    DFA -> regex (state elimination, Kleene's theorem)
    counting.py       count strings by length (DP), uniform sampling, language-is-finite check
    testgen.py        transition-coverage positives, boundary negatives, mutation tests
    trace.py          recorded steps of subset construction and table filling
    schema.py         mini JSON-schema -> LL(1) grammar (feeds pda.py)
  decoding/
    token_index.py    + trie-based index build (vocab trie x DFA product, prunes dead branches)
    generate.py       + telemetry: mass retained, preferred-vs-chosen token, certificate
    pda_generate.py   + trie walk for the fallback, schema mode
    engine.py         model registry, single model lock, cancellation
  api/                FastAPI
    main.py           app factory, lifespan loads the model once, CORS, static frontend
    routers/          automata.py  algebra.py  validators.py  generate.py  learn.py  system.py
    schemas.py        Pydantic request/response models
    services/         compile cache (LRU keyed by minimal-DFA signature), index jobs
  eval/               benchmark harness + suite definition -> results JSON/Markdown
frontend/             web app (stack: see section 6, decision needed)
tests/                unit (core), property (Hypothesis), API (TestClient), e2e (Playwright)
```

Streamlit `app.py` is retired once the new frontend reaches parity (kept until then).

### API surface (v1, all JSON, OpenAPI docs at `/docs`)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/compile` | regex -> AST, ε-NFA, DFA, min-DFA (DOT + tables + stats) |
| POST | `/api/v1/algebra` | constraint expression -> DFA, stats, equivalent regex, samples |
| POST | `/api/v1/equivalence` | two expressions -> verdict + shortest counterexample + which side accepts |
| POST | `/api/v1/validators/diff` | old vs new: strings only-old, only-new (several per side, shortest first) |
| POST | `/api/v1/validators/testgen` | positive/negative suite with coverage %, export as JSON/CSV/pytest file |
| POST | `/api/v1/count` | number of strings per length, total if finite, uniform samples |
| POST | `/api/v1/membership` | string -> accepted? + state path (certificate) |
| POST | `/api/v1/certificate/verify` | re-check a certificate independently |
| GET  | `/api/v1/learn/subset?regex=` | step-by-step subset construction trace |
| GET  | `/api/v1/learn/minimize?regex=` | table-filling trace (pairs marked per round) |
| POST | `/api/v1/generate` | **SSE stream**: constrained + unconstrained tokens, state, allowed count, pressure |
| POST | `/api/v1/generate/json` | SSE stream for the PDA (stack per step), optional schema |
| POST | `/api/v1/generate/batch` | N constrained samples (synthetic test data), deduplicated |
| GET  | `/api/v1/system/health`, `/models` | readiness, loaded model, cache stats |

Streaming uses Server-Sent Events (simple, works through proxies, one direction is enough).
Generation runs in a worker thread behind one model lock; a client disconnect cancels it.

### Frontend pages

1. **Studio**: regex or algebra editor with live validation; tabs for ε-NFA / DFA / min-DFA
   graphs (rendered from our DOT with Graphviz-WASM), state counts, membership tester
   that animates the path.
2. **Generate**: prompt + constraint, unconstrained vs constrained side by side,
   streaming; the min-DFA graph highlights the current state on every token; a pressure
   chart per step; certificate download.
3. **Constraint Algebra**: build `A & !B | C` from saved named constraints; result regex,
   size, examples.
4. **Validator Lab**: diff two validators, generate the test suite, export.
5. **JSON / Schema**: schema editor, PDA stack visualised as a growing/shrinking column.
6. **Learn**: step player for subset construction and table filling (next/prev, tables).
7. **Benchmarks**: renders the evaluation results.

---

## 3. Phases

Same rules as before: each phase ends with tests green, a commit and a 3-line summary.
Order puts the theory and backend first, so a usable product exists even if later
phases are cut.

### Phase A — Restructure and tooling (foundation)
- Move code into the `grammarguard/` package; `pyproject.toml` with optional extras
  (`[ml]`, `[api]`, `[dev]`); `ruff` + `mypy` configs; `Makefile` (`make test`, `make dev`).
- GitHub Actions CI: lint, type-check, core tests; ML tests marked `slow`.
- Mark GPT-2 tests `@pytest.mark.slow`, so the core suite runs in seconds.
- Exit: all 174 existing tests pass from the new layout.

### Phase B — Core theory extensions (stdlib only, each with Hypothesis tests vs `re`)
- **Complement** and the `dead` → `sink` refactor (complement turns the trap
  state accepting, so "dead" is no longer always the right name).
- **algebra.py**: expression grammar `expr := term ('|' term)*`, `term := factor ('&' factor)*`,
  `factor := '!' factor | '(' expr ')' | regex-literal | name | contains(r) | length(n,m)`,
  parsed by recursive descent and evaluated by product/complement with minimization after each step.
- **elimination.py**: DFA → regex by state elimination; test: regex → DFA → regex → DFA is equivalent.
- **counting.py**: count strings of each length by DP over the DFA; finite vs infinite language
  (cycle through a live state); uniform sampling of length n by weighted walks.
- **testgen.py**: shortest strings covering every transition (BFS + suffix to accept);
  boundary negatives (a valid prefix plus one symbol into the dead state, at every position);
  coverage report.
- **trace.py**: instrumented subset construction and table filling for the Learn page.
- **schema.py**: mini schema (`object` with required typed keys, `array` of a type,
  `string`, `integer`, `boolean`, `null`, string `enum`) → LL(1) grammar, checked conflict-free.
- Exit: new modules fully docstring'd, about 80 new tests, still no `re` in core.

### Phase C — Decoding engine upgrades
- **Trie-based token index**: walk a vocabulary trie in lockstep with the DFA, so shared
  prefixes are run once and dead branches are cut. Benchmark against the current
  per-token loop (now 0.4–0.7 s per regex) and keep whichever wins; must give an identical index.
- **Telemetry per step**: probability mass kept by the mask, model's preferred token, whether it
  was legal, entropy before/after. Aggregate "constraint pressure" per run.
- **Certificates**: DFA state path (or PDA stack trace) + DFA signature hash; a verifier that
  re-runs it.
- **Schema mode** for the PDA generator; trie walk for the full-vocab fallback.
- `engine.py`: one model instance, lock, cancellation flag; configurable model
  (`gpt2` default, `distilgpt2` for speed).
- Exit: all decoding tests green, including "index from trie == index from loop".

### Phase D — FastAPI backend
- App factory, lifespan model loading, settings via environment (`pydantic-settings`),
  structured logging, consistent error model (regex syntax errors -> 422 with position).
- Routers and Pydantic schemas from the table in section 2; SSE streaming endpoints.
- LRU compile cache keyed by the minimal-DFA signature; index cache stays on disk.
- Limits to stay responsive: max regex length, max DFA states, max tokens, timeouts.
- Tests with `TestClient`/`httpx`: every endpoint, error cases, SSE stream parsing.
- Exit: `uvicorn grammarguard.api.main:app` serves the documented API; API tests green.

### Phase E — Frontend
- Pages from section 2. Graphs: our DOT strings rendered in the browser with Graphviz-WASM
  (`@viz-js/viz`); no layout logic of our own.
- Live DFA highlight during streaming, pressure chart, stack column for the PDA,
  export buttons for certificates and test suites.
- Production build served by FastAPI as static files (one process, one port).
- Tests: component tests and a Playwright smoke test of each page.
- Exit: every feature reachable from the UI; e2e smoke test green.

### Phase F — Evaluation, packaging, docs
- `eval/`: benchmark suite (about 20 regexes from dates/IDs/phones/emails/policies x 10 prompts
  x greedy/top-k). Report validity %, tokens/s, index build time, mean pressure. Results table
  in the README.
- Dockerfile (CPU torch, multi-stage with the frontend build) and `docker-compose.yml`.
- Docs: README (quick start, screenshots, architecture diagram), `docs/API.md`,
  `docs/THEORY.md` (file → syllabus table, extended), `docs/VIVA.md` (likely questions and
  short answers), updated DECISIONS.md.
- Exit: fresh clone → `docker compose up` (or `make dev`) → working app.

---

## 4. Syllabus coverage after 2.0

Existing: regex, ε-NFA, ε-closure, subset construction, DFA, minimization, product,
equivalence, CFG, FIRST/FOLLOW, LL(1), deterministic PDA.
**New:** complement, closure under all Boolean operations, Kleene's theorem (DFA → regex),
emptiness/finiteness decision procedures, counting via DP, Myhill–Nerode table shown step by
step, grammar construction from a specification (schema → CFG).
Still future work: CNF/GNF, CYK, pumping lemma, LBA, PCP, Turing machines.

## 5. Risks and how the plan handles them

| Risk | Mitigation |
|---|---|
| Complement/product blow up state counts | minimize after every algebra step; hard cap on states with a clear error |
| CPU generation is slow in a web UI | KV cache (already), `distilgpt2` option, streaming so the first token shows fast |
| Concurrent users on one model | single lock + queue; limits in Phase D |
| Scope too large for the deadline | phases are ordered so A–D alone is a complete product (API + docs); E–F add the UI and results |
| Viva: "isn't this just Outlines?" | answer is in section 1: the algebra, diff, testgen, certificates and pressure metric are the contribution; constrained decoding is the substrate |

## 6. Decisions needed before starting

1. **Frontend stack.** Node.js is **not installed** in this WSL environment (`npm` resolves
   but `node` does not), and Docker is not installed either.
   - **React + TypeScript + Vite + Tailwind** (recommended for a "professional" look and the
     richest UI). Requires installing Node 20+ (e.g. via `nvm`), user-level, no sudo.
   - **No-build frontend**: HTML + ES modules + Alpine.js + Tailwind CDN, served by FastAPI.
     Nothing to install, simpler to explain, a bit less polished.
2. **Streamlit**: retire it after parity (recommended), or keep it as a lightweight teaching UI.
3. **Docker**: write the Dockerfile anyway (recommended; it just won't be run here), or skip it.
4. **Scope**: all phases A–F, or stop after D/E if the deadline is close.
