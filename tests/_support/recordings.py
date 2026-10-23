"""Replayed route outputs of the seven WI-00c3 recorded pages (``tests/recordings/<engine>/<der_sha256>.json``),
turned into ``ocr_line`` rows by the production converters. Shared by the WI-07c contract tests (the WI-07b test
keeps its own copy, which is a recorded specification file)."""

from __future__ import annotations

import json
from pathlib import Path

from _support.flows import REPO
from klode_kit.ocr import adapters_claude as AC
from klode_kit.ocr import adapters_codex as AX
from klode_kit.ocr import engines as E
from klode_kit.ocr import geometry as G

REC = REPO / "tests" / "recordings"
FIXTURES = REPO / "tests" / "fixtures"
SHAS = sorted(p.stem for p in (REC / "mlx").glob("*.json"))


def recording(engine: str, sha: str) -> dict:
    return json.loads((REC / engine / f"{sha}.json").read_text(encoding="utf-8"))


def route_rows(engine: str, sha: str) -> list[dict]:
    rec = recording(engine, sha)
    if engine in ("vision", "mlx", "tesseract"):
        size = G.image_size(FIXTURES / rec["image"])
        conv = {"vision": E.vision_lines, "mlx": E.mlx_lines, "tesseract": E.tesseract_lines}[engine]
        return conv(rec["raw"], size, page_id="d-000000000000/p001", der_sha256=sha)
    parsed = AC.parse_result(rec["stdout"]) if engine == "claude" else AX.parse_events(rec["stdout"])
    assert parsed.lines is not None, parsed.problem
    mod = AC if engine == "claude" else AX
    return mod.to_rows(parsed.lines, page_id="d-000000000000/p001", der_sha256=sha)


def page_of(sha: str) -> tuple[str, str]:
    """(fixture, page key) of a recorded page."""
    image = Path(recording("mlx", sha)["image"])
    return image.parts[0], image.name.split(".")[0]
