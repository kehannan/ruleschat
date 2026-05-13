"""Save user-uploaded images (data URLs) for chat conversations."""
import base64
import re
import uuid
from pathlib import Path

UPLOADS_DIR = Path("data/uploads")
ALLOWED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
_DATA_URL_RE = re.compile(r"^data:(image/[a-z+]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_EXT_FOR_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


class ImageValidationError(ValueError):
    pass


def save_image_data_url(data_url: str, conversation_id: int) -> str:
    """Decode + validate + write to disk. Returns relative path under UPLOADS_DIR."""
    m = _DATA_URL_RE.match(data_url.strip())
    if not m:
        raise ImageValidationError("Invalid image data URL")
    mime = m.group(1).lower()
    if mime not in ALLOWED_IMAGE_MIMES:
        raise ImageValidationError(f"Unsupported image type: {mime}")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except Exception as e:
        raise ImageValidationError(f"Invalid base64 image data: {e}")
    if len(raw) > MAX_IMAGE_BYTES:
        raise ImageValidationError(
            f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit"
        )
    conv_dir = UPLOADS_DIR / str(conversation_id)
    conv_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}.{_EXT_FOR_MIME[mime]}"
    fpath = conv_dir / fname
    fpath.write_bytes(raw)
    return f"{conversation_id}/{fname}"


def resolve_image_path(rel_path: str) -> Path:
    """Map stored relative path back to disk; raises if outside UPLOADS_DIR."""
    fpath = (UPLOADS_DIR / rel_path).resolve()
    base = UPLOADS_DIR.resolve()
    if base not in fpath.parents and fpath != base:
        raise ImageValidationError("Path escapes uploads directory")
    return fpath
