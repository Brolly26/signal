"""Error responses.

One shape for every failure, so the frontend has a single thing to parse:

    {"error": {"code": "...", "message": "...", "fields": {"title": "..."}}}

`fields` is present only for validation failures. Internal errors never carry a
driver message or an exception string: a database error can contain the DSN, and
that must not reach a client or a log line.
"""

import logging
from typing import Any

from asyncpg.exceptions import CheckViolationError, PostgresError, UniqueViolationError
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def error_body(code: str, message: str, fields: dict[str, str] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return {"error": error}


def _field_name(location: tuple[Any, ...]) -> str:
    """Turn a Pydantic error location into a field name the UI can use."""
    parts = [str(part) for part in location if part not in ("body", "query", "path")]
    return ".".join(parts) if parts else "_"


async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    fields: dict[str, str] = {}
    for error in exc.errors():
        name = _field_name(error.get("loc", ()))
        # Keep the first message per field: the UI shows one per input.
        fields.setdefault(name, error.get("msg", "Invalid value"))

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_body("validation_error", "Some fields need attention.", fields),
    )


async def handle_integrity_error(request: Request, exc: PostgresError) -> JSONResponse:
    """A constraint rejected the write.

    Reaching this handler means the database caught something the schema layer
    did not, so it is logged as a warning: it points at a gap between the
    Pydantic bounds and the CHECK constraints.
    """
    logger.warning(
        "Database constraint rejected a write on %s (%s)",
        request.url.path,
        type(exc).__name__,
    )
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT
        if isinstance(exc, UniqueViolationError)
        else status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_body(
            "constraint_violation",
            "The request conflicts with a database constraint.",
        ),
    )


async def handle_database_error(request: Request, exc: PostgresError) -> JSONResponse:
    """The database is reachable but failed, or is not reachable at all."""
    logger.exception("Database error on %s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=error_body("database_unavailable", "The service is temporarily unavailable."),
    )


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_body("internal_error", "Something went wrong."),
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(CheckViolationError, handle_integrity_error)
    app.add_exception_handler(UniqueViolationError, handle_integrity_error)
    app.add_exception_handler(PostgresError, handle_database_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
