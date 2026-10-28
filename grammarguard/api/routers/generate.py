"""Constrained generation: SSE streams for DFA and PDA constraints, plus batch sampling."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from grammarguard.api.deps import get_compiler, get_engine, get_settings
from grammarguard.api.errors import LimitError
from grammarguard.api.schemas import BatchRequest, BatchResponse, BatchSample, GenerateRequest, JsonGenerateRequest
from grammarguard.api.services.compiler import CompileService, as_expression, schema_key
from grammarguard.api.services.streaming import Emit, stream
from grammarguard.api.settings import Settings
from grammarguard.core.certificate import make_dfa_certificate, make_pda_certificate
from grammarguard.core.pda import JSON_PDA, PDA
from grammarguard.core.schema import schema_to_grammar
from grammarguard.decoding.engine import CancelToken, Engine
from grammarguard.decoding.generate import collect, summarize

router = APIRouter(prefix="/generate", tags=["generate"])
Compiler = Annotated[CompileService, Depends(get_compiler)]
Config = Annotated[Settings, Depends(get_settings)]
Eng = Annotated[Engine, Depends(get_engine)]


def drive(
    gen: Iterator[Any], emit: Emit, source: str, payload: Callable[[Any], dict[str, Any]]
) -> tuple[list[Any], str]:
    """Pull steps out of a generator, emit a `token` event for each, return (steps, stop reason).

    The generator is always closed, which releases the engine lock even if we stop early.
    """
    steps: list[Any] = []
    try:
        while True:
            try:
                step = next(gen)
            except StopIteration as stop:
                return steps, stop.value
            steps.append(step)
            emit("token", {"source": source, "index": len(steps) - 1, **payload(step)})
    finally:
        gen.close()  # type: ignore[attr-defined]


def finish_reason(reason: str, timed_out: threading.Event) -> str:
    """A run cancelled by the timer is reported as 'timeout'."""
    return "timeout" if reason == "cancelled" and timed_out.is_set() else reason


def dfa_token(step: Any) -> dict[str, Any]:
    """JSON for one DFA-generation step."""
    return {
        "text": step.token_text,
        "state": step.dfa_state,
        "allowed": step.num_allowed,
        "telemetry": step.telemetry.to_dict() if step.telemetry else None,
    }


def pda_token(step: Any) -> dict[str, Any]:
    """JSON for one PDA-generation step."""
    return {
        "text": step.token_text,
        "stack": list(step.stack),
        "valid": step.num_valid,
        "checked": step.num_checked,
        "fallback": step.fallback,
        "mass_kept": step.mass_kept,
        "preferred_legal": step.preferred_legal,
        "p_chosen": step.p_chosen,
    }


def pda_summary(steps: list[Any]) -> dict[str, Any]:
    """Pressure summary for PDA runs (mass_kept is a lower bound after a top-N check)."""
    n = len(steps)
    if not n:
        return {"steps": 0}
    overrides = sum(1 for s in steps if not s.preferred_legal)
    return {
        "steps": n,
        "mean_pressure_upper_bound": sum(1 - s.mass_kept for s in steps) / n,
        "overrides": overrides,
        "override_rate": overrides / n,
        "fallbacks": sum(1 for s in steps if s.fallback),
    }


def check_common(svc: CompileService, cfg: Settings, prompt: str, max_tokens: int) -> None:
    """Limits shared by all generation endpoints."""
    svc.check_prompt(prompt)
    if max_tokens > cfg.max_tokens:
        raise LimitError(f"max_tokens is limited to {cfg.max_tokens}")


@router.post("", response_class=EventSourceResponse, summary="Stream a DFA-constrained generation (SSE)")
def generate(req: GenerateRequest, svc: Compiler, cfg: Config, engine: Eng):
    """Streams `meta`, then `token` events (text, DFA state, #allowed tokens, constraint-pressure telemetry),
    then `done` with the full text, whether it matches, a pressure summary and a verifiable certificate.
    With `compare` an unconstrained run is streamed first (events carry `source`)."""
    check_common(svc, cfg, req.prompt, req.max_tokens)
    dfa = svc.resolve(req.constraint).dfa
    expression = as_expression(req.constraint)
    options = {"max_tokens": req.max_tokens, "top_k": req.top_k, "temperature": req.temperature, "seed": req.seed}

    def run(cancel: CancelToken, emit: Emit, timed_out: threading.Event) -> None:
        emit(
            "meta",
            {
                "kind": "dfa",
                "constraint": expression,
                "states": dfa.num_states(),
                "live_states": len(dfa.live_states()),
                "compare": req.compare,
                "options": options,
            },
        )
        if engine.busy:
            emit("status", {"state": "queued"})
        for source in (["unconstrained"] if req.compare else []) + ["constrained"]:
            if source == "constrained":
                gen = engine.constrained(dfa, req.prompt, cancel=cancel, **options)
            else:
                gen = engine.unconstrained(req.prompt, cancel=cancel, **options)
            steps, reason = drive(gen, emit, source, dfa_token)
            text = "".join(s.token_text for s in steps)
            done: dict[str, Any] = {
                "source": source,
                "stop_reason": finish_reason(reason, timed_out),
                "text": text,
                "accepted": dfa.accepts(text),
                "tokens": len(steps),
            }
            if source == "constrained":
                done["summary"] = summarize(steps)
                if req.certificate:
                    cert = make_dfa_certificate(dfa, expression, text, [s.token_text for s in steps])
                    cert.names = dict(req.constraint.names)
                    done["certificate"] = cert.to_dict()
            emit("done", done)
            if cancel():
                break

    return stream(run, cfg.generation_timeout)


@router.post("/json", response_class=EventSourceResponse, summary="Stream a PDA-constrained JSON generation (SSE)")
def generate_json(req: JsonGenerateRequest, svc: Compiler, cfg: Config, engine: Eng):
    """Like `/generate`, but the constraint is the JSON grammar (or a mini JSON schema compiled to an LL(1)
    grammar). Token events carry the PDA stack, how many tokens were checked, and whether the full-vocabulary
    fallback was needed."""
    check_common(svc, cfg, req.prompt, req.max_tokens)
    pda = JSON_PDA if req.schema_ is None else PDA(schema_to_grammar(req.schema_))
    key = schema_key(req.schema_)
    options = {"max_tokens": req.max_tokens, "top_k": req.top_k, "temperature": req.temperature, "seed": req.seed}

    def run(cancel: CancelToken, emit: Emit, timed_out: threading.Event) -> None:
        emit("meta", {"kind": "pda", "constraint": key, "compare": req.compare, "top_n": req.top_n, "options": options})
        if engine.busy:
            emit("status", {"state": "queued"})
        for source in (["unconstrained"] if req.compare else []) + ["constrained"]:
            if source == "constrained":
                gen = engine.json(req.prompt, pda=pda, cancel=cancel, top_n=req.top_n, **options)
                steps, reason = drive(gen, emit, source, pda_token)
            else:
                gen = engine.unconstrained(req.prompt, cancel=cancel, **options)
                steps, reason = drive(gen, emit, source, dfa_token)
            text = "".join(s.token_text for s in steps)
            done: dict[str, Any] = {
                "source": source,
                "stop_reason": finish_reason(reason, timed_out),
                "text": text,
                "accepted": pda.accepts(text),
                "tokens": len(steps),
            }
            if source == "constrained":
                done["summary"] = pda_summary(steps)
                if req.certificate:
                    done["certificate"] = make_pda_certificate(pda, key, text, [s.token_text for s in steps]).to_dict()
            emit("done", done)
            if cancel():
                break

    return stream(run, cfg.generation_timeout)


@router.post("/batch", response_model=BatchResponse, summary="Several distinct valid samples (synthetic test data)")
def generate_batch(req: BatchRequest, svc: Compiler, cfg: Config, engine: Eng) -> BatchResponse:
    """Runs constrained generation with different seeds until `count` DISTINCT strings that end in an accepting
    state are found (at most 3x`count` attempts, within the time limit). Useful as synthetic test data for a
    validator: every sample is guaranteed to match."""
    check_common(svc, cfg, req.prompt, req.max_tokens)
    if req.count > cfg.max_batch:
        raise LimitError(f"count is limited to {cfg.max_batch}")
    dfa = svc.resolve(req.constraint).dfa
    expression = as_expression(req.constraint)
    cancel = CancelToken()
    timer = threading.Timer(cfg.generation_timeout, cancel.cancel)
    timer.start()
    samples: list[BatchSample] = []
    seen: set[str] = set()
    attempts = 0
    try:
        while len(samples) < req.count and attempts < req.count * 3 and not cancel():
            seed = req.seed + attempts
            attempts += 1
            gen = engine.constrained(
                dfa,
                req.prompt,
                cancel=cancel,
                max_tokens=req.max_tokens,
                top_k=req.top_k,
                temperature=req.temperature,
                seed=seed,
            )
            text, steps, reason = collect(gen)
            if reason != "eos" or text in seen or not dfa.accepts(text):
                continue
            seen.add(text)
            cert = None
            if req.certificates:
                c = make_dfa_certificate(dfa, expression, text, [s.token_text for s in steps])
                c.names = dict(req.constraint.names)
                cert = c.to_dict()
            bits = summarize(steps).get("total_cost_bits", 0.0)
            samples.append(BatchSample(text=text, seed=seed, cost_bits=float(bits), certificate=cert))
    finally:
        timer.cancel()
    note = None
    if len(samples) < req.count:
        note = "fewer distinct valid samples than requested were found within the attempt/time budget"
    return BatchResponse(samples=samples, requested=req.count, attempts=attempts, note=note)
