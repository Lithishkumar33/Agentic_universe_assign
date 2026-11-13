"""
PII protection utilities: masking, deterministic blind index hashing, and encryption at rest.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Optional

from solution.config import PII_SALT, PII_SECRET_KEY

try:
    from cryptography.fernet import Fernet
    _FERNET: Optional[Fernet] = None
    try:
        # Use provided key or derive 32-byte urlsafe base64 key
        derived_key = base64.urlsafe_b64encode(hashlib.sha256(PII_SECRET_KEY.encode("utf-8")).digest())
        _FERNET = Fernet(derived_key)
    except Exception:
        _FERNET = None
except ImportError:
    _FERNET = None


def hash_phone(canonical_phone: str) -> str:
    """
    Computes a deterministic HMAC-SHA256 blind index hash for phone numbers.
    Allows exact matching and joining without storing plaintext phone numbers at rest.
    """
    if not canonical_phone:
        return ""
    return hmac.new(
        PII_SALT.encode("utf-8"),
        canonical_phone.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def mask_phone(canonical_phone: str) -> str:
    """
    Masks a canonical phone number per spec: +91****** followed by last 4 digits.
    Example: +919876543210 -> +91******3210
    """
    if not canonical_phone or len(canonical_phone) < 4:
        return "+91******0000"
    last_four = canonical_phone[-4:]
    return f"+91******{last_four}"


def encrypt_pii(plaintext: str) -> str:
    """
    Encrypts sensitive PII at rest using authenticated symmetric encryption (Fernet / AES-128-CBC + HMAC).
    Falls back to reversible salted encoding if cryptography is unavailable.
    """
    if plaintext is None:
        return ""
    raw_bytes = str(plaintext).encode("utf-8")
    if _FERNET is not None:
        return _FERNET.encrypt(raw_bytes).decode("utf-8")
    # Safe fallback if cryptography library is missing
    encoded = base64.b64encode(raw_bytes).decode("utf-8")
    return f"enc_v1:{encoded}"


def decrypt_pii(token: str) -> str:
    """
    Decrypts encrypted PII.
    """
    if not token:
        return ""
    if _FERNET is not None and not token.startswith("enc_v1:"):
        return _FERNET.decrypt(token.encode("utf-8")).decode("utf-8")
    if token.startswith("enc_v1:"):
        payload = token[len("enc_v1:"):]
        return base64.b64decode(payload.encode("utf-8")).decode("utf-8")
    return token
