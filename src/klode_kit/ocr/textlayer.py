"""Text-layer PDF split (WI-04c; design 02 r2.4 "文字层 PDF", R2-08, r3.1 segment 1).

A PDF page is read with ``pdftotext -f N -l N -bbox-layout`` and classified:

- ``empty``: fewer than ``min_chars`` non-whitespace characters (no text layer, or a scan);
- ``garbled``: the share of garbage characters is strictly above ``garbage_max``;
- ``text``: otherwise. Its lines become ``engine: pdftext`` rows of the ``ocr_line`` contract.

Boundaries are the config keys ``text_layer.min_chars`` (50) and ``text_layer.garbage_max`` (0.05): 49
characters is ``empty``, 50 is ``text``; a ratio of exactly 0.05 is ``text``, anything above is ``garbled``.

Garbage characters: everything outside the scripts this toolkit reads (Chinese/Japanese documents with ASCII,
see ``_ALLOWED``). Latin-1 letters and symbols - the usual shape of mojibake - count as garbage, except the unit
symbols ``° ± × ÷ µ · ² ³``. A PDF in an accented Latin script is therefore classified ``garbled`` and goes to
the four routes: the safe direction (more OCR, never a wrong text layer).

Geometry: pdftotext reports points with a top-left origin; ``bbox_px = pt_to_px(bbox_pt, dpi)`` where
``pt_to_px(v) = floor(v * dpi / 72 + 0.5)`` (round half up), in the pixels of the page rendered at ``dpi``
(``render_page``). Points keep pdftotext's 6 decimals and are clamped at 0 (the contract minimum) first. pdftext
tokens are words and their geometry is ``exact``.

Tools: ``pdfinfo`` (page count), ``pdftotext`` (text layer), ``pdftoppm`` (rendering). ``require_tools`` raises
``PopplerMissing`` naming every missing one; any failure to read a PDF is ``PdfUnreadable`` (``pdf_unreadable``).
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

TOOLS = ("pdfinfo", "pdftotext", "pdftoppm")
INSTALL_HINT = "install poppler (macOS: brew install poppler; Debian/Ubuntu: apt install poppler-utils)"
DEFAULT_DPI = 300
TIMEOUT_S = 120
KINDS = ("text", "empty", "garbled")

_ALLOWED = (
    (0x21, 0x7E),        # ASCII printable
    (0x0370, 0x03FF),    # Greek (units, formulae)
    (0x2010, 0x205E),    # general punctuation (dashes, quotes, ellipsis, per mille)
    (0x2070, 0x209F),    # super- and subscripts
    (0x2100, 0x218F),    # letterlike symbols, number forms
    (0x2190, 0x22FF),    # arrows, mathematical operators
    (0x2460, 0x24FF),    # enclosed alphanumerics
    (0x2E80, 0x2FDF),    # CJK radicals
    (0x3000, 0x303F),    # CJK symbols and punctuation
    (0x3040, 0x30FF),    # hiragana, katakana
    (0x3100, 0x312F),    # bopomofo
    (0x31F0, 0x31FF),    # katakana extensions
    (0x3200, 0x33FF),    # enclosed CJK, CJK compatibility (units)
    (0x3400, 0x4DBF),    # CJK extension A
    (0x4E00, 0x9FFF),    # CJK unified ideographs
    (0xAC00, 0xD7AF),    # hangul syllables
    (0xF900, 0xFAFF),    # CJK compatibility ideographs
    (0xFE30, 0xFE4F),    # CJK compatibility forms
    (0xFF00, 0xFFEF),    # half- and fullwidth forms
    (0x20000, 0x3134F),  # CJK extensions B..G
)
_ALLOWED_SYMBOLS = frozenset("°±×÷µ·²³")
_NS = "{http://www.w3.org/1999/xhtml}"


class PopplerMissing(RuntimeError):
    """A poppler tool needed for PDF input is not on PATH."""


class PdfUnreadable(ValueError):
    """The PDF cannot be read (``pdf_unreadable``)."""


@dataclass(frozen=True)
class Classification:
    kind: str
    chars: int
    garbage: int
    garbage_ratio: float


@dataclass(frozen=True)
class Word:
    text: str
    bbox_pt: tuple[float, float, float, float]


@dataclass(frozen=True)
class Line:
    words: tuple[Word, ...]
    bbox_pt: tuple[float, float, float, float]

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


@dataclass(frozen=True)
class PageText:
    width_pt: float
    height_pt: float
    lines: tuple[Line, ...] = field(default_factory=tuple)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


@dataclass(frozen=True)
class PdfPage:
    """One page of a PDF: its number, text layer, classification and rendering."""

    number: int
    text: PageText
    classification: Classification
    png: bytes
    size_px: tuple[int, int]
    dpi: int


# --------------------------------------------------------------------------- pure parts


def _is_garbage(ch: str) -> bool:
    if ch in _ALLOWED_SYMBOLS:
        return False
    cp = ord(ch)
    return not any(lo <= cp <= hi for lo, hi in _ALLOWED)


def classify_page(text: str, *, min_chars: int = 50, garbage_max: float = 0.05) -> Classification:
    """Classify the text of one page (see the module docstring for the boundaries)."""
    chars = [c for c in text if not c.isspace()]
    n = len(chars)
    garbage = sum(1 for c in chars if _is_garbage(c))
    ratio = garbage / n if n else 0.0
    if n < min_chars:
        kind = "empty"
    elif ratio > garbage_max:
        kind = "garbled"
    else:
        kind = "text"
    return Classification(kind, n, garbage, ratio)


def pt_to_px(pt: float, dpi: int) -> int:
    """Points (1/72 inch) to pixels at ``dpi``, rounded half up."""
    return math.floor(pt * dpi / 72 + 0.5)


def _pt(value: str) -> float:
    return max(0.0, round(float(value), 6))


def _box(el: ET.Element) -> tuple[float, float, float, float]:
    try:
        return (_pt(el.attrib["xMin"]), _pt(el.attrib["yMin"]), _pt(el.attrib["xMax"]), _pt(el.attrib["yMax"]))
    except (KeyError, ValueError) as exc:
        raise PdfUnreadable(f"pdftotext box without coordinates: {exc}") from None


def _local(tag: str) -> str:
    return tag[len(_NS):] if tag.startswith(_NS) else tag


def parse_bbox_layout(xml: str) -> PageText:
    """The first ``<page>`` of a ``pdftotext -bbox-layout`` document (words in reading order)."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise PdfUnreadable(f"pdftotext output is not XML: {exc}") from None
    page = next((el for el in root.iter() if _local(el.tag) == "page"), None)
    if page is None:
        raise PdfUnreadable("pdftotext output has no <page>")
    try:
        width, height = float(page.attrib["width"]), float(page.attrib["height"])
    except (KeyError, ValueError):
        raise PdfUnreadable("pdftotext <page> without width/height") from None
    lines = []
    for el in page.iter():
        if _local(el.tag) != "line":
            continue
        words = tuple(Word(w.text or "", _box(w)) for w in el if _local(w.tag) == "word" and (w.text or "").strip())
        if words:
            lines.append(Line(words, _box(el)))
    return PageText(width, height, tuple(lines))


def pdftext_rows(page: PageText, *, page_id: str, der_sha256: str, dpi: int) -> list[dict]:
    """``ocr_line`` rows (``engine: pdftext``) of a page; boxes in the pixels of the page rendered at ``dpi``."""
    rows = []
    for idx, line in enumerate(page.lines):
        tokens, pos = [], 0
        for word in line.words:
            if tokens:
                pos += 1
            tokens.append({"text": word.text, "bbox_pt": list(word.bbox_pt),
                           "bbox_px": [pt_to_px(v, dpi) for v in word.bbox_pt],
                           "span": [pos, pos + len(word.text)], "geometry": "exact"})
            pos += len(word.text)
        rows.append({"engine": "pdftext", "page_id": page_id, "der_sha256": der_sha256, "line_idx": idx,
                     "text": line.text, "bbox_pt": list(line.bbox_pt),
                     "bbox_px": [pt_to_px(v, dpi) for v in line.bbox_pt], "tokens": tokens})
    return rows


# --------------------------------------------------------------------------- poppler


def require_tools() -> dict[str, str]:
    """Paths of the poppler tools; ``PopplerMissing`` names every missing one."""
    found = {t: shutil.which(t) for t in TOOLS}
    missing = [t for t, p in found.items() if p is None]
    if missing:
        raise PopplerMissing(f"missing tool(s) for PDF input: {', '.join(missing)}; {INSTALL_HINT}")
    return {t: str(p) for t, p in found.items()}


def _run(argv: list[str], what: str) -> bytes:
    try:
        res = subprocess.run(argv, capture_output=True, timeout=TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        raise PdfUnreadable(f"{what}: {argv[0]} timed out after {TIMEOUT_S}s") from None
    if res.returncode != 0:
        detail = res.stderr.decode("utf-8", "replace").strip().splitlines()
        raise PdfUnreadable(f"{what}: {Path(argv[0]).name} exit {res.returncode}"
                            + (f" ({detail[-1]})" if detail else ""))
    return res.stdout


_PAGES = re.compile(rb"^Pages:\s+(\d+)\s*$", re.M)


def page_count(pdf: Path, tools: dict[str, str]) -> int:
    out = _run([tools["pdfinfo"], str(pdf)], f"{pdf.name}: pdfinfo")
    m = _PAGES.search(out)
    if not m or int(m.group(1)) < 1:
        raise PdfUnreadable(f"{pdf.name}: pdfinfo reports no pages")
    return int(m.group(1))


def extract_page(pdf: Path, number: int, tools: dict[str, str]) -> PageText:
    out = _run([tools["pdftotext"], "-f", str(number), "-l", str(number), "-bbox-layout", "-enc", "UTF-8",
                str(pdf), "-"], f"{pdf.name} page {number}: pdftotext")
    return parse_bbox_layout(out.decode("utf-8", "replace"))


def render_page(pdf: Path, number: int, dpi: int, tools: dict[str, str]) -> bytes:
    png = _run([tools["pdftoppm"], "-f", str(number), "-l", str(number), "-r", str(dpi), "-png", "-singlefile",
                str(pdf)], f"{pdf.name} page {number}: pdftoppm")
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise PdfUnreadable(f"{pdf.name} page {number}: pdftoppm produced no PNG")
    return png


def _png_size(png: bytes) -> tuple[int, int]:
    return int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")


def read_pdf(pdf: Path, *, dpi: int = DEFAULT_DPI, min_chars: int = 50, garbage_max: float = 0.05,
             tools: dict[str, str] | None = None) -> list[PdfPage]:
    """Every page of ``pdf``: text layer, classification and rendering. Nothing is written."""
    tools = require_tools() if tools is None else tools
    pages = []
    for n in range(1, page_count(pdf, tools) + 1):
        text = extract_page(pdf, n, tools)
        png = render_page(pdf, n, dpi, tools)
        pages.append(PdfPage(n, text, classify_page(text.text, min_chars=min_chars, garbage_max=garbage_max),
                             png, _png_size(png), dpi))
    return pages

