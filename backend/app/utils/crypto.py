"""Symmetric encryption for sensitive credentials stored in the DB.

Uses Fernet (AES-128-CBC + HMAC-SHA256) from the `cryptography` library.
Encryption key is read from the CREDENTIAL_ENCRYPTION_KEY env var.
If the env var is unset, encrypt/decrypt are no-ops (plaintext passthrough)
so existing deployments continue working until the key is configured.
"""

import logging
import os

logger = logging.getLogger(__name__)

_FERNET_KEY = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "")
_fernet = None

if _FERNET_KEY:
    try:
        from cryptography.fernet import Fernet
        _fernet = Fernet(_FERNET_KEY.encode() if isinstance(_FERNET_KEY, str) else _FERNET_KEY)
    except Exception as e:
        logger.error(f"Invalid CREDENTIAL_ENCRYPTION_KEY: {e}")


def encrypt_value(plaintext: str | None) -> str | None:
    """Encrypt a string value. Returns ciphertext or plaintext if no key configured."""
    if not plaintext:
        return plaintext
    if not _fernet:
        return plaintext
    try:
        return _fernet.encrypt(plaintext.encode()).decode()
    except Exception:
        return plaintext


def decrypt_value(ciphertext: str | None) -> str | None:
    """Decrypt a string value. Returns plaintext or the original string if not encrypted."""
    if not ciphertext:
        return ciphertext
    if not _fernet:
        return ciphertext
    # Fernet tokens start with "gAAAAA" — skip decryption for plaintext values
    if not ciphertext.startswith("gAAAAA"):
        return ciphertext
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except Exception:
        return ciphertext


def is_encrypted(value: str | None) -> bool:
    """Check if a value looks like a Fernet-encrypted token."""
    return bool(value and value.startswith("gAAAAA"))
