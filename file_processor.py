"""File attachment processing for CloudGPT chat uploads.

Validates uploads (extension allow-list, size limits per entitlement tier,
magic-byte content sniffing), extracts plain text (TXT/MD/CSV/PDF/DOCX/XLSX/Code),
processes multimodal images with Pillow (stripping EXIF, dimension normalization),
and stages content and raw bytes in Redis under a one-time attachment_id with
a TTL so the chat pipeline and download endpoints can resolve it without re-uploading bytes.
"""

from __future__ import annotations

import base64
import csv
import io
import uuid
import zipfile
from collections import OrderedDict
import threading
import time
from typing import Any

import structlog
from PIL import Image

from config import get_settings
from core.redis_client import redis_client

logger = structlog.get_logger(__name__)

ATTACHMENT_KEY_PREFIX = "cloudgpt:attachment:"


class BoundedTTLCache:
    """Thread-safe bounded in-memory cache with LRU eviction and TTL expiration."""

    def __init__(self, maxsize: int = 200, default_ttl: float = 3600.0) -> None:
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        expiry = time.monotonic() + (ttl if ttl is not None else self._default_ttl)
        with self._lock:
            self._purge_expired_locked()
            if key in self._store:
                self._store.move_to_end(key)
            elif len(self._store) >= self._maxsize:
                self._store.popitem(last=False)
            self._store[key] = (expiry, value)

    def get(self, key: str, default: Any = None) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return default
            expiry, value = entry
            if time.monotonic() > expiry:
                del self._store[key]
                return default
            self._store.move_to_end(key)
            return value

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None

    def pop(self, key: str, default: Any = None) -> Any:
        with self._lock:
            entry = self._store.pop(key, None)
            if entry is None:
                return default
            return entry[1]

    def _purge_expired_locked(self) -> None:
        now = time.monotonic()
        keys_to_del = [k for k, (exp, _) in self._store.items() if now > exp]
        for k in keys_to_del:
            del self._store[k]


_MEMORY_STAGED = BoundedTTLCache(maxsize=50, default_ttl=3600.0)

# Magic-byte signatures for sniffing declared content types (P-04 / security plan).
_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),  # DOCX / XLSX are ZIP containers
    (b"\xd0\xcf\x11\xe0", "application/x-ole-storage"),  # legacy MS Office
    (b"\x1f\x8b", "application/gzip"),
    (b"\x7fELF", "application/x-executable"),
    (b"MZ", "application/x-executable"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"ID3", "audio/mpeg"),
    (b"\xff\xfb", "audio/mpeg"),
    (b"\xff\xf3", "audio/mpeg"),
    (b"OggS", "audio/ogg"),
    (b"RIFF", "audio/wav"),
    (b"\x1a\x45\xdf\xa3", "video/webm"),
)

_MAX_EXTRACTED_CHARS = 200_000  # decompression-bomb / runaway-text guard

IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
AUDIO_EXTENSIONS = {"mp3", "wav", "m4a", "ogg", "webm"}
VIDEO_EXTENSIONS = {"mp4", "mov", "avi", "mkv", "webm"}
CODE_EXTENSIONS = {
    "py", "js", "ts", "json", "yaml", "yml", "tf", "sh", "sql",
    "html", "css", "xml", "env", "dockerfile", "toml", "ini", "c", "cpp", "h", "rs", "go"
}


class AttachmentError(Exception):
    """Raised when an upload fails validation."""


class UnsupportedFileTypeError(AttachmentError):
    """The file's extension or content type is not in the allow-list."""


class FileTooLargeError(AttachmentError):
    """The file exceeds the entitlement's max_file_bytes."""


class ExtractionError(AttachmentError):
    """Text could not be extracted from the file."""


def _detect_content_type(data: bytes, declared: str, filename: str) -> str:
    """Sniff magic bytes first, falling back to the declared content type."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return "video/mp4"
    if data.startswith(b"RIFF") and b"WAVE" in data[:16]:
        return "audio/wav"
    for signature, content_type in _MAGIC_SIGNATURES:
        if data.startswith(signature):
            return content_type
    return (declared or "application/octet-stream").split(";")[0].strip().lower()


def _extension_of(filename: str) -> str:
    name = (filename or "").lower()
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].strip()


def validate_upload(
    *,
    filename: str,
    declared_content_type: str,
    data: bytes,
    max_file_bytes: int,
) -> str:
    """Validate an upload before extraction.

    Returns the sniffed content type. Raises AttachmentError subclasses on
    rejection (extension, executable content, size).
    """
    settings = get_settings()
    allowed = set(settings.attachment_allowed_extension_list)
    extension = _extension_of(filename)

    if not extension or extension not in allowed:
        raise UnsupportedFileTypeError(
            f"File type '{extension or 'unknown'}' is not allowed. "
            f"Allowed types: {', '.join(sorted(allowed))}."
        )

    content_type = _detect_content_type(data, declared_content_type, filename)
    if content_type in ("application/x-executable", "application/x-ole-storage"):
        raise UnsupportedFileTypeError("Executable or legacy binary files are not allowed.")

    if len(data) > max_file_bytes:
        raise FileTooLargeError(
            f"File is {len(data)} bytes; the limit for your plan is {max_file_bytes} bytes."
        )

    return content_type


def process_image(data: bytes, filename: str, max_dim: int = 2048) -> tuple[bytes, str, int, int]:
    """Strip EXIF, cap max dimensions, and re-encode image safely."""
    try:
        with io.BytesIO(data) as in_buf:
            with Image.open(in_buf) as img:
                try:
                    if hasattr(img, "getexif"):
                        _ = img.getexif()
                except Exception:
                    logger.debug("exif_parse_failed", filename=filename)

                format_name = (img.format or "PNG").upper()
                if format_name not in ("JPEG", "JPG", "PNG", "WEBP", "GIF"):
                    format_name = "PNG"

                orig_mode = img.mode
                width, height = img.size

                if width > max_dim or height > max_dim:
                    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                    width, height = img.size

                out = io.BytesIO()
                if format_name in ("JPEG", "JPG"):
                    if orig_mode in ("RGBA", "P", "LA"):
                        rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                        rgb_img.paste(img, mask=img.split()[-1] if orig_mode in ("RGBA", "LA") else None)
                        rgb_img.save(out, format="JPEG", quality=88, optimize=True)
                    else:
                        img.save(out, format="JPEG", quality=88, optimize=True)
                    mime = "image/jpeg"
                elif format_name == "WEBP":
                    img.save(out, format="WEBP", quality=88)
                    mime = "image/webp"
                elif format_name == "GIF":
                    img.save(out, format="GIF")
                    mime = "image/gif"
                else:
                    img.save(out, format="PNG", optimize=True)
                    mime = "image/png"

                return out.getvalue(), mime, width, height
    except Exception as exc:
        raise ExtractionError(f"Could not process image '{filename}': {exc}") from exc


def extract_text(data: bytes, filename: str, content_type: str) -> str:
    """Extract plain text from an upload, dispatching on extension.

    PDF uses pdfminer.six, DOCX/XLSX are parsed as ZIP-based OOXML, and
    text/code formats are decoded as UTF-8 with a latin-1 fallback.
    """
    if len(data) > 50 * 1024 * 1024:
        raise FileTooLargeError(f"File too large for text extraction: {len(data) // (1024 * 1024)} MB")

    extension = _extension_of(filename)

    try:
        if extension in ("txt", "md", "log", "rtf") or extension in CODE_EXTENSIONS:
            return _decode_text(data)
        if extension == "csv":
            return _extract_csv(data)
        if extension == "pdf":
            return _extract_pdf(data, filename)
        if extension == "docx":
            return _extract_docx(data)
        if extension == "xlsx":
            return _extract_xlsx(data)
        if extension in IMAGE_EXTENSIONS:
            return f"[Image: {filename}]"
        if extension in AUDIO_EXTENSIONS:
            return f"[Audio: {filename}]"
    except AttachmentError:
        raise
    except MemoryError as exc:
        raise ExtractionError("Insufficient memory to process file; try a smaller file.") from exc
    except Exception as exc:
        raise ExtractionError(f"Could not extract text from '{filename}'.") from exc

    raise UnsupportedFileTypeError(f"Extraction is not supported for '{filename}'.")


def _decode_text(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        logger.warning("text_decode_utf8_failed_falling_back_to_latin1")
        text = data.decode("latin-1", errors="replace")
    if "\x00" in text:
        raise ExtractionError("Binary content cannot be attached as text.")
    return text[:_MAX_EXTRACTED_CHARS]


def _extract_csv(data: bytes) -> str:
    text = _decode_text(data)
    rows = list(csv.reader(io.StringIO(text)))
    return "\n".join(",".join(row) for row in rows)[:_MAX_EXTRACTED_CHARS]


def _extract_pdf(data: bytes, filename: str = "document.pdf") -> str:
    try:
        from pdfminer.high_level import extract_text as pdf_extract_text
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("PDF extraction is not available on this server.") from exc

    with io.BytesIO(data) as buf:
        text = pdf_extract_text(buf) or ""
    if not text.strip():
        return f"[PDF Document: {filename}]"
    return text[:_MAX_EXTRACTED_CHARS]


def _extract_docx(data: bytes) -> str:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("DOCX extraction is not available on this server.") from exc

    try:
        with io.BytesIO(data) as buf:
            document = docx.Document(buf)
    except Exception as exc:
        raise ExtractionError("File is not a valid DOCX document.") from exc

    parts = [para.text for para in document.paragraphs if para.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)[:_MAX_EXTRACTED_CHARS]


def _extract_xlsx(data: bytes) -> str:
    try:
        import openpyxl
    except ImportError as exc:  # pragma: no cover
        raise ExtractionError("XLSX extraction is not available on this server.") from exc

    try:
        with io.BytesIO(data) as buf:
            workbook = openpyxl.load_workbook(buf, read_only=True, data_only=True)
            parts: list[str] = []
            for sheet in workbook.worksheets:
                parts.append(f"## Sheet: {sheet.title}")
                for row in sheet.iter_rows(max_row=2000, values_only=True):
                    if row and any(cell is not None for cell in row):
                        parts.append(" | ".join("" if cell is None else str(cell) for cell in row))
            workbook.close()
    except Exception as exc:
        raise ExtractionError("File is not a valid XLSX workbook.") from exc

    return "\n".join(parts)[:_MAX_EXTRACTED_CHARS]


def _zip_bomb_guard(data: bytes) -> None:
    """Reject OOXML archives whose decompressed size is implausibly large."""
    max_decompressed = 64 * 1024 * 1024  # 64 MiB
    try:
        with io.BytesIO(data) as buf:
            with zipfile.ZipFile(buf) as archive:
                total = sum(info.file_size for info in archive.infolist())
                if total > max_decompressed:
                    raise ExtractionError("Attached document is too large to process.")
    except zipfile.BadZipFile:
        pass  # not a zip; the type-specific extractor will handle/report


async def stage_attachment(
    *,
    user_id: int,
    filename: str,
    declared_content_type: str,
    data: bytes,
    max_file_bytes: int,
) -> dict[str, Any]:
    """Validate, extract, and stage an upload in Redis. Returns metadata."""
    content_type = validate_upload(
        filename=filename,
        declared_content_type=declared_content_type,
        data=data,
        max_file_bytes=max_file_bytes,
    )
    _zip_bomb_guard(data)

    settings = get_settings()
    extension = _extension_of(filename)
    kind = "file"
    image_base64 = None
    processed_bytes = data

    if extension in IMAGE_EXTENSIONS and settings.enable_vision:
        kind = "image"
        processed_bytes, content_type, img_w, img_h = process_image(
            data, filename, max_dim=settings.max_image_dimension
        )
        image_base64 = base64.b64encode(processed_bytes).decode("ascii")
        text = f"[Image Attachment: {filename} ({img_w}x{img_h})]"
    elif extension in AUDIO_EXTENSIONS and getattr(settings, "enable_audio_input", True):
        kind = "audio"
        text = f"[Audio Attachment: {filename}]"
    elif extension in VIDEO_EXTENSIONS and getattr(settings, "enable_video_input", True):
        kind = "video"
        text = f"[Video Attachment: {filename}]"
    else:
        text = extract_text(data, filename, content_type)
        if not text.strip():
            raise ExtractionError(f"No text content could be extracted from '{filename}'.")

    attachment_id = str(uuid.uuid4())
    if kind == "image":
        raw_b64 = image_base64
    elif kind in ("audio", "video") or extension == "pdf" or content_type == "application/pdf":
        raw_b64 = base64.b64encode(processed_bytes).decode("ascii")
    else:
        raw_b64 = None

    payload = {
        "attachment_id": attachment_id,
        "user_id": user_id,
        "filename": filename,
        "size": len(processed_bytes),
        "content_type": content_type,
        "content": text,
        "kind": kind,
        "is_image": (kind == "image"),
        "is_audio": (kind == "audio"),
        "is_video": (kind == "video"),
        "is_pdf": (extension == "pdf" or content_type == "application/pdf"),
        "image_base64": image_base64,
        "raw_bytes_base64": raw_b64,
    }
    _MEMORY_STAGED.set(attachment_id, payload, ttl=settings.attachment_ttl_seconds)
    stored = await redis_client.set_json(
        f"{ATTACHMENT_KEY_PREFIX}{attachment_id}",
        payload,
        ex=settings.attachment_ttl_seconds,
    )
    if not stored:
        logger.warning("Redis unavailable; attachment %s will be used without staging", attachment_id)

    return {
        "attachment_id": attachment_id,
        "filename": filename,
        "size": len(processed_bytes),
        "content_type": content_type,
        "chars_extracted": len(text),
        "kind": kind,
        "staged": stored or True,
    }


async def load_attachments_from_redis(
    attachments: list[dict[str, Any]] | None,
    user_id: int | None = None,
) -> list[dict[str, Any]] | None:
    """Resolve attachment_id references (from ChatRequest.attachments) to payloads."""
    if not attachments:
        return None
    loaded: list[dict[str, Any]] = []
    for entry in attachments:
        if not isinstance(entry, dict):
            continue
        attachment_id = str(entry.get("attachment_id") or "").strip()
        if not attachment_id:
            continue
        payload = await redis_client.get_json(f"{ATTACHMENT_KEY_PREFIX}{attachment_id}")
        if not payload:
            payload = _MEMORY_STAGED.get(attachment_id)
        if not payload:
            logger.warning("Attachment %s not found or expired", attachment_id)
            continue

        # IDOR enforcement: ensure attachment belongs to the requesting user
        payload_owner = payload.get("user_id")
        if user_id is not None and payload_owner is not None and payload_owner != user_id:
            logger.warning(
                "attachment_access_denied_idor",
                attachment_id=attachment_id,
                user_id=user_id,
                payload_owner=payload_owner,
            )
            continue

        loaded.append({
            "attachment_id": attachment_id,
            "filename": payload.get("filename", "attachment"),
            "content_type": payload.get("content_type", "text/plain"),
            "content": payload.get("content", ""),
            "kind": payload.get("kind", "file"),
            "is_image": payload.get("is_image", False),
            "is_audio": payload.get("is_audio", False),
            "is_video": payload.get("is_video", False),
            "is_pdf": payload.get("is_pdf", False) or (payload.get("content_type") == "application/pdf"),
            "image_base64": payload.get("image_base64"),
            "raw_bytes_base64": payload.get("raw_bytes_base64"),
            "size": payload.get("size", 0),
        })
    return loaded or None


async def get_staged_attachment_bytes(attachment_id: str, user_id: int | None = None) -> tuple[bytes, str, str] | None:
    """Retrieve raw bytes, filename, and content_type for a staged attachment."""
    payload = await redis_client.get_json(f"{ATTACHMENT_KEY_PREFIX}{attachment_id}")
    if not payload:
        payload = _MEMORY_STAGED.get(attachment_id)
    if not payload:
        return None
    if user_id is not None and payload.get("user_id") != user_id:
        return None
    raw_b64 = payload.get("raw_bytes_base64")
    if not raw_b64:
        content = payload.get("content", "")
        return content.encode("utf-8"), payload.get("filename", "download.txt"), payload.get("content_type", "text/plain")
    data = base64.b64decode(raw_b64.encode("ascii"))
    return data, payload.get("filename", "download"), payload.get("content_type", "application/octet-stream")
