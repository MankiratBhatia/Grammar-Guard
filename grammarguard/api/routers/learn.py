"""Learn: step-by-step traces of the real algorithms."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from grammarguard.api.deps import get_compiler, get_settings
from grammarguard.api.errors import LimitError
from grammarguard.api.schemas import LearnMinimizeResponse, LearnSubsetResponse
from grammarguard.api.services.compiler import CompileService
from grammarguard.api.settings import Settings
from grammarguard.core.dfa import minimize, subset_construction
from grammarguard.core.nfa import OTHER, regex_to_nfa

router = APIRouter(prefix="/learn", tags=["learn"])
Compiler = Annotated[CompileService, Depends(get_compiler)]
Config = Annotated[Settings, Depends(get_settings)]
Regex = Annotated[str, Query(max_length=200, examples=["(a|b)*abb"])]


def _show_other(record: dict[str, Any]) -> dict[str, Any]:
    """Name the OTHER symbol ("any character not in the alphabet") inside a trace record."""
    marked = [{**m, "symbol": "other"} if m.get("symbol") == OTHER and "symbol" in m else m for m in record["marked"]]
    return {**record, "marked": marked}


@router.get("/subset", response_model=LearnSubsetResponse, summary="Subset construction, step by step")
def subset(regex: Regex, svc: Compiler, cfg: Config) -> LearnSubsetResponse:
    """Each record is one (subset S, symbol a) step: move(S, a), its ε-closure, and whether it is a new DFA state.
    The trace is produced by the real algorithm."""
    svc.check_regex(regex)
    nfa = regex_to_nfa(regex)
    trace: list[dict[str, Any]] = []
    dfa = subset_construction(nfa, trace=trace, max_states=cfg.learn_max_states)
    for step in trace:
        step["symbol"] = "other" if step["symbol"] == OTHER else step["symbol"]
    return LearnSubsetResponse(regex=regex, nfa=svc.nfa_view(nfa), steps=trace, dfa=svc.dfa_view(dfa, labels=True))


@router.get("/minimize", response_model=LearnMinimizeResponse, summary="Table-filling minimization, round by round")
def minimization(regex: Regex, svc: Compiler, cfg: Config) -> LearnMinimizeResponse:
    """Round 0 marks pairs split by acceptance; each later round marks pairs that reach a marked pair on some
    symbol. Unmarked pairs are merged."""
    svc.check_regex(regex)
    dfa = subset_construction(regex_to_nfa(regex), max_states=cfg.learn_max_states)
    if dfa.num_states() > cfg.learn_max_states:
        raise LimitError(f"the Learn view is limited to {cfg.learn_max_states} states")
    trace: list[dict[str, Any]] = []
    small = minimize(dfa, trace=trace)
    rounds = [_show_other(t) for t in trace if "round" in t]
    classes = trace[-1]["classes"]
    return LearnMinimizeResponse(
        regex=regex,
        dfa=svc.dfa_view(dfa, labels=True),
        rounds=rounds,
        classes=classes,
        min_dfa=svc.dfa_view(small, labels=True),
    )
