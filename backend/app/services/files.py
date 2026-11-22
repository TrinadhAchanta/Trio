"""Upload validation and storage.

Validation is allowlist-based: the file must have an allowed extension AND its leading bytes
must match the expected format (we never trust the browser-supplied content type). Text files
must be valid UTF-8 without NUL bytes. Storage is behind a tiny interface so S3/GCS can replace
the local disk backend later.
"""

import hashlib
import io
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings

TEXT_EXTS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".xml", ".yaml", ".yml", ".sql", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".sh", ".toml", ".log",
}
IMAGE_EXTS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
              ".webp": "image/webp"}
PDF_EXTS = {".pdf"}
MAX_EXTRACTED_CHARS = 200_000


class UploadRejected(Exception):
    pass


@dataclass
class ValidatedFile:
    filename: str
    content_type: str
    kind: str
    size: int
    sha256: str
    extracted_text: str | None


def safe_filename(name: str) -> str:
    base = os.path.basename(name or "file").strip()
    base = re.sub(r"[^\w.\- ()]+", "_", base)[:200]
    return base or "file"


def _sniff_image(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _extract_pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise UploadRejected("Encrypted PDFs are not supported.")
    out: list[str] = []
    total = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        out.append(text)
        total += len(text)
        if total > MAX_EXTRACTED_CHARS:
            break
    return "\n\n".join(out)[:MAX_EXTRACTED_CHARS]


def validate_upload(filename: str, data: bytes) -> ValidatedFile:
    s = get_settings()
    name = safe_filename(filename)
    ext = Path(name).suffix.lower()
    size = len(data)
    if size == 0:
        raise UploadRejected("The file is empty.")
    if size > s.max_upload_mb * 1024 * 1024:
        raise UploadRejected(f"The file is larger than {s.max_upload_mb} MB.")
    digest = hashlib.sha256(data).hexdigest()

    if ext in IMAGE_EXTS:
        sniffed = _sniff_image(data)
        if sniffed is None or sniffed != IMAGE_EXTS[ext]:
            raise UploadRejected("The file content does not match its image type.")
        return ValidatedFile(name, sniffed, "image", size, digest, None)

    if ext in PDF_EXTS:
        if not data.startswith(b"%PDF-"):
            raise UploadRejected("The file is not a valid PDF.")
        try:
            text = _extract_pdf_text(data)
        except UploadRejected:
            raise
        except Exception as e:  # malformed PDFs raise many different errors
            raise UploadRejected("The PDF could not be read.") from e
        return ValidatedFile(name, "application/pdf", "pdf", size, digest, text)

    if ext in TEXT_EXTS:
        if b"\x00" in data:
            raise UploadRejected("Binary content is not allowed in a text file.")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise UploadRejected("Text files must be UTF-8 encoded.") from e
        return ValidatedFile(name, "text/plain; charset=utf-8", "text", size, digest, text[:MAX_EXTRACTED_CHARS])

    raise UploadRejected(f"Files of type '{ext or 'unknown'}' are not supported yet.")


class LocalStorage:
    def __init__(self, root: str):
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root) + os.sep):
            raise ValueError("Invalid storage key")
        return p

    def put(self, user_id: uuid.UUID, data: bytes) -> str:
        key = f"{user_id}/{uuid.uuid4().hex}"
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()


class DbStorage:
    """Stores file bytes in the stored_blobs table. Same interface as LocalStorage."""

    def put(self, user_id: uuid.UUID, data: bytes) -> str:
        from ..db import SessionLocal
        from ..models import StoredBlob

        key = f"{user_id}/{uuid.uuid4().hex}"
        with SessionLocal() as db:
            db.add(StoredBlob(key=key, data=data))
            db.commit()
        return key

    def get(self, key: str) -> bytes:
        from ..db import SessionLocal
        from ..models import StoredBlob

        with SessionLocal() as db:
            b = db.get(StoredBlob, key)
            if b is None:
                raise FileNotFoundError(key)
            return b.data

    def delete(self, key: str) -> None:
        from ..db import SessionLocal
        from ..models import StoredBlob

        with SessionLocal() as db:
            b = db.get(StoredBlob, key)
            if b is not None:
                db.delete(b)
                db.commit()


def get_storage() -> LocalStorage | DbStorage:
    s = get_settings()
    return DbStorage() if s.storage_backend == "db" else LocalStorage(s.storage_dir)
