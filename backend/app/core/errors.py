from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from backend.app.core.context import request_id_var

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class AppError(Exception):
    code: str
    message: str
    status_code: int
    data: dict[str, Any] | None = field(default=None)
    headers: dict[str, str] | None = field(default=None)


def error_payload(error: AppError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": error.message,
        "data": error.data,
        "request_id": request_id_var.get(),
    }


async def app_error_handler(_: Request, error: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error_payload(error),
        headers=error.headers,
    )


async def validation_error_handler(_: Request, error: RequestValidationError) -> JSONResponse:
    issues = [
        {"field": ".".join(str(part) for part in item["loc"]), "reason": item["msg"]}
        for item in error.errors()[:50]
    ]
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "request validation failed",
            "data": {"retryable": False, "issues": issues},
            "request_id": request_id_var.get(),
        },
    )


async def internal_error_handler(_: Request, error: Exception) -> JSONResponse:
    logger.exception("unhandled application error", exc_info=error)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "internal server error",
            "data": {"retryable": False},
            "request_id": request_id_var.get(),
        },
    )


def not_found() -> AppError:
    return AppError("NOT_FOUND", "resource not found", 404)


def conflict(message: str = "resource state conflict") -> AppError:
    return AppError("CONFLICT", message, 409)
