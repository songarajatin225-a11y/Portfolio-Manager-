"""Symmetric encryption for secrets stored in the database (provider credentials, bot tokens).

Uses Fernet (AES-128-CBC + HMAC-SHA256). In production ``SECRET_ENCRYPTION_KEY`` is required.
In development/test a key is derived from ``SECRET_KEY`` so the app works out of the box.
"""

from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class SecretDecryptionError(Exception):
    """Raised when stored ciphertext cannot be decrypted (wrong key or tampering)."""


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    if settings.secret_encryption_key:
        return Fernet(settings.secret_encryption_key.encode())
    logger.warning("SECRET_ENCRYPTION_KEY not set - deriving a development key from SECRET_KEY")
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"marketintel-dev", info=b"fernet").derive(
        settings.secret_key.encode()
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_json(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data, sort_keys=True).encode()).decode()


def decrypt_json(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:  # pragma: no cover - defensive
        raise SecretDecryptionError("Stored secret could not be decrypted") from exc


def mask_secret(value: str | None) -> str | None:
    """Return a masked representation safe to send to the browser."""
    if not value:
        return None
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


def generate_key() -> str:
    return Fernet.generate_key().decode()
