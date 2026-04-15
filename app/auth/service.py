from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from jose import JWTError, jwt
from sqlalchemy.orm import Session
from starlette import status

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.security import (
    create_access_token,
    create_refresh_token,
    encrypt_email,
    generate_password_reset_secret,
    hash_email,
    hash_password,
    hash_password_reset_token,
    verify_password,
)
from app.db.models import PasswordResetToken, User


class UserRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_email_hash(self, email_hash: str) -> Optional[User]:
        return self.db.query(User).filter(User.email_hash == email_hash).first()

    def get_active_by_id(self, user_id: UUID) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()

    def get_active_by_id_str(self, user_id: str) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()

    def create_user(self, *, email_hash: str, email_encrypted: Optional[bytes], settings_json: dict) -> User:
        user = User(email_hash=email_hash, email_encrypted=email_encrypted, settings_json=settings_json)
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user

    def update_user(self, user: User) -> User:
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user


class AuthService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = UserRepository(db)

    def signup(self, *, email: str, password: str, name: Optional[str] = None) -> Dict[str, Any]:
        email_hash_value = hash_email(email)
        existing = self.repo.get_by_email_hash(email_hash_value)
        if existing:
            raise AppException(
                code="VALIDATION_ERROR",
                message="Email already registered",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=[{"field": "email", "issue": "unique constraint"}],
            )

        settings_json = {"password_hash": hash_password(password)}
        if name:
            settings_json["name"] = name
        user = self.repo.create_user(
            email_hash=email_hash_value,
            email_encrypted=encrypt_email(email),
            settings_json=settings_json,
        )
        return {
            "user_id": str(user.id),
            "email_hash": user.email_hash,
            "created_at": user.created_at.isoformat() + "Z" if user.created_at else None,
            "access_token": create_access_token(str(user.id)),
            "refresh_token": create_refresh_token(str(user.id)),
        }

    def login(self, *, email: str, password: str) -> Dict[str, Any]:
        email_hash_value = hash_email(email)
        user = self.repo.get_by_email_hash(email_hash_value)
        if not user:
            raise AppException(
                code="NOT_FOUND",
                message="User not found",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        if not user.is_active:
            raise AppException(
                code="UNAUTHORIZED",
                message="Invalid credentials",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        password_hash = (user.settings_json or {}).get("password_hash")
        if not password_hash or not verify_password(password, password_hash):
            raise AppException(
                code="UNAUTHORIZED",
                message="Invalid credentials",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.repo.update_user(user)

        return {
            "user_id": str(user.id),
            "access_token": create_access_token(str(user.id)),
            "refresh_token": create_refresh_token(str(user.id)),
            "expires_in": settings.access_token_expire_minutes * 60,
        }

    def refresh(self, *, refresh_token: str) -> Dict[str, Any]:
        try:
            payload = jwt.decode(refresh_token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
            if payload.get("type") != "refresh":
                raise ValueError("Invalid token type")
            user_id = payload["sub"]
        except (JWTError, KeyError, ValueError):
            raise AppException(
                code="UNAUTHORIZED",
                message="Invalid refresh token",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        user = self.repo.get_active_by_id_str(user_id)
        if not user:
            raise AppException(
                code="UNAUTHORIZED",
                message="Invalid refresh token",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return {
            "user_id": str(user.id),
            "access_token": create_access_token(str(user.id)),
            "refresh_token": create_refresh_token(str(user.id)),
            "expires_in": settings.access_token_expire_minutes * 60,
        }

    def forgot_password(
        self,
        *,
        email: str,
        client_ip: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Dict[str, Any]:
        email_hash_value = hash_email(email)
        user = self.repo.get_by_email_hash(email_hash_value)
        if not user or not user.is_active:
            return {"message": "If an account exists, a reset link has been sent."}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        ).delete(synchronize_session=False)

        raw_token = generate_password_reset_secret()
        token_hash = hash_password_reset_token(raw_token)
        expires_at = now + timedelta(minutes=settings.reset_token_expire_minutes)
        row = PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
            request_ip=client_ip,
            user_agent=user_agent,
        )
        self.db.add(row)
        self.db.commit()

        if settings.app_debug:
            return {"message": "If an account exists, a reset link has been sent.", "reset_token": raw_token}
        return {"message": "If an account exists, a reset link has been sent."}

    def reset_password(self, *, token: str, new_password: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        token_hash = hash_password_reset_token(token)
        row = (
            self.db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.token_hash == token_hash,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.expires_at > now,
            )
            .first()
        )
        if not row:
            raise AppException(
                code="VALIDATION_ERROR",
                message="Invalid or expired reset token",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        user = self.repo.get_active_by_id(row.user_id)
        if not user:
            raise AppException(
                code="VALIDATION_ERROR",
                message="Invalid or expired reset token",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        current_hash = (user.settings_json or {}).get("password_hash")
        if current_hash and verify_password(new_password, current_hash):
            raise AppException(
                code="VALIDATION_ERROR",
                message="New password must be different from your current password",
                status_code=status.HTTP_400_BAD_REQUEST,
                details=[{"field": "new_password", "issue": "must not match existing password"}],
            )

        settings_json = dict(user.settings_json or {})
        settings_json["password_hash"] = hash_password(new_password)
        user.settings_json = settings_json
        row.used_at = now
        self.db.add(user)
        self.db.add(row)
        self.db.commit()
        return {"message": "Password reset successful"}

    def me(self, user: User) -> Dict[str, Any]:
        return {"user_id": str(user.id), "is_active": user.is_active}
