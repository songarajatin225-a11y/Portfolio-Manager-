"""Password hashing, JWT access tokens, opaque refresh tokens, WebSocket tickets and CSRF tokens."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

_hasher = PasswordHasher()  # argon2id with library defaults (OWASP-compliant)
JWT_ALGORITHM = "HS256"

ACCESS_COOKIE = "mi_access"
REFRESH_COOKIE = "mi_refresh"
CSRF_COOKIE = "mi_csrf"
CSRF_HEADER = "x-csrf-token"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str) -> list[str]:
    problems: list[str] = []
    if len(password) < 10:
        problems.append("Password must be at least 10 characters long")
    if password.lower() == password or password.upper() == password:
        problems.append("Password must mix upper and lower case letters")
    if not any(c.isdigit() for c in password):
        problems.append("Password must contain a digit")
    return problems


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, get_settings().secret_key, algorithm=JWT_ALGORITHM)


def create_access_token(user_id: uuid.UUID, role: str) -> str:
    now = datetime.now(UTC)
    return _encode(
        {
            "sub": str(user_id),
            "role": role,
            "type": "access",
            "iat": now,
            "exp": now + timedelta(minutes=get_settings().access_token_ttl_minutes),
            "jti": uuid.uuid4().hex,
        }
    )


def create_ws_ticket(user_id: uuid.UUID, role: str) -> str:
    now = datetime.now(UTC)
    return _encode(
        {
            "sub": str(user_id),
            "role": role,
            "type": "ws",
            "iat": now,
            "exp": now + timedelta(seconds=get_settings().ws_ticket_ttl_seconds),
            "jti": uuid.uuid4().hex,
        }
    )


class TokenError(Exception):
    pass


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            get_settings().secret_key,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "type"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid or expired token") from exc
    if payload.get("type") != expected_type:
        raise TokenError("Wrong token type")
    return payload


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def constant_time_equals(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())
