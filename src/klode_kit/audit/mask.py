"""Header masking before anything leaves the machine (WI-05a; design 02 r2.2-B, N5, r5 "打码对照图位置").

Input is a WI-04b batch: every line of ``work/<batch>/derived.jsonl`` names an upright (derived) page image and
its ``der_sha256``. For each page:

1. Apple Vision (route A, ``engines.OcrMacEngine``) reads the page; ``find_boxes`` picks the rows to black out
   with the rules of ``klode_kit/config/mask_rules.toml``:
   - the top band of the page (``header_band`` x height) and every line reaching into it;
   - every line whose text holds a header keyword (Chinese / Japanese / English: 姓名, 患者, 编号, ID, 病历号,
     住院号, 床号, 電話, 氏名, カルテ, ...) or matches an identifier pattern (``T-0000001``-like numbers);
   a matched line is padded and, with ``full_width``, stretched over the page width.
2. ``apply`` sets every pixel inside the boxes to 0 (all bands) and leaves every other pixel as it was.
3. Outputs, in the data project:

   ``_masked/<doc_id>/<page>.m.png``   the masked page - the ONLY images that may be sent to a cloud route;
   ``_masked/MANIFEST.jsonl``         one row per masked image (below), append-only, never a duplicate;
   ``_mask_review/<doc_id>/<page>.review.png``  before/after picture (original with the boxes outlined | masked).
                                      It shows the unmasked header: local only, the sender refuses this
                                      directory and its sha is never in the MANIFEST.

MANIFEST row (the whitelist WI-05b's sender checks a file's recomputed sha256 against)::

    {"der_sha256": sha256 of the .m.png bytes, "parent": img_sha256 of the original document image,
     "page_id": "<doc_id>/p<NNN>", "mask_boxes": [[x0, y0, x1, y1], ...], "tool_version": "...",
     "path": "<doc_id>/<page>.m.png"}

``mask_boxes`` are integer pixel ranges of the masked (= derived) image, half-open (``x0 <= x < x1``), clipped
to the image. ``tool_version`` carries this module's version and the fingerprint of the rules, so a page masked
under other rules is told apart. ``read_manifest`` validates every row (``MaskError`` otherwise).

Durability and concurrency: all writes of a page happen while holding an exclusive ``flock`` on
``_masked/MANIFEST.jsonl`` (no extra lock file, so ``_masked/`` holds only images and the MANIFEST). Under the
lock a run first removes temporary files a killed run left (``.<name>.<hex>.tmp``) and cuts a torn last MANIFEST
line; a page whose ``der_sha256`` is listed and whose file holds those bytes is not written again (idempotent:
reruns and concurrent runs add no row and rewrite no byte). Images are written to a temporary name and moved into
place; ``crashpoint()`` separates every two durable writes (matrix section 0.3), and the MANIFEST row is appended
last, so a listed sha always names a complete file.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import io
import json
import os
import re
import secrets
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Sequence

from PIL import Image, ImageDraw

from klode_kit import __version__
from klode_kit.config import ConfigError
from klode_kit.seams import crashpoint

MASKED_DIR = "_masked"
REVIEW_DIR = "_mask_review"
MANIFEST_NAME = "MANIFEST.jsonl"
RULES_FILE = "mask_rules.toml"
TOOL = "mask/1"
CRASHPOINTS = (
    "mask.mkdir", "mask.cleanup", "mask.manifest_repaired", "mask.image_written", "mask.review_written",
    "mask.manifest_appended",
)
ROW_FIELDS = ("der_sha256", "parent", "page_id", "mask_boxes", "tool_version", "path")
MODES = ("L", "RGB", "RGBA")

_HEX64 = re.compile(r"[0-9a-f]{64}")
_PAGE = re.compile(r"(d-[0-9a-f]{12})/(p[0-9]{3,})")
_BATCH = re.compile(r"[0-9a-f]{12}")
_TMP = re.compile(r"\..+\.tmp")
_RULE_KEYS = {"keywords": list, "patterns": list, "header_band": float, "pad_px": int, "pad_frac": float,
              "full_width": bool}
_REVIEW_GAP = 16
_OUTLINE = (255, 0, 0)


class MaskError(Exception):
    """Bad input (unknown batch, changed derived image, bad page id) or a corrupt MANIFEST."""


# --------------------------------------------------------------------------- rules


@dataclass(frozen=True)
class MaskRules:
    keywords: tuple[str, ...]
    patterns: tuple[str, ...]
    header_band: float
    pad_px: int
    pad_frac: float
    full_width: bool

    @property
    def fingerprint(self) -> str:
        blob = json.dumps({"keywords": list(self.keywords), "patterns": list(self.patterns),
                           "header_band": self.header_band, "pad_px": self.pad_px, "pad_frac": self.pad_frac,
                           "full_width": self.full_width}, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    @property
    def tool_version(self) -> str:
        return f"{TOOL} klode-kit/{__version__} rules/{self.fingerprint[:12]}"


def _rule_error(path: Path, message: str, key: str | None = None) -> ConfigError:
    return ConfigError(f"{path}: {message}", key)


def load_rules(path: str | os.PathLike | None = None) -> MaskRules:
    """The packaged ``mask_rules.toml`` (or ``path``); any defect is a ``ConfigError`` (exit 40)."""
    path = Path(path) if path is not None else Path(str(resources.files("klode_kit.config") / RULES_FILE))
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise _rule_error(path, f"cannot read mask rules: {exc}") from exc
    unknown = sorted(set(data) - set(_RULE_KEYS))
    missing = sorted(set(_RULE_KEYS) - set(data))
    if unknown or missing:
        raise _rule_error(path, f"mask rules keys: unknown {unknown}, missing {missing}",
                          (unknown or missing)[0])
    for key, kind in _RULE_KEYS.items():
        value = data[key]
        ok = (isinstance(value, (int, float)) and not isinstance(value, bool)) if kind is float else \
            (type(value) is kind)
        if not ok:
            raise _rule_error(path, f"mask rules key {key} must be {kind.__name__}", key)
    keywords, patterns = data["keywords"], data["patterns"]
    if not keywords or not all(isinstance(k, str) and k.strip() for k in keywords):
        raise _rule_error(path, "keywords must be a non-empty list of non-empty strings", "keywords")
    for p in patterns:
        if not isinstance(p, str) or not p:
            raise _rule_error(path, "patterns must be non-empty strings", "patterns")
        try:
            re.compile(p)
        except re.error as exc:
            raise _rule_error(path, f"pattern {p!r} is not a regular expression: {exc}", "patterns") from exc
    band = float(data["header_band"])
    if not 0 <= band < 0.5:
        raise _rule_error(path, "header_band must be in [0, 0.5)", "header_band")
    if data["pad_px"] < 0 or data["pad_frac"] < 0:
        raise _rule_error(path, "pad_px and pad_frac must be >= 0", "pad_px")
    return MaskRules(tuple(keywords), tuple(patterns), band, data["pad_px"], float(data["pad_frac"]),
                     data["full_width"])


# --------------------------------------------------------------------------- boxes and pixels


def _check_box(box: Any) -> tuple[int, int, int, int]:
    if not isinstance(box, (list, tuple)) or len(box) != 4 or not all(type(v) is int for v in box):
        raise ValueError(f"mask box must be four integers [x0, y0, x1, y1]: {box!r}")
    x0, y0, x1, y1 = box
    if x0 >= x1 or y0 >= y1:
        raise ValueError(f"mask box must have x0 < x1 and y0 < y1: {box!r}")
    return x0, y0, x1, y1


def _clip(box: Sequence[int], size: Sequence[int]) -> list[int] | None:
    w, h = size
    x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(w, box[2]), min(h, box[3])
    return [x0, y0, x1, y1] if x0 < x1 and y0 < y1 else None


def apply(image: Image.Image, boxes: Iterable[Sequence[int]]) -> Image.Image:
    """A copy of ``image`` with every pixel inside the (clipped) ``boxes`` set to 0 in every band."""
    checked = [_check_box(b) for b in boxes]
    if image.mode not in MODES:
        raise ValueError(f"image mode {image.mode} is not one of {MODES}")
    out = image.copy()
    for box in checked:
        clipped = _clip(box, out.size)
        if clipped is not None:
            out.paste(0, tuple(clipped))
    return out


def _matches(text: str, rules: MaskRules) -> bool:
    compact = re.sub(r"\s+", "", text)
    for kw in rules.keywords:
        if kw.isascii():
            if re.search(rf"(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])", text, re.IGNORECASE):
                return True
        elif kw in compact:
            return True
    return any(re.search(p, text) for p in rules.patterns)


def find_boxes(lines: Iterable[dict], size: Sequence[int], rules: MaskRules | None = None) -> list[list[int]]:
    """Mask boxes for a page from its Vision ``ocr_line`` rows (pixel boxes, inclusive corners)."""
    rules = rules or load_rules()
    w, h = size
    band = round(h * rules.header_band)
    boxes: list[list[int]] = [[0, 0, w, band]] if band > 0 else []
    for line in lines:
        bbox = line.get("bbox_px")
        if bbox is None:
            continue
        x0, y0, x1, y1 = bbox
        if not (y0 < band or _matches(str(line.get("text", "")), rules)):
            continue
        pad = max(rules.pad_px, round(rules.pad_frac * (y1 - y0 + 1)))
        box = [0, y0 - pad, w, y1 + 1 + pad] if rules.full_width else [x0 - pad, y0 - pad, x1 + 1 + pad,
                                                                         y1 + 1 + pad]
        clipped = _clip(box, size)
        if clipped is not None:
            boxes.append(clipped)
    return sorted({tuple(b): b for b in boxes}.values())


def _normalise(image: Image.Image) -> Image.Image:
    if image.mode in MODES:
        return image
    alpha = "A" in image.getbands() or "transparency" in image.info
    return image.convert("RGBA" if alpha else "RGB")


def _png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _review(original: Image.Image, masked: Image.Image, boxes: Sequence[Sequence[int]]) -> bytes:
    left = original.convert("RGB")
    draw = ImageDraw.Draw(left)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=_OUTLINE, width=3)
    w, h = left.size
    sheet = Image.new("RGB", (2 * w + _REVIEW_GAP, h), (255, 255, 255))
    sheet.paste(left, (0, 0))
    sheet.paste(masked.convert("RGB"), (w + _REVIEW_GAP, 0))
    return _png(sheet)


# --------------------------------------------------------------------------- durable writes


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _ensure_dir(directory: Path) -> None:
    missing = []
    cur = directory
    while not cur.is_dir():
        missing.append(cur)
        cur = cur.parent
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            if not d.is_dir():
                raise
        crashpoint("mask.mkdir")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]
    os.fsync(fd)


def _atomic(path: Path, data: bytes, after: str) -> None:
    _ensure_dir(path.parent)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        try:
            _write_all(fd, data)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise
    _fsync_dir(path.parent)
    crashpoint(after)


def _cleanup(roots: Iterable[Path]) -> None:
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob(".*.tmp")):
            if path.is_file() and _TMP.fullmatch(path.name):
                os.remove(path)
                crashpoint("mask.cleanup")


def _parse_rows(data: bytes, origin: Path) -> list[dict]:
    rows = []
    for n, raw in enumerate(data.decode("utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MaskError(f"{origin}:{n}: not JSON: {exc}") from exc
        _check_row(row, f"{origin}:{n}")
        rows.append(row)
    return rows


def _check_row(row: Any, where: str) -> None:
    if not isinstance(row, dict) or set(row) != set(ROW_FIELDS):
        raise MaskError(f"{where}: a MANIFEST row has exactly the fields {list(ROW_FIELDS)}")
    if not all(isinstance(row[k], str) and _HEX64.fullmatch(row[k]) for k in ("der_sha256", "parent")):
        raise MaskError(f"{where}: der_sha256 and parent must be sha256 hex")
    m = _PAGE.fullmatch(row["page_id"]) if isinstance(row["page_id"], str) else None
    if m is None or row["path"] != f"{m.group(1)}/{m.group(2)}.m.png":
        raise MaskError(f"{where}: bad page_id / path")
    boxes = row["mask_boxes"]
    if not isinstance(boxes, list) or not all(
            isinstance(b, list) and len(b) == 4 and all(type(v) is int and v >= 0 for v in b) for b in boxes):
        raise MaskError(f"{where}: mask_boxes must be lists of four non-negative integers")
    if not isinstance(row["tool_version"], str) or not row["tool_version"]:
        raise MaskError(f"{where}: tool_version must be a non-empty string")


def read_manifest(project: str | os.PathLike) -> list[dict]:
    """The validated MANIFEST rows (a torn, never-committed last line is ignored); ``[]`` if there is none."""
    path = Path(project) / MASKED_DIR / MANIFEST_NAME
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return []
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        with os.fdopen(os.dup(fd), "rb") as fh:
            data = fh.read()
    finally:
        os.close(fd)
    if data and not data.endswith(b"\n"):
        data = data[: data.rfind(b"\n") + 1]
    return _parse_rows(data, path)


@contextlib.contextmanager
def _manifest_locked(project: Path):
    masked = project / MASKED_DIR
    _ensure_dir(masked)
    path = masked / MANIFEST_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        _cleanup([masked, project / REVIEW_DIR])
        with os.fdopen(os.dup(fd), "rb") as fh:
            fh.seek(0)
            data = fh.read()
        if data and not data.endswith(b"\n"):
            keep = data.rfind(b"\n") + 1
            os.ftruncate(fd, keep)
            os.fsync(fd)
            crashpoint("mask.manifest_repaired")
            data = data[:keep]
        yield fd, _parse_rows(data, path)
    finally:
        os.close(fd)


# --------------------------------------------------------------------------- pages and batches


def _page_parts(page_id: Any) -> tuple[str, str]:
    m = _PAGE.fullmatch(page_id) if isinstance(page_id, str) else None
    if m is None:
        raise MaskError(f"page_id must be d-<12 hex>/p<NNN>: {page_id!r}")
    return m.group(1), m.group(2)


def _load(data: bytes, where: Path) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.load()
            return _normalise(im.copy())
    except Exception as exc:  # PIL raises UnidentifiedImageError / OSError
        raise MaskError(f"{where}: not a readable image ({exc})") from exc


def mask_page(project: str | os.PathLike, image_path: str | os.PathLike, *, parent: str, page_id: str,
              der_sha256: str, engine: Any, rules: MaskRules | None = None) -> dict:
    """Mask one derived page image; returns its MANIFEST row (written once, idempotently)."""
    project, image_path = Path(project), Path(image_path)
    doc, page = _page_parts(page_id)
    if not (isinstance(parent, str) and _HEX64.fullmatch(parent)):
        raise MaskError(f"parent must be sha256 hex: {parent!r}")
    try:
        data = image_path.read_bytes()
    except OSError as exc:
        raise MaskError(f"cannot read derived image {image_path}: {exc}") from exc
    if _sha(data) != der_sha256:
        raise MaskError(f"{image_path}: bytes do not match der_sha256 {der_sha256} (changed since the batch)")
    rules = rules or load_rules()
    original = _load(data, image_path)
    lines = engine.ocr_page(image_path, page_id=page_id, der_sha256=der_sha256)
    boxes = find_boxes(lines, original.size, rules)
    masked = apply(original, boxes)
    png = _png(masked)
    rel = f"{doc}/{page}.m.png"
    row = {"der_sha256": _sha(png), "parent": parent, "page_id": page_id, "mask_boxes": boxes,
           "tool_version": rules.tool_version, "path": rel}
    out = project / MASKED_DIR / rel
    review = project / REVIEW_DIR / doc / f"{page}.review.png"
    with _manifest_locked(project) as (fd, rows):
        listed = next((r for r in rows if r["der_sha256"] == row["der_sha256"]), None)
        if listed is not None and out.is_file() and _sha(out.read_bytes()) == row["der_sha256"]:
            return listed
        _atomic(out, png, "mask.image_written")
        _atomic(review, _review(original, masked, boxes), "mask.review_written")
        if listed is not None:
            return listed
        _write_all(fd, (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
        crashpoint("mask.manifest_appended")
    return row


def _derived_rows(project: Path, batch_id: str) -> list[dict]:
    if not (isinstance(batch_id, str) and _BATCH.fullmatch(batch_id)):
        raise MaskError(f"batch id must be 12 hex characters: {batch_id!r}")
    path = project / "work" / batch_id / "derived.jsonl"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MaskError(f"no derived images for batch {batch_id}: {exc}") from exc
    try:
        return [json.loads(x) for x in text.splitlines() if x.strip()]
    except json.JSONDecodeError as exc:
        raise MaskError(f"{path}: not JSON lines: {exc}") from exc


def mask_batch(project: str | os.PathLike, batch_id: str, *, engine: Any = None,
               rules: MaskRules | None = None) -> list[dict]:
    """Mask every page of ``work/<batch_id>/derived.jsonl``; returns the MANIFEST rows in page order.

    Every derived image is checked against its ``der_sha256`` before anything is written."""
    project = Path(project)
    rows = _derived_rows(project, batch_id)
    pages = []
    for der in rows:
        src = Path(der.get("path", ""))
        src = src if src.is_absolute() else project / src
        try:
            data = src.read_bytes()
        except OSError as exc:
            raise MaskError(f"cannot read derived image {src}: {exc}") from exc
        if _sha(data) != der.get("der_sha256"):
            raise MaskError(f"{src}: bytes do not match der_sha256 (changed since the batch)")
        pages.append((src, der))
    if engine is None:
        from klode_kit.ocr.engines import OcrMacEngine

        engine = OcrMacEngine()
    rules = rules or load_rules()
    return [mask_page(project, src, parent=der["parent"], page_id=der["page_id"], der_sha256=der["der_sha256"],
                      engine=engine, rules=rules) for src, der in pages]
