"""Benchmark results produced by `python -m grammarguard.eval`."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from grammarguard.api.deps import get_settings
from grammarguard.api.errors import ApiError
from grammarguard.api.settings import Settings

router = APIRouter(prefix="/benchmarks", tags=["benchmarks"])


@router.get("", summary="Evaluation results: validity, cost and speed of constrained vs unconstrained decoding")
def benchmarks(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    """The full results document (`meta`, `cases`, `runs`, `summary`) from the last benchmark run.
    Every run, including the generated text, is included so the numbers can be audited."""
    path = settings.benchmarks_path
    if not path.is_file():
        raise ApiError(
            "not_found",
            "No benchmark results yet. Generate them with: python -m grammarguard.eval",
            status=404,
        )
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ApiError("internal", f"benchmark results could not be read: {e}", status=500) from None
