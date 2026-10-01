"""Confidential documents (NIB, NPWP, deeds, signed BAK) encrypted at rest.

Files are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) before they touch the disk. The
key comes from ``NEGO_DOC_KEY`` (a Fernet key; keep it in the server's secret store). Without
it, a key file is created in the workspace (readable only by the app's user), which is fine
for a pilot but should be replaced by ``NEGO_DOC_KEY`` in production so the key doesn't sit
next to the data.
"""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from pathlib import Path

from ..engine import EngineError
from ..settings import workspace

DOC_TYPES = (
    ("nib", "NIB / Izin usaha", "Business licence (NIB)", True),
    ("npwp", "NPWP perusahaan", "Company tax ID (NPWP)", True),
    ("akta", "Akta pendirian & perubahan", "Deed of establishment & amendments", False),
    ("loa", "Surat penunjukan distributor (LoA)", "Distributor appointment letter (LoA)", False),
    ("izin_edar", "Izin edar / sertifikat produk", "Product registration / certificates", False),
    ("other", "Lainnya", "Other", False),
)
TYPE_KEYS = {k for k, *_ in DOC_TYPES}
REQUIRED = {k for k, _, _, req in DOC_TYPES if req}
ALLOWED = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
           ".zip": "application/zip", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
           ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
MAX_BYTES = 15 * 1024 * 1024


def _fernet():
    from cryptography.fernet import Fernet

    key = os.environ.get("NEGO_DOC_KEY")
    if not key:
        path = workspace() / "doc.key"
        if not path.exists():
            path.write_bytes(Fernet.generate_key())
            os.chmod(path, 0o600)
        key = path.read_bytes().decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def _dir(cid: int) -> Path:
    d = workspace() / "nego_docs" / str(int(cid))
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_upload(filename: str, content: bytes, doc_type: str) -> tuple[str, str]:
    if doc_type not in TYPE_KEYS:
        raise EngineError("Pilih jenis dokumen. / Choose a document type.")
    name = re.sub(r"[^\w.\- ()]", "_", Path(filename or "file").name).strip()[:150] or "file"
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise EngineError(f"Jenis file tidak didukung. / File type not allowed: {', '.join(sorted(ALLOWED))}")
    if len(content) > MAX_BYTES:
        raise EngineError("File maksimal 15 MB. / Files can be up to 15 MB.")
    if not content:
        raise EngineError("File kosong. / The file is empty.")
    if ext == ".pdf" and not content.startswith(b"%PDF"):
        raise EngineError("File ini bukan PDF. / That file isn't a PDF.")
    return name, ALLOWED[ext]


def save(cid: int, content: bytes) -> tuple[str, str]:
    stored = uuid.uuid4().hex + ".bin"
    (_dir(cid) / stored).write_bytes(_fernet().encrypt(content))
    return stored, hashlib.sha256(content).hexdigest()


def load(cid: int, stored: str) -> bytes:
    path = _dir(cid) / Path(stored).name
    if not path.exists():
        raise EngineError("No such document")
    return _fernet().decrypt(path.read_bytes())


def remove(cid: int, stored: str) -> None:
    (_dir(cid) / Path(stored).name).unlink(missing_ok=True)
