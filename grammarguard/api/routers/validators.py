"""Validator Lab: diff two validators, synthesize a test suite."""

from __future__ import annotations

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends

from grammarguard.api.deps import get_compiler, get_settings
from grammarguard.api.errors import ApiError, LimitError
from grammarguard.api.schemas import DiffRequest, DiffResponse, TestCaseOut, TestgenRequest, TestgenResponse
from grammarguard.api.services.compiler import CompileService
from grammarguard.api.settings import Settings
from grammarguard.core import counting, testgen
from grammarguard.core.dfa import product
from grammarguard.core.elimination import dfa_to_regex

router = APIRouter(prefix="/validators", tags=["validators"])
Compiler = Annotated[CompileService, Depends(get_compiler)]
Config = Annotated[Settings, Depends(get_settings)]


@router.post("/diff", response_model=DiffResponse, summary="Compare an old and a new validator")
def diff(req: DiffRequest, svc: Compiler, cfg: Config) -> DiffResponse:
    """Closure properties in action: L_old − L_new, L_new − L_old and L_old ∩ L_new are each a product DFA;
    a non-empty one is searched (BFS) for its shortest strings, which are the witnesses."""
    old, new = svc.resolve(req.old).dfa, svc.resolve(req.new).dfa
    limit = cfg.max_states * 20
    only_old = counting.shortest_strings(product(old, new, "difference", limit=limit), req.examples)
    only_new = counting.shortest_strings(product(new, old, "difference", limit=limit), req.examples)
    both = counting.shortest_strings(product(old, new, "intersection", limit=limit), min(req.examples, 5))
    if not only_old and not only_new:
        relation, summary = "equal", "The validators accept exactly the same strings."
    elif not only_new:
        relation, summary = (
            "new_stricter",
            "The new validator accepts fewer strings: everything it accepts the old one accepted.",
        )
    elif not only_old:
        relation, summary = (
            "new_looser",
            "The new validator accepts more strings: everything the old one accepted is still accepted.",
        )
    elif both:
        relation, summary = "overlap", "Each validator accepts strings the other rejects."
    else:
        relation, summary = "disjoint", "No string is accepted by both validators."
    return DiffResponse(
        relation=relation,
        summary=summary,
        only_old=only_old,
        only_new=only_new,
        both=both,
        states_old=old.num_states(),
        states_new=new.num_states(),
    )


@router.post("/testgen", response_model=TestgenResponse, summary="Synthesize a test suite with transition coverage")
def generate_test_suite(req: TestgenRequest, svc: Compiler, cfg: Config) -> TestgenResponse:
    """Positive cases covering every DFA transition, plus boundary / substitution / deletion / truncation
    negatives. Optionally exported as CSV or as a ready-to-run pytest file."""
    if req.max_cases > cfg.max_testgen_cases:
        raise LimitError(f"max_cases is limited to {cfg.max_testgen_cases}")
    dfa = svc.resolve(req.constraint).dfa
    cases = testgen.generate_tests(dfa)
    coverage = testgen.coverage(dfa, [c.text for c in cases if c.expected])
    kept = cases[: req.max_cases]
    counts: dict[str, int] = {}
    for c in kept:
        counts[c.kind] = counts.get(c.kind, 0) + 1
    out = TestgenResponse(
        cases=[TestCaseOut(text=c.text, expected=c.expected, kind=c.kind, reason=c.reason) for c in kept],
        counts=counts,
        coverage=coverage,
        truncated=len(cases) > len(kept),
    )
    if req.format == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["text", "expected", "kind", "reason"])
        w.writerows([c.text, c.expected, c.kind, c.reason] for c in kept)
        out.csv = buf.getvalue()
    elif req.format == "pytest":
        if req.constraint.kind == "regex":
            pattern = req.constraint.text
        elif dfa.num_states() > cfg.max_regex_states:
            raise ApiError(
                "not_expressible",
                f"the automaton has {dfa.num_states()} states; an equivalent regex would be impractically large",
            )
        else:
            pattern = dfa_to_regex(dfa)
        out.pattern = pattern
        out.pytest = testgen.to_pytest(pattern, kept)
    return out
