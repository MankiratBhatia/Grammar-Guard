"""Compile a regex, run strings through a DFA, verify certificates."""

from __future__ import annotations

import json
import time
from typing import Annotated

from fastapi import APIRouter, Depends

from grammarguard.api.deps import get_compiler
from grammarguard.api.errors import ApiError
from grammarguard.api.schemas import (
    CertificateVerifyRequest,
    CertificateVerifyResponse,
    CompileRequest,
    CompileResponse,
    MembershipRequest,
    MembershipResponse,
    PathStep,
)
from grammarguard.api.services.compiler import CompileService, as_expression
from grammarguard.core.certificate import (
    Certificate,
    make_dfa_certificate,
    verify_dfa_certificate,
    verify_pda_certificate,
)
from grammarguard.core.pda import JSON_PDA, PDA
from grammarguard.core.regex_parser import to_string
from grammarguard.core.schema import schema_to_grammar

router = APIRouter(tags=["automata"])
Compiler = Annotated[CompileService, Depends(get_compiler)]


@router.post("/compile", response_model=CompileResponse, summary="Regex -> ε-NFA -> DFA -> minimal DFA")
def compile_regex(req: CompileRequest, svc: Compiler) -> CompileResponse:
    """Run the whole pipeline (Thompson, subset construction, table-filling) and return each automaton
    as DOT text, a transition list and state counts."""
    t0 = time.perf_counter()
    p = svc.pipeline(req.regex)
    return CompileResponse(
        regex=req.regex,
        ast=to_string(p.ast),
        alphabet=sorted(p.nfa.alphabet),
        nfa=svc.nfa_view(p.nfa),
        dfa=svc.dfa_view(p.dfa, labels=True),
        min_dfa=svc.dfa_view(p.min_dfa, labels=True),
        elapsed_ms=round((time.perf_counter() - t0) * 1000, 2),
    )


@router.post("/membership", response_model=MembershipResponse, summary="Run a string through the DFA")
def membership(req: MembershipRequest, svc: Compiler) -> MembershipResponse:
    """Accepted or not, with the state path. The path is also returned as a verifiable certificate."""
    svc.check_text(req.text)
    dfa = svc.resolve(req.constraint).dfa
    live = dfa.live_states()
    path, q, doomed = [], dfa.start, None
    for i, ch in enumerate(req.text):
        nxt = dfa.step(q, ch)
        path.append(PathStep(index=i, char=ch, source=q, target=nxt, live=nxt in live))
        if nxt not in live and doomed is None:
            doomed = i
        q = nxt
    cert = make_dfa_certificate(dfa, as_expression(req.constraint), req.text)
    cert.names = dict(req.constraint.names)
    return MembershipResponse(
        accepted=q in dfa.accept,
        path=path,
        start=dfa.start,
        final_state=q,
        doomed_at=doomed,
        viable_prefix=q in live,
        certificate=cert.to_dict(),
    )


@router.post("/certificate/verify", response_model=CertificateVerifyResponse, summary="Re-check a certificate")
def verify_certificate(req: CertificateVerifyRequest, svc: Compiler) -> CertificateVerifyResponse:
    """Rebuild the automaton from the constraint text inside the certificate and replay the recorded run.
    No language model is involved."""
    try:
        cert = Certificate.from_dict(req.certificate)
    except TypeError:
        raise ApiError("validation", "certificate is missing required fields") from None
    if cert.kind == "dfa":
        dfa = svc.dfa_for_expression(cert.constraint, cert.names)
        v = verify_dfa_certificate(cert, dfa)
        return CertificateVerifyResponse(
            valid=v.valid, accepted=v.accepted, reason=v.reason, automaton_states=dfa.num_states()
        )
    if cert.kind == "pda":
        if cert.constraint == "json":
            pda = JSON_PDA
        elif cert.constraint.startswith("schema:"):
            pda = PDA(schema_to_grammar(json.loads(cert.constraint[len("schema:") :])))
        else:
            raise ApiError("validation", f"unknown PDA constraint {cert.constraint[:30]!r}")
        v = verify_pda_certificate(cert, pda)
        return CertificateVerifyResponse(valid=v.valid, accepted=v.accepted, reason=v.reason)
    raise ApiError("validation", f"unknown certificate kind {cert.kind!r}")
