"""Render benchmark results as a Markdown report (for the README / docs)."""

from __future__ import annotations

from typing import Any


def pct(r: dict[str, Any]) -> str:
    """'94% (89–97)' style cell for a rate with its Wilson interval."""
    return f"{r['rate'] * 100:.0f}% ({r['lo'] * 100:.0f}–{r['hi'] * 100:.0f})"


def headline_rows(group: dict[str, Any], retries: int = 5) -> list[tuple[str, dict[str, Any], dict[str, Any] | None]]:
    """(label, rate, mode summary) rows: each baseline both AS-IS and with prefix extraction, then GrammarGuard."""
    rows: list[tuple[str, dict[str, Any], dict[str, Any] | None]] = []
    unc, retry, con = group.get("unconstrained"), group.get("retry5"), group.get("constrained")
    if unc:
        rows.append(("Unconstrained GPT-2, as is", unc["validity"], unc))
        rows.append(("Unconstrained + extract a valid prefix", unc["prefix_validity"], None))
    if retry:
        rows.append((f"Best-of-{retries} retries, as is", retry["validity"], retry))
        rows.append((f"Best-of-{retries} retries + extract a valid prefix", retry["prefix_validity"], None))
    if con:
        rows.append(("GrammarGuard (DFA-constrained)", con["validity"], con))
    return rows


def markdown(results: dict[str, Any]) -> str:
    """Headline table, per-category table, and the index build times."""
    meta, summary = results["meta"], results["summary"]
    overall = summary["overall"]
    lines = [
        f"# Benchmark results ({meta['date'][:10]}, {meta['model']}, CPU)",
        "",
        f"{len(results['runs'])} runs over {len(results['cases'])} validators; commit `{meta.get('commit')}`; "
        f"{meta['cpus']} CPUs; torch {meta['torch']}; {meta['wall_seconds']:.0f} s wall time. "
        "Intervals are 95% Wilson confidence intervals.",
        "",
        "## Does the output satisfy the validator?",
        "",
        '"As is" means the whole output must match, as a real validator would require. '
        '"Extract a valid prefix" is the best a post-processing step could do: '
        "it counts a run if *any* non-empty prefix of the output matches.",
        "",
        "| Method | Valid output | Mean tokens | Mean seconds |",
        "|---|---|---|---|",
    ]
    for label, rate, mode in headline_rows(overall, meta["retries"]):
        extra = f" {mode['mean_tokens']:.1f} | {mode['mean_seconds']:.2f} |" if mode else " | |"
        lines.append(f"| {label} | {pct(rate)} |{extra}")

    unconstrained = overall.get("unconstrained")
    if unconstrained:
        stops = sum(1 for r in results["runs"] if r["mode"] == "unconstrained" and r["stop_reason"] == "eos")
        total = sum(1 for r in results["runs"] if r["mode"] == "unconstrained")
        lines += [
            "",
            f"Plain GPT-2 almost never stops where the format ends ({total - stops} of {total} unconstrained runs "
            "hit the token limit), so compare *per-token* cost rather than total seconds.",
        ]
    if "slowdown" in overall:
        lines.append(
            f"Per-token cost: constrained decoding takes **{overall['slowdown']:.2f}x** the time of unconstrained "
            "decoding (mask + pressure telemetry), excluding the one-off token-index build."
        )
    constrained = overall.get("constrained")
    if constrained and constrained.get("mean_cost_bits") is not None:
        lines.append(
            f"Mean constraint cost: {constrained['mean_cost_bits']:.1f} bits per output; mean pressure "
            f"{constrained['mean_pressure'] * 100:.0f}%; {constrained['mean_overrides']:.1f} overrides per output."
        )

    lines += [
        "",
        "## By category",
        "",
        "| Category | Unconstrained | Unconstrained + prefix | Best-of-5 | Best-of-5 + prefix | Constrained |",
    ]
    lines.append("|---|---|---|---|---|---|")
    for category, group in summary["by_category"].items():
        rows = {label: rate for label, rate, _ in headline_rows(group, meta["retries"])}
        cells = [pct(r) for r in rows.values()]
        lines.append(f"| {category} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## Token-index build time",
        "",
        "Cost of building the *complete* token index once per constraint (a cold start). Large automata use a lazy "
        "index that only builds the states a generation actually visits, so they do not pay this up front.",
        "",
        "| Validator | DFA states | Full index build |",
        "|---|---|---|",
    ]
    for case in results["cases"]:
        lines.append(f"| {case['id']} | {case['dfa_states']} | {case['index_build_ms']:.0f} ms |")
    return "\n".join(lines) + "\n"
