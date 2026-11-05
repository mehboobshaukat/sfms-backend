"""Validation and local storage helpers for optional user profile images."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import settings


_ALLOWED_TYPES = {
    "image/jpeg": ("jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": ("png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": ("webp", lambda data: len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"),
}


async def save_profile_image(upload: UploadFile) -> str:
    """Validate JPEG/PNG/WebP content and return the public relative URL."""
    content_type = (upload.content_type or "").lower()
    spec = _ALLOWED_TYPES.get(content_type)
    if not spec:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Profile image must be JPEG, PNG, or WebP.",
        )

    max_bytes = max(1, settings.MAX_PROFILE_IMAGE_MB) * 1024 * 1024
    data = await upload.read(max_bytes + 1)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Profile image is empty.")
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Profile image must be {settings.MAX_PROFILE_IMAGE_MB} MB or smaller.",
        )

    extension, signature_check = spec
    if not signature_check(data):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Profile image content is invalid.")

    relative_dir = Path("profile_images")
    root = Path(settings.MEDIA_ROOT)
    destination_dir = root / relative_dir
    destination_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{uuid4().hex}.{extension}"
    (destination_dir / filename).write_bytes(data)

    media_url = settings.MEDIA_URL.rstrip("/")
    return f"{media_url}/{relative_dir.as_posix()}/{filename}"


def delete_profile_image(profile_image_url: str | None) -> None:
    """Best-effort deletion for images stored by save_profile_image()."""
    if not profile_image_url:
        return

    media_prefix = settings.MEDIA_URL.rstrip("/") + "/"
    if not profile_image_url.startswith(media_prefix):
        return

    relative = profile_image_url[len(media_prefix):]
    path = (Path(settings.MEDIA_ROOT) / relative).resolve()
    root = Path(settings.MEDIA_ROOT).resolve()

    try:
        path.relative_to(root)
    except ValueError:
        return

    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
