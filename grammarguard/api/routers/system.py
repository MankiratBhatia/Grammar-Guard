"""Health and model information."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from grammarguard.api import __version__
from grammarguard.api.deps import get_engine, get_settings
from grammarguard.api.schemas import HealthResponse
from grammarguard.api.settings import Settings
from grammarguard.decoding.engine import Engine

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness and model state")
def health(engine: Annotated[Engine, Depends(get_engine)]) -> HealthResponse:
    """Always 200 while the server is up; `engine.loaded` tells whether the language model has finished loading."""
    return HealthResponse(status="ok", version=__version__, engine=engine.stats())


@router.get("/models", summary="Configured model and limits")
def models(
    settings: Annotated[Settings, Depends(get_settings)], engine: Annotated[Engine, Depends(get_engine)]
) -> dict[str, Any]:
    """The model in use and the limits the server enforces (so a UI can validate before sending)."""
    return {
        "model": settings.model_name,
        "loaded": engine.stats()["loaded"],
        "limits": {
            "max_regex_len": settings.max_regex_len,
            "max_expression_len": settings.max_expression_len,
            "max_text_len": settings.max_text_len,
            "max_prompt_len": settings.max_prompt_len,
            "max_states": settings.max_states,
            "max_tokens": settings.max_tokens,
            "max_batch": settings.max_batch,
            "max_count_length": settings.max_count_length,
            "generation_timeout": settings.generation_timeout,
        },
    }
