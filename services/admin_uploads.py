"""Validated local file storage for admin-panel uploads."""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError


class UploadError(ValueError):
    """Raised when an uploaded file violates panel storage policy."""


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
DOCUMENT_EXTENSIONS = {".txt", ".pdf", ".csv", ".json", ".zip", ".jpg", ".jpeg", ".png", ".webp"}


def _read_upload(upload, *, allowed: set[str], max_bytes: int) -> tuple[bytes, str]:
    extension = Path(upload.filename or "").suffix.lower()
    if extension not in allowed:
        raise UploadError("Tipe file tidak diizinkan.")
    content = upload.stream.read(max_bytes + 1)
    if not content:
        raise UploadError("File kosong.")
    if len(content) > max_bytes:
        raise UploadError("Ukuran file melebihi batas.")
    return content, extension


def _persist(content: bytes, root: str, subdirectory: str, extension: str) -> str:
    directory = os.path.abspath(os.path.join(root, subdirectory))
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{uuid.uuid4().hex}{extension}")
    with open(path, "wb") as output:
        output.write(content)
    return path


def save_image(upload, root: str, subdirectory: str, *, max_bytes: int = 5 * 1024 * 1024) -> str:
    content, extension = _read_upload(upload, allowed=IMAGE_EXTENSIONS, max_bytes=max_bytes)
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
    except (UnidentifiedImageError, OSError) as exc:
        raise UploadError("Isi file bukan gambar yang valid.") from exc
    return _persist(content, root, subdirectory, extension)


def save_document(upload, root: str, subdirectory: str, *, max_bytes: int = 20 * 1024 * 1024) -> str:
    content, extension = _read_upload(upload, allowed=DOCUMENT_EXTENSIONS, max_bytes=max_bytes)
    return _persist(content, root, subdirectory, extension)
