"""Local OCR engines, routes A and B (WI-04a; ported from 189 ``src/ocr/engines.py``).

Route A ``vision``: Apple Vision through ``ocrmac`` (``framework="vision"``, ``accurate``, Simplified Chinese +
Japanese + English). Route B ``mlx``: Unlimited-OCR 8bit through the ``mlx-vlm`` Python API (never the CLI,
189 E15/E19), primary prompt then alt prompt, then ``tesseract`` (``chi_sim+jpn``, tsv word boxes) as fallback.

Every engine returns ``ocr_line`` rows (design 02 r2.2-A, R2-08, R3-05): pixel boxes, origin top-left
(``klode_kit.ocr.geometry``); ``tokens`` with ``span`` into the line text and ``geometry``:

- vision: an ocrmac box is one ``VNRecognizedTextObservation``, not a word (WI-00c3 R3-05), so tokens are the
  whitespace-separated pieces of each observation with boxes estimated by character proportion -> ``approx``;
- tesseract: tsv word boxes -> ``exact``;
- mlx: no word boxes -> ``tokens: null`` and the ``<|det|>`` ``block_bbox`` (a one-line block also gives
  ``bbox_px``).

Changes against 189:

- ``strip_layout_tags`` handles table cells before rows. In 189 the ``</t[dh]>\\s*`` substitution swallowed the
  newline just inserted for ``</tr>`` and a whole table collapsed into one line (WI-00c3 F-1).
- MLX inline LaTeX units (``\\( 10^9/L \\)``) are flattened (WI-00c3 F-2).
- Degenerate output: the configured ``repeat_ratio_max`` (default 0.3, strictly greater) applies to the line
  level ``degenerate.repeat_ratio`` (WI-00a); 189's own check - 6-character n-gram repetition > 0.5 - is kept as
  a second signal, because the real MLX failure is one line repeating a phrase, where the line ratio is 0.
- The fallback after the alt prompt is tesseract (189: ``failed``); degenerate attempts are reported as
  ``degenerate_output`` and a page whose whole chain fails as page-level ``route_failed`` with no lines.
- MLX weights are loaded only from the local Hugging Face cache snapshot (gate G4: nothing is downloaded);
  ``mlx_weights_status`` is the check doctor (WI-10b) reports.
"""

from __future__ import annotations

import csv
import io
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from klode_kit.ocr import geometry as G
from klode_kit.ocr.degenerate import DEFAULT_MAX_RATIO, is_degenerate, ngram_repeat_ratio, repeat_ratio

VISION_LANGS = ("zh-Hans", "ja-JP", "en-US")
VISION_LEVEL = "accurate"
VISION_ROW_OVERLAP = 0.5
TESS_LANG = "chi_sim+jpn"
TESS_PSM = "6"
MLX_MODEL = "mlx-community/Unlimited-OCR-8bit"
MLX_SNAPSHOT = "6d2a0677ec509c87b16eb18be4b761e6fb2830c6"
MLX_PROMPT = "document parsing."
MLX_ALT_PROMPT = "Free OCR."
MLX_MAX_TOKENS = 3072
NGRAM_N = 6
NGRAM_MAX_RATIO = 0.5
DOCTOR_HINT = "run `klode-kit doctor`; weights are never downloaded automatically (gate G4)"


class EngineUnavailable(RuntimeError):
    """A local engine (binary, package or language pack) is not usable on this machine."""


class MlxWeightsMissing(EngineUnavailable):
    """The Unlimited-OCR snapshot is not in the local Hugging Face cache."""


# --------------------------------------------------------------------------- ocr_line rows


def _line(engine: str, page_id: str, der: str, idx: int, text: str, bbox, tokens, block=None) -> dict:
    row: dict[str, Any] = {
        "engine": engine,
        "page_id": page_id,
        "der_sha256": der,
        "line_idx": idx,
        "text": text,
        "bbox_px": bbox,
        "tokens": tokens,
    }
    if block is not None:
        row["block_bbox"] = block
    return row


# --------------------------------------------------------------------------- route A: Vision


def group_rows(observations: Sequence[dict], overlap: float = VISION_ROW_OVERLAP) -> list[list[dict]]:
    """189 ``OcrMacEngine._rows`` in pixel space: observations whose vertical overlap is at least ``overlap`` of
    the smaller height form a row; rows top to bottom, observations left to right."""
    rows: list[dict] = []
    for o in sorted(observations, key=lambda o: (o["bbox_px"][1] + o["bbox_px"][3]) / 2):
        y0, y1 = o["bbox_px"][1], o["bbox_px"][3]
        for r in rows:
            ov = min(r["y1"], y1) - max(r["y0"], y0)
            if ov > 0 and ov / max(1e-6, min(y1 - y0, r["y1"] - r["y0"])) >= overlap:
                r["items"].append(o)
                r["y0"], r["y1"] = min(r["y0"], y0), max(r["y1"], y1)
                break
        else:
            rows.append({"y0": y0, "y1": y1, "items": [o]})
    rows.sort(key=lambda r: (r["y0"] + r["y1"]) / 2)
    return [sorted(r["items"], key=lambda o: o["bbox_px"][0]) for r in rows]


def vision_lines(observations: Iterable[dict], size: Sequence[int], *, page_id: str, der_sha256: str) -> list[dict]:
    """Vision observations (``{"text", "bbox_px"}``, pixels, top-left) -> ``ocr_line`` rows, approx tokens."""
    obs = [
        {"text": o["text"], "bbox_px": G.clamp_px(o["bbox_px"], size)}
        for o in observations
        if str(o.get("text", "")).strip()
    ]
    out = []
    for idx, row in enumerate(group_rows(obs)):
        text, tokens = "", []
        for o in row:
            if text:
                text += " "
            base = len(text)
            for m in re.finditer(r"\S+", o["text"]):
                tokens.append({
                    "text": m.group(),
                    "bbox_px": G.span_box(o["bbox_px"], m.start(), m.end(), len(o["text"])),
                    "span": [base + m.start(), base + m.end()],
                    "geometry": "approx",
                })
            text += o["text"]
        out.append(_line("vision", page_id, der_sha256, idx, text, G.union(o["bbox_px"] for o in row), tokens))
    return out


def _ocrmac_recognize(image: str, langs: Sequence[str], level: str) -> list:
    try:
        from ocrmac import ocrmac
    except ImportError as exc:
        raise EngineUnavailable(f"ocrmac is not installed (dependency group ocr-local): {exc}") from exc
    return ocrmac.OCR(image, framework="vision", recognition_level=level, language_preference=list(langs)).recognize()


class OcrMacEngine:
    """Route A. ``recognize(image, langs, level)`` is the Vision call; it returns ocrmac's
    ``[(text, confidence, (x, y, w, h)), ...]`` with normalised, bottom-left boxes."""

    name = "vision"

    def __init__(self, langs: Sequence[str] = VISION_LANGS, level: str = VISION_LEVEL,
                 recognize: Callable[[str, Sequence[str], str], list] | None = None):
        self.langs, self.level = tuple(langs), level
        self._recognize = recognize or _ocrmac_recognize

    @property
    def params(self) -> dict:
        return {"engine": self.name, "langs": list(self.langs), "level": self.level, "framework": "vision"}

    def ocr_page(self, png: str | Path, *, page_id: str, der_sha256: str) -> list[dict]:
        size = G.image_size(png)
        res = self._recognize(str(png), self.langs, self.level)
        obs = [{"text": text, "bbox_px": G.vision_to_px(box, size)} for text, _conf, box in res]
        return vision_lines(obs, size, page_id=page_id, der_sha256=der_sha256)


# --------------------------------------------------------------------------- route B: MLX text


_DET = re.compile(r"<\|det\|>\s*(?P<kind>[^\[<]*?)\s*\[(?P<box>[^\]]*)\]\s*<\|/det\|>")
_DET_ANY = re.compile(r"<\|det\|>[^<]*?<\|/det\|>")
_REF = re.compile(r"<\|ref\|>.*?<\|/ref\|>", re.S)
_TOK = re.compile(r"<\|[^|>]{1,40}\|>")
_ROW_END = re.compile(r"</tr>\s*", re.I)
_CELL_END = re.compile(r"</t[dh]>\s*", re.I)
_HTML = re.compile(r"</?(?:table|thead|tbody|tr|td|th)\b[^>]*>", re.I)
_MATH = re.compile(r"\\\((.*?)\\\)|\\\[(.*?)\\\]", re.S)
_MATH_WRAP = re.compile(r"\\(?:mathrm|text|mathbf|mathit|operatorname|rm)\s*\{([^{}]*)\}")
_MATH_SYM = {r"\times": "×", r"\cdot": "·", r"\%": "%", r"\mu": "μ", r"\pm": "±", r"\leq": "≤", r"\geq": "≥",
             r"\le": "≤", r"\ge": "≥", r"\sim": "~"}


def strip_layout_tags(text: str) -> str:
    """Remove layout tags without changing a character of content; flatten HTML tables to text
    (cells -> one space, rows -> lines). Cells first: see the module docstring (F-1)."""
    text = _DET_ANY.sub("", text)
    text = _REF.sub("", text)
    text = _TOK.sub("", text)
    text = _CELL_END.sub(" ", text)
    text = _ROW_END.sub("\n", text)
    text = _HTML.sub("", text)
    return text.strip() + "\n"


def _flatten_math(body: str) -> str:
    prev = None
    while prev != body:
        prev, body = body, _MATH_WRAP.sub(r"\1", body)
    for k in sorted(_MATH_SYM, key=len, reverse=True):
        body = body.replace(k, _MATH_SYM[k])
    return re.sub(r"\s+", "", body.replace("{", "").replace("}", ""))


def flatten_latex(text: str) -> str:
    """Inline LaTeX spans ``\\( ... \\)`` / ``\\[ ... \\]`` -> plain text (F-2); text outside math is unchanged."""
    return _MATH.sub(lambda m: _flatten_math(m.group(1) if m.group(1) is not None else m.group(2)), text)


def _text_lines(chunk: str) -> list[str]:
    return [flatten_latex(x.strip()) for x in strip_layout_tags(chunk).splitlines() if x.strip()]


def _det_box(box: str, size: Sequence[int]) -> list[int] | None:
    try:
        nums = [float(v) for v in box.split(",")]
        return G.mlx_to_px(nums, size)
    except ValueError:
        return None


def mlx_lines(raw: str, size: Sequence[int], *, page_id: str, der_sha256: str) -> list[dict]:
    """Raw Unlimited-OCR output -> ``ocr_line`` rows; each line keeps the pixel box of its ``<|det|>`` block."""
    parts: list[tuple[list[int] | None, str]] = []
    pos, block = 0, None
    for m in _DET.finditer(raw):
        parts.append((block, raw[pos:m.start()]))
        block, pos = _det_box(m.group("box"), size), m.end()
    parts.append((block, raw[pos:]))
    out: list[dict] = []
    for box, chunk in parts:
        texts = _text_lines(chunk)
        for text in texts:
            line_box = box if box is not None and len(texts) == 1 else None
            out.append(_line("mlx", page_id, der_sha256, len(out), text, line_box, None, box))
    return out


# --------------------------------------------------------------------------- route B: tesseract


def tesseract_lines(tsv: str, size: Sequence[int], *, page_id: str, der_sha256: str) -> list[dict]:
    """tesseract tsv -> ``ocr_line`` rows: words of one (block, par, line) joined by a space, exact tokens."""
    groups: dict[tuple[str, str, str], list[dict]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        box = G.tsv_to_px([int(row[k]) for k in ("left", "top", "width", "height")], size)
        groups.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append(
            {"text": row["text"], "bbox_px": box})
    out = []
    for idx, words in enumerate(groups.values()):
        text, tokens = "", []
        for w in words:
            if text:
                text += " "
            tokens.append({"text": w["text"], "bbox_px": w["bbox_px"], "span": [len(text), len(text) + len(w["text"])],
                           "geometry": "exact"})
            text += w["text"]
        out.append(_line("tesseract", page_id, der_sha256, idx, text, G.union(w["bbox_px"] for w in words), tokens))
    return out


def _tesseract_tsv(image: str, lang: str, psm: str) -> str:
    try:
        res = subprocess.run(["tesseract", image, "stdout", "-l", lang, "--psm", str(psm), "tsv"],
                             capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise EngineUnavailable("tesseract is not on PATH") from exc
    if res.returncode != 0:
        raise EngineUnavailable(f"tesseract exit {res.returncode}: {res.stderr.strip()[:300]}")
    return res.stdout


class TesseractEngine:
    """Route B fallback. ``run_tsv(image, lang, psm)`` is the tesseract process; it returns the tsv text."""

    name = "tesseract"

    def __init__(self, lang: str = TESS_LANG, psm: str = TESS_PSM,
                 run_tsv: Callable[[str, str, str], str] | None = None):
        self.lang, self.psm = lang, str(psm)
        self._run = run_tsv or _tesseract_tsv

    @property
    def params(self) -> dict:
        return {"engine": self.name, "lang": self.lang, "psm": self.psm}

    def ocr_page(self, png: str | Path, *, page_id: str, der_sha256: str) -> list[dict]:
        size = G.image_size(png)
        return tesseract_lines(self._run(str(png), self.lang, self.psm), size, page_id=page_id, der_sha256=der_sha256)


# --------------------------------------------------------------------------- route B: MLX engine


@dataclass(frozen=True)
class WeightsStatus:
    ok: bool
    path: Path
    problem: str | None = None


def hub_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def mlx_weights_status(model: str = MLX_MODEL, snapshot: str = MLX_SNAPSHOT,
                       hub_cache: str | Path | None = None) -> WeightsStatus:
    """Is the pinned snapshot complete in the local cache? Reads the file system only (no network)."""
    root = Path(hub_cache) if hub_cache is not None else hub_cache_dir()
    snap = root / ("models--" + model.replace("/", "--")) / "snapshots" / snapshot
    if not snap.is_dir():
        return WeightsStatus(False, snap, f"snapshot {snapshot[:12]} of {model} not in {root}; {DOCTOR_HINT}")
    if not (snap / "config.json").is_file():
        return WeightsStatus(False, snap, f"{snap}/config.json missing; {DOCTOR_HINT}")
    weights = sorted(snap.glob("*.safetensors"))
    if not weights or not all(w.is_file() and w.stat().st_size > 0 for w in weights):
        return WeightsStatus(False, snap, f"model *.safetensors missing or dangling in {snap}; {DOCTOR_HINT}")
    return WeightsStatus(True, snap)


class _MlxRuntime:
    """The real mlx-vlm generate call, loaded once from the local snapshot directory."""

    def __init__(self, snap: Path, prompts: Sequence[str], max_tokens: int, temperature: float):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        try:
            import mlx_vlm
            from mlx_vlm import generate, load
            from mlx_vlm.prompt_utils import apply_chat_template
            from mlx_vlm.utils import load_config
        except ImportError as exc:
            raise EngineUnavailable(f"mlx-vlm is not installed (dependency group ocr-local): {exc}") from exc
        self.version = mlx_vlm.__version__
        self._generate = generate
        self._model, self._processor = load(str(snap))
        config = load_config(str(snap))
        self._templates = {p: apply_chat_template(self._processor, config, p, num_images=1) for p in prompts}
        self.max_tokens, self.temperature = max_tokens, temperature

    def __call__(self, prompt: str, image: str) -> tuple[str, int | None]:
        res = self._generate(self._model, self._processor, self._templates[prompt], image=[image],
                             max_tokens=self.max_tokens, temperature=self.temperature, verbose=False)
        raw = res.text if hasattr(res, "text") else str(res)
        return raw, getattr(res, "generation_tokens", None)


class MlxEngine:
    """Route B. ``generate(prompt, image) -> (raw, generation_tokens | None)`` is the model call; without it the
    real mlx-vlm runtime is loaded lazily, from the local cache only."""

    name = "mlx"

    def __init__(self, prompt: str = MLX_PROMPT, alt_prompt: str = MLX_ALT_PROMPT, max_tokens: int = MLX_MAX_TOKENS,
                 temperature: float = 0.0, model: str = MLX_MODEL, snapshot: str = MLX_SNAPSHOT,
                 hub_cache: str | Path | None = None,
                 generate: Callable[[str, str], tuple[str, int | None]] | None = None):
        self.prompt, self.alt_prompt = prompt, alt_prompt
        self.max_tokens, self.temperature = max_tokens, temperature
        self.model, self.snapshot, self.hub_cache = model, snapshot, hub_cache
        self._generate = generate

    @property
    def params(self) -> dict:
        return {"engine": self.name, "model": self.model, "snapshot": self.snapshot, "prompt": self.prompt,
                "alt_prompt": self.alt_prompt, "max_tokens": self.max_tokens, "temperature": self.temperature}

    def _runtime(self) -> Callable[[str, str], tuple[str, int | None]]:
        if self._generate is None:
            status = mlx_weights_status(self.model, self.snapshot, self.hub_cache)
            if not status.ok:
                raise MlxWeightsMissing(status.problem)
            self._generate = _MlxRuntime(status.path, (self.prompt, self.alt_prompt), self.max_tokens,
                                         self.temperature)
        return self._generate

    def run(self, png: str | Path, *, alt: bool, page_id: str, der_sha256: str) -> tuple[list[dict], dict]:
        prompt = self.alt_prompt if alt else self.prompt
        raw, gen_tokens = self._runtime()(prompt, str(png))
        lines = mlx_lines(raw, G.image_size(png), page_id=page_id, der_sha256=der_sha256)
        truncated = bool(gen_tokens is not None and gen_tokens >= self.max_tokens - 1)
        return lines, {"prompt": prompt, "generation_tokens": gen_tokens, "truncated": truncated}

    def ocr_page(self, png: str | Path, *, page_id: str, der_sha256: str, alt: bool = False) -> list[dict]:
        return self.run(png, alt=alt, page_id=page_id, der_sha256=der_sha256)[0]


# --------------------------------------------------------------------------- route B chain


@dataclass
class PageResult:
    """Outcome of one page on route B. ``lines`` is ``None`` (never ``[]``) when the route failed."""

    status: str
    engine: str | None
    lines: list[dict] | None
    attempts: list[dict] = field(default_factory=list)
    uncertain: list[dict] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def assess(lines: Sequence[dict], max_ratio: float = DEFAULT_MAX_RATIO) -> dict:
    texts = [line["text"] for line in lines]
    ratio = repeat_ratio(texts)
    ngram = ngram_repeat_ratio("\n".join(texts), NGRAM_N)
    return {"repeat_ratio": ratio, "ngram_ratio": round(ngram, 4),
            "degenerate": is_degenerate(ratio, max_ratio) or ngram > NGRAM_MAX_RATIO}


def ocr_page(png: str | Path, *, page_id: str, der_sha256: str, mlx: MlxEngine, fallback: TesseractEngine,
             max_ratio: float = DEFAULT_MAX_RATIO) -> PageResult:
    """Route B for one page: MLX primary prompt -> MLX alt prompt -> tesseract, each step only when the previous
    one degenerated (``repeat_ratio > max_ratio`` or 6-gram ratio > 0.5) or raised."""
    steps: list[tuple[str, str | None, Callable[[], list[dict]]]] = [
        ("mlx", mlx.prompt, lambda: mlx.run(png, alt=False, page_id=page_id, der_sha256=der_sha256)[0]),
        ("mlx", mlx.alt_prompt, lambda: mlx.run(png, alt=True, page_id=page_id, der_sha256=der_sha256)[0]),
        ("tesseract", None, lambda: fallback.ocr_page(png, page_id=page_id, der_sha256=der_sha256)),
    ]
    attempts: list[dict] = []
    for engine, prompt, call in steps:
        attempt: dict[str, Any] = {"engine": engine, "prompt": prompt}
        try:
            lines = call()
        except MlxWeightsMissing:
            raise
        except Exception as exc:  # an engine failure is an attempt, not a crash
            attempt.update({"repeat_ratio": None, "ngram_ratio": None, "degenerate": False,
                            "error": f"{type(exc).__name__}: {exc}"})
            attempts.append(attempt)
            continue
        attempt.update(assess(lines, max_ratio))
        attempt["error"] = None
        attempts.append(attempt)
        if not attempt["degenerate"]:
            return PageResult("ok", engine, lines, attempts, _uncertain(page_id, attempts, cleared=True))
    return PageResult("route_failed", None, None, attempts, _uncertain(page_id, attempts, cleared=False))


def _uncertain(page_id: str, attempts: list[dict], *, cleared: bool) -> list[dict]:
    out: list[dict] = []
    if any(a["engine"] == "mlx" and a["degenerate"] for a in attempts):
        item = {"id": f"{page_id}:mlx:degenerate_output", "reason": "degenerate_output", "scope": "page",
                "engine": "mlx"}
        if cleared:
            final = attempts[-1]
            item.update({"cleared_by": "auto",
                         "cleared_evidence": f"{final['engine']} {final['prompt'] or ''}".strip()})
        out.append(item)
    if not cleared:
        reasons = "; ".join(
            f"{a['engine']}{'/' + a['prompt'] if a['prompt'] else ''}: "
            + (a["error"] or f"degenerate repeat_ratio={a['repeat_ratio']} ngram_ratio={a['ngram_ratio']}")
            for a in attempts)
        out.append({"id": f"{page_id}:mlx:route_failed", "reason": "route_failed", "scope": "page", "engine": "mlx",
                    "fix_action": f"rerun route B for this page (failed: {reasons})"})
    return out
