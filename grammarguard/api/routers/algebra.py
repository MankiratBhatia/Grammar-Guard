"""Constraint algebra, equivalence, counting."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from grammarguard.api.deps import get_compiler, get_settings
from grammarguard.api.errors import LimitError
from grammarguard.api.schemas import (
    AlgebraRequest,
    AlgebraResponse,
    AlgebraStep,
    BuiltinsResponse,
    Constraint,
    CountRequest,
    CountResponse,
    EquivalenceRequest,
    EquivalenceResponse,
)
from grammarguard.api.services.compiler import CompileService
from grammarguard.api.settings import Settings
from grammarguard.core import counting
from grammarguard.core.algebra import BUILTINS
from grammarguard.core.dfa import equivalent
from grammarguard.core.elimination import NotExpressibleError, dfa_to_regex

router = APIRouter(tags=["algebra"])
Compiler = Annotated[CompileService, Depends(get_compiler)]
Config = Annotated[Settings, Depends(get_settings)]


@router.get("/builtins", response_model=BuiltinsResponse, summary="Named constraints usable in expressions")
def builtins() -> BuiltinsResponse:
    """The built-in names (digit, email, iso_date, ...) and the regex behind each."""
    return BuiltinsResponse(builtins=BUILTINS)


@router.post("/algebra", response_model=AlgebraResponse, summary="Evaluate a constraint expression")
def evaluate_expression(req: AlgebraRequest, svc: Compiler, cfg: Config) -> AlgebraResponse:
    """Combine regular languages with ∩ ∪ − ¬ concat star (product / complement constructions), minimize,
    and describe the result: size, examples, an equivalent regex (state elimination)."""
    compiled = svc.resolve(Constraint(kind="expression", text=req.expression, names=req.names))
    dfa = compiled.dfa
    regex, note = None, None
    if dfa.num_states() > cfg.max_regex_states:
        note = f"the automaton has {dfa.num_states()} states; an equivalent regex would be impractically large"
    else:
        try:
            regex = dfa_to_regex(dfa)
        except NotExpressibleError as e:
            note = str(e)
    size = counting.language_size(dfa)
    return AlgebraResponse(
        expression=req.expression,
        dfa=svc.dfa_view(dfa),
        steps=[AlgebraStep(expr=s.expr, operation=s.operation, states=s.states) for s in compiled.steps],
        finite=size is not None,
        size=None if size is None else str(size),
        regex=regex,
        regex_note=note,
        examples=counting.shortest_strings(dfa, req.examples),
        counts=[str(c) for c in counting.counts_by_length(dfa, 12)],
    )


@router.post("/equivalence", response_model=EquivalenceResponse, summary="Are two constraints the same language?")
def check_equivalence(req: EquivalenceRequest, svc: Compiler) -> EquivalenceResponse:
    """L1 = L2 iff their symmetric difference is empty; otherwise a shortest string in it is returned."""
    a, b = svc.resolve(req.a).dfa, svc.resolve(req.b).dfa
    same, witness = equivalent(a, b)
    return EquivalenceResponse(
        equivalent=same,
        counterexample=witness,
        accepted_by_a=None if witness is None else a.accepts(witness),
        accepted_by_b=None if witness is None else b.accepts(witness),
        states_a=a.num_states(),
        states_b=b.num_states(),
    )


@router.post("/count", response_model=CountResponse, summary="Count and sample the language")
def count(req: CountRequest, svc: Compiler, cfg: Config) -> CountResponse:
    """Number of accepted strings of each length (dynamic programming over the DFA, over the constraint's own
    alphabet), whether the language is finite, and exact uniform samples of one length."""
    for n in (req.max_length, req.sample_length or 0):
        if n > cfg.max_count_length:
            raise LimitError(f"lengths are limited to {cfg.max_count_length}")
    dfa = svc.resolve(req.constraint).dfa
    size = counting.language_size(dfa)
    samples = []
    if req.sample_length is not None:
        samples = counting.sample_uniform(dfa, req.sample_length, req.samples, req.seed)
    return CountResponse(
        counts=[str(c) for c in counting.counts_by_length(dfa, req.max_length)],
        finite=size is not None,
        size=None if size is None else str(size),
        sample_length=req.sample_length,
        samples=samples,
        shortest=counting.shortest_strings(dfa, 5),
    )
