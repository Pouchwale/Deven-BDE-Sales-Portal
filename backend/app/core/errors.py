"""One error shape for the whole API.

Every failure - raised by us, by FastAPI's validation, or by an unhandled
exception - leaves as:

    {"error": {"code": "ROLE_ABOVE_ACTOR", "message": "...", "details": {...}}}

so the frontend has exactly one branch to write.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.constants import ErrorCode


class ApiError(Exception):
    """A failure with a machine-readable code."""

    def __init__(
        self,
        code: ErrorCode | str,
        message: str,
        *,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


# ------------------------------------------------ the failures we raise a lot
def unauthorized(message: str = "Not authenticated") -> ApiError:
    return ApiError(
        ErrorCode.UNAUTHORIZED, message, status_code=status.HTTP_401_UNAUTHORIZED
    )


def forbidden(
    message: str,
    code: ErrorCode | str = ErrorCode.FORBIDDEN,
    **details: Any,
) -> ApiError:
    return ApiError(code, message, status_code=status.HTTP_403_FORBIDDEN, details=details)


def not_found(message: str = "Not found") -> ApiError:
    """Used for "exists but you may not see it" as well as "does not exist".

    Returning 404 rather than 403 for an invisible record stops ids being
    probed for existence across the hierarchy.
    """
    return ApiError(ErrorCode.NOT_FOUND, message, status_code=status.HTTP_404_NOT_FOUND)


def conflict(
    message: str, code: ErrorCode | str = ErrorCode.CONFLICT, **details: Any
) -> ApiError:
    return ApiError(code, message, status_code=status.HTTP_409_CONFLICT, details=details)


def invalid(
    message: str, code: ErrorCode | str = ErrorCode.VALIDATION_ERROR, **details: Any
) -> ApiError:
    # The literal 422 rather than the constant: Starlette renamed
    # HTTP_422_UNPROCESSABLE_ENTITY to ..._CONTENT and deprecated the old
    # name, so the number is the version-independent spelling.
    return ApiError(code, message, status_code=422, details=details)


def _envelope(code: str, message: str, details: dict[str, Any] | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            status.HTTP_401_UNAUTHORIZED: ErrorCode.UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN: ErrorCode.FORBIDDEN,
            status.HTTP_404_NOT_FOUND: ErrorCode.NOT_FOUND,
            status.HTTP_409_CONFLICT: ErrorCode.CONFLICT,
        }.get(exc.status_code, ErrorCode.VALIDATION_ERROR)
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(str(code), str(exc.detail)),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_envelope(
                str(ErrorCode.VALIDATION_ERROR),
                "The request body failed validation.",
                # jsonable so a ValueError in a validator cannot break the
                # error response itself.
                {"fields": [
                    {
                        "location": ".".join(str(p) for p in err.get("loc", ())),
                        "message": err.get("msg", ""),
                    }
                    for err in exc.errors()
                ]},
            ),
        )
