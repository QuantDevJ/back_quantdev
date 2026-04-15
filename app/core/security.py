import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings
from app.privacy.encryption import decrypt_email as decrypt_email_bytes
from app.privacy.encryption import encrypt_email as encrypt_email_bytes

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def hash_email(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def generate_password_reset_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_password_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def encrypt_email(email: str) -> Optional[bytes]:
    return encrypt_email_bytes(email)


def decrypt_email(encrypted_value: bytes) -> Optional[str]:
    return decrypt_email_bytes(encrypted_value)


def create_token(subject: str, token_type: str, expires_delta: timedelta, extra: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str) -> str:
    return create_token(
        subject=user_id,
        token_type="access",
        expires_delta=timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: str) -> str:
    return create_token(
        subject=user_id,
        token_type="refresh",
        expires_delta=timedelta(days=settings.refresh_token_expire_days),
    )
