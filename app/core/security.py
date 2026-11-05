"""
Password hashing (Argon2) and JWT access/refresh token helpers.
"""
from datetime import datetime, timedelta, timezone
from typing import Any
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

# pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
ph = PasswordHasher()

# ---------------------------------------------------------------- #
# Password hashing
# ---------------------------------------------------------------- #
def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return ph.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        return False


# ---------------------------------------------------------------- #
# JWT tokens
# ---------------------------------------------------------------- #
def _create_token(subject: str, expires_delta: timedelta, token_type: str, extra: dict | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: int) -> str:
    return _create_token(
        subject=str(user_id),
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        token_type="access",
    )


def create_refresh_token(user_id: int, jti: str) -> str:
    return _create_token(
        subject=str(user_id),
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        token_type="refresh",
        extra={"jti": jti},
    )


def decode_token(token: str) -> dict:
    """Raises jose.JWTError if invalid/expired."""
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
