from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.auth.schemas import ForgotPasswordRequest, LoginRequest, RefreshTokenRequest, ResetPasswordRequest, SignupRequest
from app.auth.service import AuthService
from app.core.config import settings
from app.core.exceptions import AppException
from app.core.responses import success_response
from app.db.database import SessionLocal
from app.db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("type") != "access":
            raise ValueError("Invalid token type")
        user_id = UUID(payload["sub"])
    except (JWTError, KeyError, ValueError):
        raise AppException(code="UNAUTHORIZED", message="Invalid authentication token", status_code=401)

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user:
        raise AppException(code="UNAUTHORIZED", message="User not found or inactive", status_code=401)
    return user


@router.post("/signup", status_code=201)
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> JSONResponse:
    data = AuthService(db).signup(email=payload.email, password=payload.password, name=payload.name)
    body, status_code = success_response(data, status_code=201)
    return JSONResponse(content=body, status_code=status_code)


@router.post("/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> JSONResponse:
    data = AuthService(db).login(email=payload.email, password=payload.password)
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.post("/refresh")
def refresh(payload: RefreshTokenRequest, db: Session = Depends(get_db)) -> JSONResponse:
    data = AuthService(db).refresh(refresh_token=payload.refresh_token)
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.post("/forgot-password")
def forgot_password(
    request: Request,
    payload: ForgotPasswordRequest,
    db: Session = Depends(get_db),
) -> JSONResponse:
    client = request.client
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    else:
        client_ip = client.host if client else None
    user_agent = request.headers.get("user-agent")
    data = AuthService(db).forgot_password(
        email=payload.email,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)) -> JSONResponse:
    data = AuthService(db).reset_password(token=payload.token, new_password=payload.new_password)
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)


@router.get("/me")
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> JSONResponse:
    data = AuthService(db).me(current_user)
    body, status_code = success_response(data)
    return JSONResponse(content=body, status_code=status_code)
