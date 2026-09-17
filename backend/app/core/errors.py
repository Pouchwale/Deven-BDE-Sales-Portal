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
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.constants import ErrorCode
from app.core.logging import get_logger, request_id_var


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


INTERNAL_ERROR_CODE = "INTERNAL_ERROR"
INTERNAL_ERROR_MESSAGE = "Something went wrong. Please try again."
SERVICE_UNAVAILABLE_CODE = "SERVICE_UNAVAILABLE"

_log = get_logger("app.errors")


def internal_error_body(request_id: str | None) -> dict:
    """The body of every unexpected 500. Deliberately says nothing about the
    failure: the request id is the handle to the server-side traceback."""
    details = {"request_id": request_id} if request_id and request_id != "-" else {}
    return _envelope(INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE, details)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or request_id_var.get()


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(IntegrityError)
    async def _integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        # The constraint name and SQL stay in the log; the client learns only
        # that its change collided with existing data.
        _log.warning(
            "database integrity error: %s", type(exc.orig).__name__ if exc.orig else "unknown"
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=_envelope(
                str(ErrorCode.CONFLICT),
                "That change conflicts with existing data. Refresh and try again.",
                {"request_id": _request_id(request)},
            ),
        )

    @app.exception_handler(OperationalError)
    async def _operational_error(request: Request, exc: OperationalError) -> JSONResponse:
        _log.error("database unavailable", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=_envelope(
                SERVICE_UNAVAILABLE_CODE,
                "The service is temporarily unavailable. Please try again shortly.",
                {"request_id": _request_id(request)},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Normally unreachable: RequestContextMiddleware catches unhandled
        # exceptions first so the 500 still carries the security headers.
        # Kept so an app built without that middleware is equally safe.
        _log.error("unhandled exception", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=internal_error_body(_request_id(request)),
        )

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
