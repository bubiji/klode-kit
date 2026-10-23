"""WI-07b: alignment on replayed route outputs vs the fixtures' truth (contract_replay).

Two inputs, same entry point ``klode_kit.audit.align.align``:

1. **Real recordings** (WI-00c3, ``tests/recordings/{vision,tesseract,mlx,claude,codex}/<der_sha256>.json``)
   turned into ``ocr_line`` rows by the production converters (``engines.vision_lines`` / ``tesseract_lines`` /
   ``mlx_lines`` incl. the F-1/F-2 fixes, ``adapters_claude.parse_result``, ``adapters_codex.parse_events``).
   Vision tokens are all ``approx`` in production, so the base route is tesseract (exact word boxes). A truth
   cell is located on the alignment, not on raw lines: the ``n``-th row whose ``item_label`` is the truth item's
   slug (``items.yaml``); ``value`` = the drug cell's ``dose`` on a drug row, else the first number cell's
   ``norm``; ``unit`` = that number's glued ``unit``, else the first unit cell's ``norm``.
   Known misreads are pinned exactly as WI-07a pinned them; the WI-07a F-3 entries (MLX prints the rx-stamp
   table column by column) are **not** misreads here: WI-07b rebuilds the rows from the per-cell boxes, so MLX
   must read the three doses correctly.
2. **Simulated fixture routes** (``tests/fixtures/<fx>/engine/<route>/<key>.jsonl``, base = vision with exact
   tokens): every truth field's per-route reading (``truth.json`` ``routes[engine] = {value, line_idx, span}``)
   must be the candidate of the cell on row ``lNN`` (``value`` -> first number cell, ``unit`` -> first unit
   cell), with the same ``line_idx`` and ``span``; a route the truth lists as missing has no candidate.
   fx-colshift is the WI-07c contract sample and is not used here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit import items as items_mod
from klode_kit.audit import align as A
from klode_kit.ocr import adapters_claude as AC
from klode_kit.ocr import adapters_codex as AX
from klode_kit.ocr import engines as E
from klode_kit.ocr import geometry as G

pytestmark = pytest.mark.contract_replay

REC = REPO / "tests" / "recordings"
FIXTURES = REPO / "tests" / "fixtures"
ROUTES = ("vision", "mlx", "claude", "codex")
SHAS = sorted(p.stem for p in (REC / "mlx").glob("*.json"))

# (fixture, page key, route, item, row, col) -> what this route really reads (WI-07a pins, F-3 entries dropped)
KNOWN_MISREADS: dict[tuple[str, str, str, str, int, str], str | None] = {
    ("fx-u1-3labs", "p001", "vision", "白细胞", 1, "unit"): None,
    ("fx-u1-3labs", "p002", "vision", "白细胞", 1, "unit"): None,
    ("fx-u1-3labs", "p003", "vision", "白细胞", 1, "unit"): None,
    ("fx-rotated90", "p001", "vision", "白细胞", 1, "unit"): None,
    ("fx-vertical", "p001", "vision", "血小板", 3, "unit"): None,
    ("fx-lowres", "p001", "vision", "白细胞", 1, "unit"): None,
    ("fx-lowres", "p001", "vision", "红细胞", 2, "unit"): None,
    ("fx-rx-stamp", "p001", "vision", "示例片", 0, "value"): "0mg",
    ("fx-rx-stamp", "p001", "vision", "示例片", 1, "value"): None,
    ("fx-rx-stamp", "p001", "vision", "示例片", 2, "value"): "8.25g",
}

FIXTURE_SCENES = ("fx-labs-1x", "fx-labs-3x", "fx-dup10", "fx-swap", "fx-missingrow", "fx-manualfix", "fx-redo",
                  "fx-rx-stamp", "fx-lowres", "fx-vertical", "fx-rotated90", "fx-u1-3labs", "fx-u3-mixed", "fx-u8",
                  "fx-approx-only")

# (fixture, field_id, route) -> (value, line_idx, span) the cell really holds, where kind-ordinal placement differs
# from the generator's column notion: a token that is not a number/unit leaves the cell to the next field of that
# kind (a disagreement, never an agreement).
FIXTURE_PINS: dict[tuple[str, str, str], tuple[str, int, list[int]] | None] = {
    # mlx reads 5mg as "5rng" (not a number): the row's first number is the 2 of 每日2次
    ("fx-rx-stamp", "p001-l06:value", "mlx"): ("2", 6, [11, 12]),
    # mlx reads % as ‰, which is not a unit: the row has no unit cell for mlx
    ("fx-u1-3labs", "p001-l09:unit", "mlx"): None,
}


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
    image = Path(recording("mlx", sha)["image"])
    return image.parts[0], image.name.split(".")[0]


def read(al: A.Alignment, row: int, col: str, route: str) -> str | None:
    def cand(kind: str):
        c = next((c for c in al.cells if (c.row, c.kind, c.k) == (row, kind, 0)), None)
        return c.candidate(route) if c is not None else None

    num = cand("number")
    if col == "value":
        drug = next((c for c in al.cells if (c.row, c.kind) == (row, "drug")), None)
        if drug is not None:
            d = drug.candidate(route)
            return d.dose if d is not None else None
        return num.norm if num is not None else None
    if num is not None and num.unit is not None:
        return num.unit
    unit = cand("unit")
    return unit.norm if unit is not None else None


def test_recordings_cover_seven_pages_five_routes():
    assert len(SHAS) == 7
    for engine in (*ROUTES, "tesseract"):
        assert {p.name.split(".")[0] for p in (REC / engine).glob("*.json")} >= set(SHAS)


def test_recorded_pages_align_to_truth():
    """07b contract (L1-S2): on all seven recorded pages, aligning vision/tesseract/mlx/claude/codex puts every
    truth cell's reading of each of the four routes on the truth row - equal to truth, except the pinned misreads
    which read exactly as pinned (incl. MLX's column-wise rx-stamp table, rebuilt into rows). Every truth row has
    exact geometry and no uncertain code."""
    items = items_mod.default()
    checked, seen_known, wrong = 0, set(), []
    for sha in SHAS:
        fixture, key = page_of(sha)
        truth = json.loads((FIXTURES / fixture / "truth.json").read_text(encoding="utf-8"))
        cells = [f for f in truth["fields"] if f["key"] == key]
        assert cells, (fixture, key)
        al = A.align({e: route_rows(e, sha) for e in (*ROUTES, "tesseract")}, items=items)
        assert al.base == "tesseract", (fixture, key)
        for cell in cells:
            slug = items.resolve(cell["item"])[0]
            occurrence = len({c["row"] for c in cells if c["item"] == cell["item"] and c["row"] < cell["row"]})
            rows = [r.row for r in al.rows if r.item_label == slug]
            assert occurrence < len(rows), (fixture, key, cell["item"], rows)
            row = rows[occurrence]
            assert al.rows[row].exact, (fixture, key, row)
            assert [code for code, r in al.uncertain if r == row] == [], (fixture, key, row, al.uncertain)
            for route in ROUTES:
                got = read(al, row, cell["col"], route)
                k = (fixture, key, route, cell["item"], cell["row"], cell["col"])
                want = KNOWN_MISREADS[k] if k in KNOWN_MISREADS else cell["truth"]
                if k in KNOWN_MISREADS:
                    seen_known.add(k)
                if got != want:
                    wrong.append((k, got, want))
                checked += 1
    assert wrong == []
    assert seen_known == set(KNOWN_MISREADS)
    assert checked >= 200


def test_recorded_rx_stamp_mlx_rows_rebuilt():
    """F-3 (L1-S1): on the recorded rx-stamp page MLX's twelve one-cell boxes become three table rows; each
    dose candidate points into its own one-cell MLX line."""
    sha = next(s for s in SHAS if page_of(s)[0] == "fx-rx-stamp")
    mlx = route_rows("mlx", sha)
    al = A.align({e: route_rows(e, sha) for e in (*ROUTES, "tesseract")})
    doses = []
    for row in (r.row for r in al.rows if r.item_label == "sample-tablet"):
        num = next(c for c in al.cells if (c.row, c.kind, c.k) == (row, "number", 0)).candidate("mlx")
        s, e = num.span
        assert mlx[num.line_idx]["text"][s:e] == num.value
        doses.append(num.value)
    assert doses == ["10mg", "5mg", "0.25g"]


def fixture_routes(name: str, key: str) -> dict[str, list[dict]]:
    out = {}
    for route in ROUTES:
        path = FIXTURES / name / "engine" / route / f"{key}.jsonl"
        out[route] = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return out


@pytest.mark.parametrize("name", FIXTURE_SCENES)
def test_fixture_routes_align_to_truth_spans(name):
    """07b contract (L1-S2): simulated four-route pages - every truth field's per-route reading is that route's
    candidate in the cell on the truth's vision row, with the truth's line_idx and span (merged codex line of
    fx-missingrow included); a route the truth lists as missing has no candidate."""
    truth = json.loads((FIXTURES / name / "truth.json").read_text(encoding="utf-8"))
    wrong, checked = [], 0
    for key in sorted({f["key"] for f in truth["fields"]}):
        al = A.align(fixture_routes(name, key))
        assert al.base == "vision"
        for f in (f for f in truth["fields"] if f["key"] == key):
            row = int(f["field_id"].split("-l")[1].split(":")[0])
            kind = "number" if f["col"] == "value" else "unit"
            cell = next((c for c in al.cells if (c.row, c.kind, c.k) == (row, kind, 0)), None)
            for route in ROUTES:
                pin_key = (name, f["field_id"], route)
                if pin_key in FIXTURE_PINS:
                    want = FIXTURE_PINS[pin_key]
                else:
                    r = f["routes"].get(route)
                    want = None if r is None else (r["value"], r["line_idx"], r["span"])
                cand = cell.candidate(route) if cell is not None else None
                got = None if cand is None else (cand.value, cand.line_idx, list(cand.span))
                if got != want:
                    wrong.append((key, f["field_id"], route, got, want))
                checked += 1
    assert wrong == []
    assert checked >= 4
