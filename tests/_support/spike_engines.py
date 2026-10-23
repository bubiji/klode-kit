"""WI-00c3 spike helpers: local OCR engines, gold text and the R3-05 geometry analysis (model lane only).

These are spike adapters, not the production adapters of WI-04a; they exist so that the four routes can be run
once on the hardest synthetic pages and their raw output recorded under ``tests/recordings/<engine>/``.

- Route A ``vision``: Apple Vision through ``ocrmac==1.0.1`` (``framework="vision"``, ``accurate``,
  ``zh-Hans`` + ``en-US``). One box per ``VNRecognizedTextObservation``; observations are grouped into lines by
  vertical overlap and joined left to right with a space.
- Route B ``mlx``: Unlimited-OCR 8bit through ``mlx-vlm==0.7.1`` Python API (the 189 project's settings: prompt
  ``document parsing.``, ``max_tokens`` 3072, temperature 0, snapshot ``6d2a0677…``), loaded with
  ``HF_HUB_OFFLINE=1`` so the local cache is used and nothing is downloaded. Layout tags are stripped and the
  HTML table flattened (cells -> spaces, rows -> lines; port of 189 ``engines.strip_layout_tags``).
  ``tesseract`` (``chi_sim+eng``, psm 6) is the fallback when MLX fails or degenerates
  (``klode_kit.ocr.degenerate``) and is always run for its word boxes (R3-05 corroboration).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from klode_kit.ocr.degenerate import is_degenerate, repeat_ratio

MLX_MODEL = "mlx-community/Unlimited-OCR-8bit"
MLX_SNAPSHOT = "6d2a0677ec509c87b16eb18be4b761e6fb2830c6"
MLX_PROMPT = "document parsing."
MLX_MAX_TOKENS = 3072
VISION_LANGS = ["zh-Hans", "en-US"]
TESS_LANG = "chi_sim+eng"
TESS_PSM = "6"
CONTAIN_PAD_PX = 6.0
RECORDING_SCHEMA = "klode-kit.replay_recording/1"

_DET = re.compile(r"<\|det\|>[^<]*?<\|/det\|>")
_REF = re.compile(r"<\|ref\|>.*?<\|/ref\|>", re.S)
_TOK = re.compile(r"<\|[^|>]{1,40}\|>")
_ROW_END = re.compile(r"</tr>\s*", re.I)
_CELL_END = re.compile(r"</t[dh]>\s*", re.I)
_HTML = re.compile(r"</?(?:table|thead|tbody|tr|td|th)\b[^>]*>", re.I)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# --------------------------------------------------------------------------- gold


def gold_lines(fixture_dir: Path, key: str) -> list[str]:
    """The drawn text of a page, in the generator's reading order: the simulated output of a route that has no
    injected deviation on this page reproduces the layout text exactly (``gen_engine_outputs.route_lines``)."""
    truth = json.loads((fixture_dir / "truth.json").read_text(encoding="utf-8"))
    dirty = {x["route"] for x in truth.get("injections", []) if x.get("key") == key}
    for route in ("vision", "claude", "codex", "mlx"):
        if route not in dirty:
            path = fixture_dir / truth.get("engine_dir", "engine") / route / f"{key}.jsonl"
            return [json.loads(x)["text"] for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    raise ValueError(f"{fixture_dir.name}/{key}: every simulated route carries an injection")


def segment_boxes(fixture_dir: Path, key: str) -> list[dict]:
    """Ground-truth drawn segments (text + pixel box) from the simulated vision tokens of the page."""
    path = fixture_dir / "engine" / "vision" / f"{key}.jsonl"
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = json.loads(raw)
        for t in line.get("tokens") or []:
            out.append({"line_idx": line["line_idx"], "span": t["span"], "text": t["text"], "bbox": t["bbox_px"]})
    return out


# --------------------------------------------------------------------------- route A: Vision


def vision_observations(png: Path) -> tuple[list[dict], dict]:
    from PIL import Image
    from ocrmac import ocrmac
    import importlib.metadata as md

    with Image.open(png) as im:
        width, height = im.size
    t = time.time()
    res = ocrmac.OCR(str(png), framework="vision", recognition_level="accurate",
                     language_preference=VISION_LANGS).recognize()
    obs = []
    for text, conf, (x, y, w, h) in res:
        box = [x * width, (1 - y - h) * height, (x + w) * width, (1 - y) * height]
        obs.append({"text": text, "confidence": float(conf), "bbox_px": [round(v, 1) for v in box]})
    meta = {"ocrmac": md.version("ocrmac"), "framework": "vision", "recognition_level": "accurate",
            "language_preference": VISION_LANGS, "size": [width, height], "sec": round(time.time() - t, 2)}
    return obs, meta


def group_lines(obs: list[dict], overlap: float = 0.5) -> list[str]:
    rows: list[dict] = []
    for o in sorted(obs, key=lambda o: (o["bbox_px"][1] + o["bbox_px"][3]) / 2):
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
    return [" ".join(i["text"] for i in sorted(r["items"], key=lambda i: i["bbox_px"][0])) for r in rows]


# --------------------------------------------------------------------------- route B: MLX + tesseract


def strip_layout_tags(text: str) -> str:
    text = _DET.sub("", text)
    text = _REF.sub("", text)
    text = _TOK.sub("", text)
    # cells before rows: in the 189 original the row break inserted for </tr> is swallowed by the following
    # "</td>\s*" substitution, so a whole table collapses into one line (found in this spike, 2026-10-23)
    text = _CELL_END.sub(" ", text)
    text = _ROW_END.sub("\n", text)
    text = _HTML.sub("", text)
    return text.strip() + "\n"


def mlx_lines(raw: str) -> list[str]:
    return [x.strip() for x in strip_layout_tags(raw).splitlines() if x.strip()]


class Mlx:
    def __init__(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import mlx_vlm
        from mlx_vlm import generate, load
        from mlx_vlm.prompt_utils import apply_chat_template
        from mlx_vlm.utils import load_config

        self.version = mlx_vlm.__version__
        self._generate = generate
        self._model, self._processor = load(MLX_MODEL, revision=MLX_SNAPSHOT)
        self._prompt = apply_chat_template(self._processor, load_config(MLX_MODEL, revision=MLX_SNAPSHOT),
                                           MLX_PROMPT, num_images=1)

    def params(self) -> dict:
        return {"model": MLX_MODEL, "snapshot": MLX_SNAPSHOT, "mlx_vlm": self.version, "prompt": MLX_PROMPT,
                "max_tokens": MLX_MAX_TOKENS, "temperature": 0.0, "offline": True}

    def ocr(self, png: Path) -> tuple[str, list[str], dict]:
        t = time.time()
        res = self._generate(self._model, self._processor, self._prompt, image=[str(png)],
                             max_tokens=MLX_MAX_TOKENS, temperature=0.0, verbose=False)
        raw = res.text if hasattr(res, "text") else str(res)
        lines = mlx_lines(raw)
        gen = getattr(res, "generation_tokens", None)
        ratio = repeat_ratio(lines)
        meta = {"sec": round(time.time() - t, 2), "generation_tokens": gen,
                "truncated": bool(gen is not None and gen >= MLX_MAX_TOKENS - 1),
                "repeat_ratio": ratio, "degenerate": is_degenerate(ratio)}
        return raw, lines, meta


def tesseract(png: Path) -> tuple[str, list[str], list[dict], dict]:
    t = time.time()
    tsv = subprocess.run(["tesseract", str(png), "stdout", "-l", TESS_LANG, "--psm", TESS_PSM, "tsv"],
                         capture_output=True, text=True, check=True).stdout
    version = subprocess.run(["tesseract", "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    words: list[dict] = []
    lines: dict[tuple, list[dict]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row.get("level") != "5" or not (row.get("text") or "").strip():
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        word = {"text": row["text"], "conf": float(row["conf"]), "bbox_px": [x, y, x + w, y + h]}
        words.append(word)
        lines.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append(word)
    text_lines = [" ".join(w["text"] for w in ws) for _, ws in sorted(lines.items(), key=lambda kv: tuple(map(int, kv[0])))]
    meta = {"version": version, "lang": TESS_LANG, "psm": TESS_PSM, "sec": round(time.time() - t, 2)}
    return tsv, text_lines, words, meta


# --------------------------------------------------------------------------- R3-05


def _center(b):
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2)


def _inside(pt, b, pad=2.0):
    return b[0] - pad <= pt[0] <= b[2] + pad and b[1] - pad <= pt[1] <= b[3] + pad


def _iou(a, b):
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _contains(outer, inner, pad=CONTAIN_PAD_PX):
    """``inner`` lies inside ``outer`` grown by ``pad`` px (Vision pads short glyphs by a few px)."""
    return (outer[0] - pad <= inner[0] and outer[1] - pad <= inner[1]
            and inner[2] <= outer[2] + pad and inner[3] <= outer[3] + pad)


def granularity(obs: list[dict], segments: list[dict], fields: list[dict], words: list[dict]) -> dict:
    """R3-05: is a Vision observation box a word (= one drawn segment)?

    - per observation: ``exact`` when it holds exactly one drawn segment (by segment centre) and its box localises
      it (IoU >= 0.5, or the segment box lies inside the observation box grown by ``CONTAIN_PAD_PX``);
      ``multi`` when it holds two or more segments (a token box would have to be estimated: ``approx``);
      ``other`` otherwise (stamp text, a vertical column, noise);
    - per truth field (table cell): the observation holding the cell's segment decides ``exact`` / ``approx`` /
      ``none``; an ``approx`` or ``none`` cell needs manual confirmation (``no_geometry``) unless the same row has
      an exact tesseract word box (the R3-05 corroboration rule).
    """
    kinds, ious = [], []
    for o in obs:
        inside = [s for s in segments if _inside(_center(s["bbox"]), o["bbox_px"])]
        iou = _iou(inside[0]["bbox"], o["bbox_px"]) if len(inside) == 1 else None
        ious.append(None if iou is None else round(iou, 3))
        if len(inside) == 1 and (iou >= 0.5 or _contains(o["bbox_px"], inside[0]["bbox"])):
            kinds.append("exact")
        elif len(inside) >= 2:
            kinds.append("multi")
        else:
            kinds.append("other")
    cells = []
    for f in fields:
        seg = next((s for s in segments if s["line_idx"] == f["line_idx"] and s["span"] == f["span"]), None)
        if seg is None:
            continue
        holder = [k for o, k in zip(obs, kinds) if _inside(_center(seg["bbox"]), o["bbox_px"])]
        geometry = "exact" if holder == ["exact"] else ("approx" if holder else "none")
        y0, y1 = seg["bbox"][1], seg["bbox"][3]
        corroborated = any(min(y1, w["bbox_px"][3]) - max(y0, w["bbox_px"][1]) > 0.5 * (y1 - y0) for w in words)
        manual = geometry != "exact" and not corroborated
        cells.append({"field_id": f["field_id"], "geometry": geometry, "tesseract_row_box": corroborated,
                      "manual": manual})
    count = lambda xs, v: sum(1 for x in xs if x == v)  # noqa: E731
    return {
        "observations": len(obs), "exact": count(kinds, "exact"), "multi": count(kinds, "multi"),
        "other": count(kinds, "other"), "kinds": kinds, "single_segment_iou": ious,
        "cells": cells, "cells_total": len(cells),
        "cells_exact": sum(1 for c in cells if c["geometry"] == "exact"),
        "cells_approx": sum(1 for c in cells if c["geometry"] == "approx"),
        "cells_none": sum(1 for c in cells if c["geometry"] == "none"),
        "cells_manual": sum(1 for c in cells if c["manual"]),
    }


def write_recording(path: Path, data: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": RECORDING_SCHEMA, "recorded": True, **data}, ensure_ascii=False,
                               indent=1) + "\n", encoding="utf-8")
    return path
