# Benchmark results (2026-10-28, gpt2, CPU)

288 runs over 18 validators; commit `bfa769a`; 12 CPUs; torch 2.14.1+cpu; 432 s wall time. Intervals are 95% Wilson confidence intervals.

## Does the output satisfy the validator?

"As is" means the whole output must match, as a real validator would require. "Extract a valid prefix" is the best a post-processing step could do: it counts a run if *any* non-empty prefix of the output matches.

| Method | Valid output | Mean tokens | Mean seconds |
|---|---|---|---|
| Unconstrained GPT-2, as is | 0% (0–3) | 29.3 | 0.85 |
| Unconstrained + extract a valid prefix | 11% (6–18) | | |
| Best-of-5 retries, as is | 3% (1–10) | 144.0 | 4.21 |
| Best-of-5 retries + extract a valid prefix | 17% (10–27) | | |
| GrammarGuard (DFA-constrained) | 100% (97–100) | 7.0 | 0.26 |

Plain GPT-2 almost never stops where the format ends (104 of 108 unconstrained runs hit the token limit), so compare *per-token* cost rather than total seconds.
Per-token cost: constrained decoding takes **1.34x** the time of unconstrained decoding (mask + pressure telemetry), excluding the one-off token-index build.
Mean constraint cost: 16.9 bits per output; mean pressure 48%; 2.8 overrides per output.

## By category

| Category | Unconstrained | Unconstrained + prefix | Best-of-5 | Best-of-5 + prefix | Constrained |
|---|---|---|---|---|---|
| dates | 0% (0–18) | 0% (0–18) | 0% (0–24) | 0% (0–24) | 100% (82–100) |
| emails | 0% (0–24) | 42% (19–68) | 12% (2–47) | 62% (31–86) | 100% (76–100) |
| ids | 0% (0–11) | 3% (1–17) | 0% (0–16) | 10% (3–30) | 100% (89–100) |
| numbers | 0% (0–24) | 0% (0–24) | 0% (0–32) | 12% (2–47) | 100% (76–100) |
| phones | 0% (0–24) | 0% (0–24) | 0% (0–32) | 0% (0–32) | 100% (76–100) |
| policies | 0% (0–14) | 25% (12–45) | 6% (1–28) | 25% (10–49) | 100% (86–100) |

## Token-index build time

Cost of building the *complete* token index once per constraint (a cold start). Large automata use a lazy index that only builds the states a generation actually visits, so they do not pay this up front.

| Validator | DFA states | Full index build |
|---|---|---|
| iso_date | 12 | 2 ms |
| dmy_date | 15 | 2 ms |
| time24 | 8 | 1 ms |
| us_phone | 16 | 6 ms |
| intl_phone | 17 | 7 ms |
| employee_id | 11 | 3 ms |
| ssn_like | 13 | 4 ms |
| passport | 10 | 5 ms |
| zip_us | 12 | 5 ms |
| semver | 10 | 5 ms |
| email_simple | 13 | 53 ms |
| username | 18 | 177 ms |
| decimal | 7 | 4 ms |
| bool_word | 12 | 1 ms |
| email_policy | 649 | 7526 ms |
| time_not_midnight | 12 | 2 ms |
| username_policy | 29 | 313 ms |
| date_in_2024 | 15 | 1 ms |
