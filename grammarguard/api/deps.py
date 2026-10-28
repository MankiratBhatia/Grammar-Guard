"""FastAPI dependencies: shared objects live on app.state (so tests can swap them)."""

from __future__ import annotations

from fastapi import Request

from grammarguard.api.services.compiler import CompileService
from grammarguard.api.settings import Settings
from grammarguard.decoding.engine import Engine


def get_settings(request: Request) -> Settings:
    """The server settings."""
    return request.app.state.settings


def get_compiler(request: Request) -> CompileService:
    """The compile service (limits + LRU cache)."""
    return request.app.state.compiler


def get_engine(request: Request) -> Engine:
    """The shared language-model engine (a fake one in fast tests)."""
    return request.app.state.engine
