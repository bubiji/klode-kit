"""Shared helpers for the synthetic fixture generators (WI-00a).

Everything here is deterministic: no clock, no randomness without a fixed seed, sorted JSON.
Output is an in-memory tree ``{relpath: bytes}`` that ``gen_all`` writes or compares.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]

# Allowlist (the only person-like names, IDs and drug names that may appear anywhere).
PATIENT = "测试患者甲"
PATIENT_B = "测试患者乙"
PATIENT_ID = "T-0000001"
PATIENT_ID_B = "T-0000002"
DRUG = "示例片"
HOSPITAL = "示例医院"

# CJK raster font: macOS system font, used only to render pixels; never copied into the repo.
FONT_PATH = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
FONT_INDEX = 0
# Text-layer PDFs: reportlab's built-in CID font (not embedded, Adobe-GB1 encoding).
PDF_CJK_FONT = "STSong-Light"

FIXED_CREATED = "2026-10-22T00:00:00Z"
ROUTES = ("vision", "mlx", "claude", "codex")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jdump(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def jlines(rows) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows).encode("utf-8")


def text(s: str) -> bytes:
    return s.encode("utf-8")


def doc_id(img: bytes) -> str:
    return "d-" + sha256(img)[:12]


def rec_id(doc: str, page: str = "p001", record_idx: int = 0) -> str:
    return "r-" + sha256(f"{doc}|{page}|{record_idx}".encode("utf-8"))[:12]


def png_bytes(image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def front_matter(meta: dict, body: str = "") -> bytes:
    """Markdown with a JSON front-matter block (JSON is valid YAML; no YAML dependency)."""
    return ("---\n" + json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n---\n" + body).encode("utf-8")
