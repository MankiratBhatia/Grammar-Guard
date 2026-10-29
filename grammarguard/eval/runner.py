"""Run the benchmark: for every case x prompt x sampling, compare four ways of getting valid output.

    unconstrained   plain GPT-2. Valid only if the WHOLE output matches. We also record whether any prefix
                    matches ("prefix_validity"), the best a regex post-processing hack could do.
    retry5          best-of-5 rejection sampling: sample unconstrained up to 5 times and keep the first output
                    that matches. This is the standard workaround without constrained decoding.
    constrained     GrammarGuard: the DFA masks every token.

Validity is checked by OUR DFA and cross-checked with Python's `re` for regex cases (`oracle_agrees`).
Timing excludes the one-off token-index build, which is reported separately per case.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from grammarguard.core.algebra import evaluate
from grammarguard.core.dfa import DFA, regex_to_dfa
from grammarguard.decoding.engine import Engine
from grammarguard.decoding.generate import collect, load_model
from grammarguard.decoding.generate import summarize as summarize_steps
from grammarguard.decoding.token_index import VocabTrie, build_index_trie, vocab_trie
from grammarguard.eval.stats import summarize
from grammarguard.eval.suite import CASES, QUICK_IDS, Case

# (name, top_k, seeds): greedy is deterministic, "sample" uses top-20 sampling with two seeds
SAMPLINGS: tuple[tuple[str, int | None, tuple[int, ...]], ...] = (("greedy", None, (0,)), ("sample", 20, (1, 2)))
RETRIES = 5


def compile_case(case: Case) -> DFA:
    """The minimal DFA of a case's constraint."""
    return regex_to_dfa(case.text) if case.kind == "regex" else evaluate(case.text).dfa


def has_valid_prefix(dfa: DFA, text: str) -> bool:
    """Does some non-empty prefix of `text` belong to the language? (One pass: check after every character.)"""
    q = dfa.start
    for ch in text:
        q = dfa.step(q, ch)
        if q in dfa.accept:
            return True
    return False


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


def run_suite(
    engine: Engine,
    cases: tuple[Case, ...] = CASES,
    *,
    max_tokens: int = 30,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run everything and return the full results document (JSON-serializable)."""
    say = progress or (lambda _m: None)
    import torch

    engine.warm_up()
    _, trie = vocab_trie(load_model(engine.model_name)[0])
    runs: list[dict[str, Any]] = []
    case_info: list[dict[str, Any]] = []
    started = time.perf_counter()

    for ci, case in enumerate(cases, 1):
        dfa = compile_case(case)
        t0 = time.perf_counter()
        _eager_probe(dfa, trie)  # time one honest index build for the report
        index_ms = (time.perf_counter() - t0) * 1000
        engine.index_for(dfa)  # warm the engine's own cache so run timings exclude it
        info = {
            "id": case.id,
            "category": case.category,
            "kind": case.kind,
            "text": case.text,
            "dfa_states": dfa.num_states(),
            "live_states": len(dfa.live_states()),
            "index_build_ms": round(index_ms, 1),
        }
        case_info.append(info)
        say(f"[{ci}/{len(cases)}] {case.id}: {dfa.num_states()} states, index {index_ms:.0f} ms")
        for pi, prompt in enumerate(case.prompts):
            for sampling, top_k, seeds in SAMPLINGS:
                for seed in seeds:
                    base = {"case": case.id, "prompt": pi, "sampling": sampling, "seed": seed}
                    opts = {"max_tokens": max_tokens, "top_k": top_k, "temperature": 1.0, "seed": seed}
                    runs.append({**base, **_unconstrained(engine, dfa, case, prompt, opts)})
                    runs.append({**base, **_constrained(engine, dfa, case, prompt, opts)})
                    if sampling == "sample":
                        runs.append({**base, **_retry(engine, dfa, case, prompt, opts)})
        say(f"    elapsed {time.perf_counter() - started:.0f}s")

    import grammarguard

    return {
        "meta": {
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "model": engine.model_name,
            "torch": torch.__version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpus": os.cpu_count(),
            "commit": _git_commit(),
            "version": getattr(grammarguard, "__version__", "2.0.0"),
            "max_tokens": max_tokens,
            "samplings": [{"name": n, "top_k": k, "seeds": list(s)} for n, k, s in SAMPLINGS],
            "retries": RETRIES,
            "wall_seconds": round(time.perf_counter() - started, 1),
        },
        "cases": case_info,
        "runs": runs,
        "summary": summarize(runs, case_info),
    }


def _eager_probe(dfa: DFA, trie: VocabTrie) -> None:
    """Build the full token index once (what a cold start would pay); discarded afterwards."""
    build_index_trie(dfa, trie)


def _oracle(case: Case, text: str) -> bool | None:
    """Python's `re` as an independent judge (regex cases only; our dialect is a subset of re's)."""
    return re.fullmatch(case.text, text) is not None if case.kind == "regex" else None


def _record(case: Case, dfa: DFA, mode: str, text: str, stop: str, tokens: int, seconds: float) -> dict[str, Any]:
    valid = dfa.accepts(text)
    oracle = _oracle(case, text)
    return {
        "mode": mode,
        "valid": valid,
        "valid_prefix": has_valid_prefix(dfa, text),
        "stop_reason": stop,
        "tokens": tokens,
        "seconds": round(seconds, 4),
        "text": text,
        "oracle_agrees": None if oracle is None else oracle == valid,
    }


def _unconstrained(engine: Engine, dfa: DFA, case: Case, prompt: str, opts: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    text, steps, stop = collect(engine.unconstrained(prompt, **opts))
    return _record(case, dfa, "unconstrained", text, stop, len(steps), time.perf_counter() - t0)


def _constrained(engine: Engine, dfa: DFA, case: Case, prompt: str, opts: dict[str, Any]) -> dict[str, Any]:
    t0 = time.perf_counter()
    text, steps, stop = collect(engine.constrained(dfa, prompt, **opts))
    rec = _record(case, dfa, "constrained", text, stop, len(steps), time.perf_counter() - t0)
    s = summarize_steps(steps)
    rec.update(
        {
            "mean_pressure": s.get("mean_pressure"),
            "total_cost_bits": s.get("total_cost_bits"),
            "overrides": s.get("overrides"),
        }
    )
    return rec


def _retry(engine: Engine, dfa: DFA, case: Case, prompt: str, opts: dict[str, Any]) -> dict[str, Any]:
    """Best-of-N rejection sampling with seeds opts.seed*100 + attempt; stops at the first valid output."""
    t0 = time.perf_counter()
    tokens, attempts, text, stop, any_prefix = 0, 0, "", "max_tokens", False
    for attempt in range(RETRIES):
        attempts += 1
        text, steps, stop = collect(engine.unconstrained(prompt, **{**opts, "seed": opts["seed"] * 100 + attempt}))
        tokens += len(steps)
        any_prefix = any_prefix or has_valid_prefix(dfa, text)
        if dfa.accepts(text):
            break
    rec = _record(case, dfa, "retry5", text, stop, tokens, time.perf_counter() - t0)
    rec["attempts"] = attempts
    rec["valid_prefix"] = any_prefix  # "some attempt had a valid prefix": what retries + extraction could rescue
    return rec


def selected_cases(quick: bool) -> tuple[Case, ...]:
    """The full suite, or the 3-case quick subset."""
    return tuple(c for c in CASES if c.id in QUICK_IDS) if quick else CASES


__all__ = ["run_suite", "selected_cases", "compile_case", "has_valid_prefix", "asdict"]
