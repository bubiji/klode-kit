"""A replay directory (``KLODE_KIT_ENGINE_REPLAY``) built from the WI-00c3 *real* four-route recordings
(``tests/recordings``) of one synthetic fixture, instead of the designed ``engine/`` rows of the fixture (WI-32b).

The recorded rows are what the real engines returned: Vision word boxes are all ``approx``, there is no tesseract
route (it is not one of the four), so every cell has ``no_geometry`` - the situation of the real U1 run that listed
65 disputes. Only the page id is rewritten (the converters stamp a placeholder document id).
"""

from __future__ import annotations

import json
from pathlib import Path

from _support import recordings as R

ROUTES4 = ("vision", "mlx", "claude", "codex")


def pages_of(fixture: str) -> dict[str, str]:
    """``{page key: der_sha256}`` of the recorded pages of ``fixture``."""
    out = {}
    for sha in R.SHAS:
        fx, key = R.page_of(sha)
        if fx == fixture:
            out[key] = sha
    return dict(sorted(out.items()))


def route_lines(engine: str, sha: str, key: str) -> list[dict]:
    rows = R.route_rows(engine, sha)
    page_id = f"d-{sha[:12]}/p001"   # every screenshot is a one-page document
    return [{**r, "page_id": page_id} for r in rows]


def build(dest: Path, fixture: str, routes=ROUTES4) -> Path:
    for key, sha in pages_of(fixture).items():
        for engine in routes:
            d = dest / engine
            d.mkdir(parents=True, exist_ok=True)
            text = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in route_lines(engine, sha, key))
            (d / f"{key}.jsonl").write_text(text, encoding="utf-8")
    return dest
