from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from backend.app.core.errors import AppError


class AvatarStore:
    """Validate and persist small user avatars behind a single upload seam."""

    _max_bytes = 2 * 1024 * 1024
    _extensions = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }

    def __init__(self, directory: str) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    def save(self, *, user_id: UUID, content_type: str, payload: bytes) -> str:
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        suffix = self._extensions.get(normalized_type)
        if suffix is None or not payload or len(payload) > self._max_bytes:
            raise AppError(
                "VALIDATION_ERROR", "avatar must be a PNG, JPEG, or WebP under 2 MB", 422
            )
        if not _matches_signature(normalized_type, payload):
            raise AppError("VALIDATION_ERROR", "avatar content does not match its image type", 422)
        filename = f"{user_id}-{uuid4().hex}{suffix}"
        (self._directory / filename).write_bytes(payload)
        return f"/uploads/{filename}"


def _matches_signature(content_type: str, payload: bytes) -> bool:
    if content_type == "image/jpeg":
        return payload.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return payload.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return len(payload) >= 12 and payload.startswith(b"RIFF") and payload[8:12] == b"WEBP"
    return False
