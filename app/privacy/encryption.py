import base64
import binascii
import hashlib
import os
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from starlette import status

from app.core.config import settings
from app.core.exceptions import AppException


def _decode_32_byte_key(raw_key: str, *, label: str) -> bytes:
    """Decode a standard or url-safe base64 key; must be exactly 32 bytes (AES-256)."""
    normalized = "".join(raw_key.split())
    pad = (-len(normalized)) % 4
    if pad:
        normalized += "=" * pad

    decoded: Optional[bytes] = None
    last_err: Optional[Exception] = None
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            decoded = decoder(normalized, validate=False)
            break
        except binascii.Error as e:
            last_err = e
            decoded = None

    if decoded is None:
        raise AppException(
            code="CONFIG_ERROR",
            message=(
                f"{label} is not valid base64. "
                "Use a 32-byte key, e.g. "
                'python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"'
            ),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        ) from last_err

    if len(decoded) != 32:
        raise AppException(
            code="CONFIG_ERROR",
            message=(
                f"{label} must decode to 32 bytes (got {len(decoded)}). "
                'Generate one: python -c "import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())"'
            ),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return decoded


def _dev_derived_aes_key() -> bytes:
    """Deterministic AES-256 key from JWT secret — only when app_debug is True (local dev)."""
    return hashlib.sha256(settings.jwt_secret_key.encode("utf-8")).digest()


def _get_storage_key() -> Optional[bytes]:
    """
    Key for AES-GCM storage (emails at rest, Plaid access tokens, etc.).

    Resolution order:
    1. EMAIL_ENCRYPTION_KEY (if set)
    2. DATA_ENCRYPTION_KEY (if set)
    3. If app_debug: derive from jwt_secret_key (development convenience)
    4. Otherwise None (email encryption skipped; Plaid exchange must use explicit key)
    """
    if settings.email_encryption_key:
        return _decode_32_byte_key(
            settings.email_encryption_key,
            label="EMAIL_ENCRYPTION_KEY",
        )
    if settings.data_encryption_key:
        return _decode_32_byte_key(
            settings.data_encryption_key,
            label="DATA_ENCRYPTION_KEY",
        )
    if settings.app_debug:
        return _dev_derived_aes_key()
    return None


def encrypt_email(email: str) -> Optional[bytes]:
    key = _get_storage_key()
    if key is None:
        return None
    aes = AESGCM(key)
    nonce = os.urandom(12)
    ciphertext = aes.encrypt(nonce, email.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt_email(encrypted_value: bytes) -> Optional[str]:
    key = _get_storage_key()
    if key is None:
        return None
    nonce = encrypted_value[:12]
    ciphertext = encrypted_value[12:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ciphertext, None).decode("utf-8")
