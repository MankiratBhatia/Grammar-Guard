"""One error shape for the whole API:

    {"error": {"code": "...", "message": "...", "position": 12 | null, "details": ...}}

Codes: regex_syntax, algebra, schema, limit_exceeded, validation, not_found, internal.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from grammarguard.core.algebra import AlgebraError
from grammarguard.core.cfg import GrammarError
from grammarguard.core.dfa import StateLimitError
from grammarguard.core.elimination import NotExpressibleError
from grammarguard.core.regex_parser import RegexSyntaxError
from grammarguard.core.schema import SchemaError

log = logging.getLogger("grammarguard.api")


class ApiError(Exception):
    """A problem with the request itself (never a server bug)."""

    def __init__(self, code: str, message: str, status: int = 422, position: int | None = None, details: Any = None):
        """Machine-readable code, human message, optional character position."""
        super().__init__(message)
        self.code, self.message, self.status, self.position, self.details = code, message, status, position, details


class LimitError(ApiError):
    """An input exceeds a configured limit."""

    def __init__(self, message: str, details: Any = None):
        """Always a 422 with code limit_exceeded."""
        super().__init__("limit_exceeded", message, 422, details=details)


def body(code: str, message: str, position: int | None = None, details: Any = None) -> dict[str, Any]:
    """The JSON error envelope."""
    return {"error": {"code": code, "message": message, "position": position, "details": details}}


def _position(message: str) -> int | None:
    """The regex parser puts '(pos N)' / 'at pos N' in its messages; surface it as a field."""
    m = re.search(r"pos (\d+)", message)
    return int(m.group(1)) if m else None


def install_error_handlers(app: FastAPI) -> None:
    """Map library exceptions to the error envelope."""

    def reply(status: int, code: str, message: str, position: int | None = None, details: Any = None):
        return JSONResponse(status_code=status, content=body(code, message, position, details))

    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError):
        return reply(exc.status, exc.code, exc.message, exc.position, exc.details)

    @app.exception_handler(RegexSyntaxError)
    async def _regex(_: Request, exc: RegexSyntaxError):
        return reply(422, "regex_syntax", str(exc), _position(str(exc)))

    @app.exception_handler(AlgebraError)
    async def _algebra(_: Request, exc: AlgebraError):
        return reply(422, "algebra", str(exc), exc.pos)

    @app.exception_handler(StateLimitError)
    async def _states(_: Request, exc: StateLimitError):
        return reply(422, "limit_exceeded", f"{exc}; simplify the pattern", details={"limit": exc.limit})

    @app.exception_handler(SchemaError)
    @app.exception_handler(GrammarError)
    async def _schema(_: Request, exc: Exception):
        return reply(422, "schema", str(exc))

    @app.exception_handler(NotExpressibleError)
    async def _not_expressible(_: Request, exc: NotExpressibleError):
        return reply(422, "not_expressible", str(exc))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()
        ]
        first = errors[0] if errors else {"field": "", "message": "invalid request"}
        return reply(422, "validation", f"{first['field']}: {first['message']}".strip(": "), details=errors)

    @app.exception_handler(Exception)
    async def _internal(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return reply(500, "internal", "internal server error")
