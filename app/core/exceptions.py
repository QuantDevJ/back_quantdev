from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette import status

from app.core.responses import error_response


def _clean_validation_issue(raw_msg: str) -> str:
    """Turn Pydantic / FastAPI messages into short, user-facing copy."""
    msg = (raw_msg or "").strip()
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, ") :].strip()
    return msg


def _validation_details_from_pydantic(errors: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    details: List[Dict[str, str]] = []
    for err in errors:
        loc = [str(part) for part in err.get("loc", ()) if part not in ("body",)]
        field = ".".join(loc) if loc else "request"
        issue = _clean_validation_issue(str(err.get("msg", "Invalid value.")))
        details.append({"field": field, "issue": issue})
    return details


def _validation_summary_message(details: List[Dict[str, str]]) -> str:
    if not details:
        return "Invalid request. Please check your input."
    if len(details) == 1:
        return details[0]["issue"]
    return "Please correct the following and try again."


class AppException(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[List[Dict]] = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppException)
    async def app_exception_handler(_: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = _validation_details_from_pydantic(exc.errors())
        message = _validation_summary_message(details)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_response("VALIDATION_ERROR", message, details),
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response("HTTP_ERROR", message),
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_response("INTERNAL_SERVER_ERROR", "Unexpected server error"),
        )
