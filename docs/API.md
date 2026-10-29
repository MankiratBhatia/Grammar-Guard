# API reference

Base path `/api/v1`. Interactive docs (generated from the code, always current): **`/docs`** (Swagger) and `/redoc`;
the machine-readable schema is at `/openapi.json`. Everything is JSON except the generation endpoints, which stream
[Server-Sent Events](#streaming-events).

Run the server: `python -m grammarguard.api` (or `make serve`, or `docker compose up`), then open <http://localhost:8000>.

## Conventions

**Constraint object** (used by most endpoints):

```json
{ "kind": "regex", "text": "[0-9]{4}-[0-9]{2}", "names": {} }
```

* `kind: "regex"`: one regular expression (supports `| * + ? ( ) [a-z] {n} {n,m}` and `\` escapes).
* `kind: "expression"`: the constraint algebra, e.g. `email & !contains(/spam/) & length(8, 30)`. Regexes go between slashes.
  `names` adds your own named constraints (`{"code": "[A-Z]{2}[0-9]{2}"}`). Built-ins are listed by `GET /builtins`.

**Errors** always have one shape; user mistakes are HTTP 422, only genuine bugs are 500:

```json
{ "error": { "code": "regex_syntax", "message": "missing ')' for '(' at pos 2", "position": 2, "details": null } }
```

| `code` | Meaning |
|---|---|
| `regex_syntax` | the regex is malformed or uses an unsupported feature (`.`, `^`, `$`, `[^…]`); `position` is the character index |
| `algebra` | a constraint expression is malformed or refers to an unknown name |
| `schema` | a JSON schema is malformed or unsupported |
| `limit_exceeded` | an input or automaton is larger than the server allows (see `/system/models` for limits) |
| `not_expressible` | no regex can be written for the result (empty language, or it accepts characters outside its alphabet) |
| `validation` | the request body is the wrong shape; `details` lists the fields |
| `not_found` | e.g. no benchmark results yet |
| `internal` | a server bug |

**Counts of strings are JSON strings**, never numbers: a realistic policy has about 3.6 × 10⁴⁵ matching strings, and JavaScript
numbers lose precision above 2⁵³.

Every response carries an `X-Request-ID` header (send your own to correlate with the server's JSON log line).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/compile` | regex → ε-NFA → DFA → minimal DFA (DOT drawings, transition tables, state counts) |
| POST | `/membership` | run a string through the DFA; state path, where it dies, certificate |
| POST | `/certificate/verify` | re-check a certificate without the language model |
| GET | `/builtins` | named constraints usable in expressions |
| POST | `/algebra` | evaluate a constraint expression: size, examples, equivalent regex, construction steps |
| POST | `/equivalence` | same language? otherwise a shortest counterexample |
| POST | `/count` | counts per length, finiteness, exact uniform samples |
| POST | `/validators/diff` | old vs new validator: relation and witness strings |
| POST | `/validators/testgen` | synthesize a test suite (JSON, CSV or a runnable pytest file) |
| GET | `/learn/subset?regex=` | subset construction, step by step |
| GET | `/learn/minimize?regex=` | table-filling minimization, round by round |
| POST | `/generate` | **SSE** DFA-constrained generation |
| POST | `/generate/json` | **SSE** PDA-constrained JSON generation (optionally schema-guided) |
| POST | `/generate/batch` | several *distinct* valid samples |
| GET | `/benchmarks` | stored evaluation results |
| GET | `/system/health` | liveness and model state |
| GET | `/system/models` | model name and the limits the server enforces |

### `POST /compile`

```bash
curl -s localhost:8000/api/v1/compile -H 'content-type: application/json' -d '{"regex": "(a|b)*abb"}'
```

Returns `ast` (desugared), `alphabet`, and `nfa` / `dfa` / `min_dfa`, each an *automaton view*:
`states`, `live_states`, `start`, `accepting`, `dot` (Graphviz text, or `null` when too large to draw), `transitions`
(`source`, `target`, merged `label`), `state_labels` (the NFA state sets / merged states). For `(a|b)*abb`: 14 → 6 → 5 states.

### `POST /membership`

```bash
curl -s localhost:8000/api/v1/membership -H 'content-type: application/json' \
  -d '{"constraint": {"kind": "regex", "text": "[0-9]{4}-[0-9]{2}"}, "text": "20x4-05"}'
```

`accepted`, `viable_prefix` (could it still be completed?), `doomed_at` (index of the first character after which no completion
exists), `path` (`source`, `char`, `target`, `live` per character) and a `certificate`.

### `POST /algebra`

```bash
curl -s localhost:8000/api/v1/algebra -H 'content-type: application/json' \
  -d '{"expression": "digits & !/0[0-9]*/ & length(2, 4)"}'
```

`dfa` view, `steps` (one per closure construction, with the intermediate state count), `finite`, `size` (string, finite languages only),
`regex` (equivalent regex by state elimination, only for automata of ≤ 40 states; otherwise `regex_note` says why), `examples`
(shortest accepted strings), `counts` (accepted strings of each length 0–12).

### `POST /equivalence`

```bash
curl -s localhost:8000/api/v1/equivalence -H 'content-type: application/json' \
  -d '{"a": {"text": "(a|b)*abb"}, "b": {"text": "(a|b)*bb"}}'
# → {"equivalent": false, "counterexample": "bb", "accepted_by_a": false, "accepted_by_b": true, ...}
```

### `POST /validators/diff`

```bash
curl -s localhost:8000/api/v1/validators/diff -H 'content-type: application/json' \
  -d '{"old": {"text": "[0-9]{3}-[0-9]{2}"}, "new": {"text": "[0-9]{3}[-_][0-9]{2}"}}'
```

`relation` is one of `equal`, `new_stricter`, `new_looser`, `overlap`, `disjoint`, with `only_old`, `only_new`, `both`
(shortest witnesses) and a plain-language `summary`. Here: `new_looser`, first witness `000_00`.

### `POST /validators/testgen`

`{"constraint": {...}, "format": "json" | "csv" | "pytest", "max_cases": 500}`. Returns `cases`
(`text`, `expected`, `kind`, `reason`), `counts` per kind, `coverage` (transition coverage = 1.0 for the positive cases) and, depending
on `format`, a `csv` string or a complete `pytest` source file that checks your validator with `re.fullmatch`.

### `POST /count`

`{"constraint": {...}, "max_length": 12, "sample_length": 8, "samples": 5, "seed": 0}` → `counts` (strings), `finite`, `size`,
`samples` (exactly uniform among accepted strings of that length), `shortest`.

### `GET /learn/subset` and `/learn/minimize`

Traces produced by the real algorithms (limited to 40 DFA states). `steps` records each `(S, a)` of subset construction with
`move`, `closure` and whether the DFA state is new; `rounds` records each round of table filling with the newly marked pairs and why.

## Streaming events

`POST /generate` and `/generate/json` answer `200` with `Content-Type: text/event-stream`. Validation problems (bad regex, limits)
are returned as normal HTTP 422 *before* any stream starts. Events:

| Event | Data |
|---|---|
| `meta` | `kind` (`dfa` / `pda`), `constraint`, `states`, `live_states`, `compare` |
| `status` | `{"state": "queued"}` when another generation holds the model |
| `token` | one per token (below) |
| `done` | one per run (`source`: `constrained` or `unconstrained`): `stop_reason`, `text`, `accepted`, `tokens`, and for constrained runs `summary` and `certificate` |
| `error` | `code`, `message` (a failure after streaming started) |
| `end` | the stream is complete |

`stop_reason` is one of `eos`, `max_tokens`, `dead_end`, `cancelled`, `timeout`.

DFA `token` event:

```json
{ "source": "constrained", "index": 0, "text": "2019", "state": 5, "allowed": 981,
  "telemetry": { "mass_kept": 0.0028, "pressure": 0.9972, "cost_bits": 8.50, "preferred": " ",
                 "preferred_legal": false, "p_chosen": 0.0003, "entropy_before": 5.02, "entropy_after": 8.36 } }
```

PDA `token` event: `text`, `stack` (top first), `valid` / `checked` (tokens that passed / were tested), `fallback` (the whole vocabulary
had to be searched), `mass_kept` (a lower bound after a top-100 check), `preferred_legal`, `p_chosen`.

**Request** (`/generate`): `constraint`, `prompt`, `max_tokens` (≤ server limit), `top_k` (`null` = greedy), `temperature`, `seed`,
`compare` (also stream an unconstrained run first), `certificate`. `/generate/json` takes `schema` (a mini JSON schema, or `null` for
any JSON object/array) and `top_n` instead of `constraint`.

```bash
curl -N localhost:8000/api/v1/generate -H 'content-type: application/json' \
  -d '{"constraint": {"text": "[0-9]{4}-[0-9]{2}-[0-9]{2}"}, "prompt": "Date: ", "compare": true}'
```

Closing the connection cancels the generation at the next token and frees the model for the next request.

From JavaScript use `fetch` with a stream reader (`EventSource` cannot POST); see `frontend/src/api/sse.ts`.

### Mini JSON schema

```json
{ "type": "object", "properties": { "name": {"type": "string"}, "age": {"type": "integer"},
                                     "role": {"enum": ["admin", "editor"]}, "tags": {"type": "array", "items": {"type": "string"}} } }
```

Supported: object (keys appear in exactly this order, all required), array (`minItems` 0 or 1), `string` (lowercase letters),
`integer` (non-negative), `boolean`, `null`, `enum` (lowercase words).

## Certificates

`certificate` (from `/membership`, `/generate`, `/generate/batch`) is verified with
`POST /certificate/verify {"certificate": {...}}` → `{"valid", "accepted", "reason"}`. The server rebuilds the automaton from the
constraint text stored in the certificate (so user-defined `names` are included in it) and replays the recorded run. `valid` means the
certificate is truthful; `accepted` means the text is in the language. A certificate can honestly show that a text is *not* accepted.

## Configuration

Environment variables with the prefix `GRAMMARGUARD_` (see `grammarguard/api/settings.py`): `MODEL_NAME`, `WARM_START`,
`CORS_ORIGINS`, `FRONTEND_DIR`, `BENCHMARKS_PATH`, `CACHE_DIR`, and the limits `MAX_REGEX_LEN`, `MAX_EXPRESSION_LEN`, `MAX_TEXT_LEN`,
`MAX_PROMPT_LEN`, `MAX_STATES`, `MAX_DFA_STATES`, `MAX_TOKENS`, `MAX_BATCH`, `MAX_COUNT_LENGTH`, `GENERATION_TIMEOUT`.
