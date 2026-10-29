"""Statistics for the benchmark: confidence intervals and aggregation. Pure functions, no model needed."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion.

    Better than the usual normal approximation when the rate is near 0 or 1 or n is small, which is exactly
    our situation (e.g. 0 valid out of 36, or 36 out of 36).
    """
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    lo = 0.0 if successes == 0 else max(0.0, centre - half)  # the endpoints are exactly 0 / 1 there; avoid float fuzz
    hi = 1.0 if successes == n else min(1.0, centre + half)
    return (lo, hi)


def rate(flags: Iterable[bool]) -> dict[str, float | int]:
    """Success rate with its Wilson interval."""
    flags = list(flags)
    k, n = sum(flags), len(flags)
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": k / n if n else 0.0, "lo": lo, "hi": hi}


def mean(xs: Iterable[float]) -> float | None:
    """Arithmetic mean, None for an empty sequence (so JSON shows null, not NaN)."""
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def _tps(run: dict[str, Any]) -> float | None:
    """Tokens per second of one run (None when there is nothing to divide)."""
    return run["tokens"] / run["seconds"] if run["seconds"] > 0 and run["tokens"] > 0 else None


def summarize_group(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Headline numbers for one group of runs (any mix of modes; callers group by mode first).

    validity            the whole output matches the constraint
    prefix_validity     SOME non-empty prefix of the output matches (what regex post-processing could rescue)
    """
    by_mode: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in runs:
        by_mode[r["mode"]].append(r)
    out: dict[str, Any] = {}
    for mode, rs in by_mode.items():
        entry: dict[str, Any] = {
            "validity": rate(r["valid"] for r in rs),
            "mean_tokens": mean(r["tokens"] for r in rs),
            "mean_seconds": mean(r["seconds"] for r in rs),
            "mean_tokens_per_second": mean(t for t in (_tps(r) for r in rs) if t is not None),
        }
        if mode in ("unconstrained", "retry5"):
            entry["prefix_validity"] = rate(r["valid_prefix"] for r in rs)
        if mode == "retry5":
            entry["mean_attempts"] = mean(r["attempts"] for r in rs)
        if mode == "constrained":
            entry["mean_pressure"] = mean(r["mean_pressure"] for r in rs if r.get("mean_pressure") is not None)
            entry["mean_cost_bits"] = mean(r["total_cost_bits"] for r in rs if r.get("total_cost_bits") is not None)
            entry["mean_overrides"] = mean(r["overrides"] for r in rs if r.get("overrides") is not None)
        out[mode] = entry
    unc, con = out.get("unconstrained"), out.get("constrained")
    if unc and con and unc["mean_tokens_per_second"] and con["mean_tokens_per_second"]:
        # >1 means constrained decoding is slower per token (mask + telemetry overhead)
        out["slowdown"] = unc["mean_tokens_per_second"] / con["mean_tokens_per_second"]
    return out


def summarize(runs: list[dict[str, Any]], cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall, per-category, per-case and per-sampling summaries."""
    category = {c["id"]: c["category"] for c in cases}
    groups: dict[str, dict[str, list]] = {
        "category": defaultdict(list),
        "case": defaultdict(list),
        "sampling": defaultdict(list),
    }
    for r in runs:
        groups["category"][category[r["case"]]].append(r)
        groups["case"][r["case"]].append(r)
        groups["sampling"][r["sampling"]].append(r)
    return {
        "overall": summarize_group(runs),
        "by_category": {k: summarize_group(v) for k, v in sorted(groups["category"].items())},
        "by_case": {k: summarize_group(v) for k, v in groups["case"].items()},
        "by_sampling": {k: summarize_group(v) for k, v in sorted(groups["sampling"].items())},
    }
