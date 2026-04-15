from fastapi import Request
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.responses import error_response


class JWTAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.public_paths = {
            "/",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/v1/auth/signup",
            "/v1/auth/login",
            "/v1/auth/forgot-password",
            "/v1/auth/reset-password",
            "/v1/auth/refresh",
            "/v1/plaid/webhook",
        }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        normalized_public = {p.rstrip("/") or "/" for p in self.public_paths}
        if not path.startswith("/v1"):
            return await call_next(request)
        if path in normalized_public:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content=error_response("UNAUTHORIZED", "Missing Bearer token"))

        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            if payload.get("type") != "access":
                raise ValueError("Invalid token type")
        except (JWTError, ValueError):
            return JSONResponse(status_code=401, content=error_response("UNAUTHORIZED", "Invalid authentication token"))

        request.state.user_id = payload.get("sub")
        return await call_next(request)
