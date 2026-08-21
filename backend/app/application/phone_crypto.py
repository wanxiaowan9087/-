from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError as exc:  # pragma: no cover - exercised by deployment checks
    AESGCM = None  # type: ignore[assignment,misc]
    _CRYPTO_IMPORT_ERROR = exc
else:
    _CRYPTO_IMPORT_ERROR = None


_MAINLAND_PHONE = re.compile(r"^1[3-9]\d{9}$")


def normalize_mainland_phone(phone: str) -> str:
    """Return an E.164 mainland China phone number or raise ValueError."""
    value = phone.strip().replace(" ", "").replace("-", "")
    if value.startswith("+86"):
        value = value[3:]
    elif value.startswith("0086"):
        value = value[4:]
    if not _MAINLAND_PHONE.fullmatch(value):
        raise ValueError("invalid mainland China mobile phone")
    return "+86" + value


def _key(value: str, *, name: str) -> bytes:
    raw = value.encode("utf-8")
    # Production values should be base64 encoded; accepting raw test keys keeps
    # the adapter easy to use without ever weakening the cryptographic primitive.
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        decoded = raw
    if len(decoded) not in {16, 24, 32}:
        raise ValueError(f"{name} must decode to 16, 24, or 32 bytes")
    return decoded


@dataclass(frozen=True, slots=True)
class PhoneProtector:
    encryption_key: bytes
    lookup_key: bytes
    key_version: str = "v1"

    def __init__(self, encryption_key: str | bytes, lookup_key: str | bytes, key_version: str = "v1") -> None:
        if AESGCM is None:  # pragma: no cover
            raise RuntimeError("cryptography is required for phone protection") from _CRYPTO_IMPORT_ERROR
        encryption = encryption_key if isinstance(encryption_key, bytes) else _key(encryption_key, name="phone encryption key")
        lookup = lookup_key if isinstance(lookup_key, bytes) else lookup_key.encode("utf-8")
        if len(encryption) not in {16, 24, 32}:
            raise ValueError("phone encryption key must be 16, 24, or 32 bytes")
        if len(lookup) < 32:
            raise ValueError("phone lookup HMAC key must be at least 32 bytes")
        object.__setattr__(self, "encryption_key", encryption)
        object.__setattr__(self, "lookup_key", lookup)
        object.__setattr__(self, "key_version", key_version)

    def encrypt(self, phone: str) -> str:
        normalized = normalize_mainland_phone(phone)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.encryption_key).encrypt(nonce, normalized.encode(), self.key_version.encode())
        return f"{self.key_version}:{base64.urlsafe_b64encode(nonce + ciphertext).decode('ascii')}"

    def decrypt(self, value: str) -> str:
        try:
            version, encoded = value.split(":", 1)
            payload = base64.urlsafe_b64decode(encoded.encode("ascii"))
            nonce, ciphertext = payload[:12], payload[12:]
            plain = AESGCM(self.encryption_key).decrypt(nonce, ciphertext, version.encode())
            return normalize_mainland_phone(plain.decode("utf-8"))
        except Exception as exc:
            raise ValueError("invalid encrypted phone") from exc

    def lookup_digest(self, phone: str) -> str:
        normalized = normalize_mainland_phone(phone)
        return hmac.new(self.lookup_key, normalized.encode("ascii"), hashlib.sha256).hexdigest()

    def redact(self, phone: str) -> str:
        normalized = normalize_mainland_phone(phone)[3:]
        return normalized[:3] + "****" + normalized[-4:]
