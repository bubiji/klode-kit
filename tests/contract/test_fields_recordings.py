"""WI-07a: field extraction on real recorded OCR lines vs the fixtures' truth (contract_replay regression).

Input: the WI-00c3 recordings ``tests/recordings/{vision,mlx,claude,codex}/<der_sha256>.json`` (the four routes),
turned into lines by the production converters (``engines.vision_lines`` / ``engines.mlx_lines`` incl. the F-1 table
fix and the F-2 LaTeX flattening, ``adapters_claude.parse_result``, ``adapters_codex.parse_events``).
Truth: ``tests/fixtures/<fx>/truth.json`` (``value`` / ``unit`` columns) and ``scenario.json`` (page date).

How a truth cell is read from one route (WI-07b does the real alignment; this is only the regression probe):

- the row: the ``row``-th line (0-based) whose text contains the item label (``truth.item``);
- ``value``: on a line with a drug field, that drug's ``dose``; otherwise the ``norm`` of the first ``number`` field;
- ``unit``: the glued ``unit`` of that number, otherwise the ``norm`` of the very next field if it is a ``unit``;
- the page date: the ``norm`` of the first ``date`` field on the first line containing 日期.

Known misreads are pinned below with the exact value read (``None`` = nothing usable): a regression and a
silent "fix" (e.g. normalising ``10~9/L`` into ``10^9/L``, which would make a misread vote agree) both fail.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.flows import REPO
from klode_kit.audit.fields import extract
from klode_kit.ocr import adapters_claude as AC
from klode_kit.ocr import adapters_codex as AX
from klode_kit.ocr import engines as E
from klode_kit.ocr import geometry as G

pytestmark = pytest.mark.contract_replay

REC = REPO / "tests" / "recordings"
FIXTURES = REPO / "tests" / "fixtures"
ROUTES = ("vision", "mlx", "claude", "codex")
SHAS = sorted(p.stem for p in (REC / "mlx").glob("*.json"))

# (fixture, page key, route, item, row, col) -> what this route really reads (truth differs)
KNOWN_MISREADS: dict[tuple[str, str, str, str, int, str], str | None] = {
    # vision reads 10^9/L as 10~9/L (WI-00c3 spike: 6 times); not normalised on purpose
    ("fx-u1-3labs", "p001", "vision", "白细胞", 1, "unit"): None,
    ("fx-u1-3labs", "p002", "vision", "白细胞", 1, "unit"): None,
    ("fx-u1-3labs", "p003", "vision", "白细胞", 1, "unit"): None,
    ("fx-rotated90", "p001", "vision", "白细胞", 1, "unit"): None,
    ("fx-vertical", "p001", "vision", "血小板", 3, "unit"): None,
    ("fx-lowres", "p001", "vision", "白细胞", 1, "unit"): None,
    # fx-lowres: vision 10^12/L -> 10^12亿L
    ("fx-lowres", "p001", "vision", "红细胞", 2, "unit"): None,
    # fx-rx-stamp: vision 10mg -> 0mg, stamp over row 2 (示們奇用章), 0.25g -> 8.25g
    ("fx-rx-stamp", "p001", "vision", "示例片", 0, "value"): "0mg",
    ("fx-rx-stamp", "p001", "vision", "示例片", 1, "value"): None,
    ("fx-rx-stamp", "p001", "vision", "示例片", 2, "value"): "8.25g",
    # fx-rx-stamp: mlx outputs the table column by column (F-3; row rebuild is WI-07b's job)
    ("fx-rx-stamp", "p001", "mlx", "示例片", 0, "value"): None,
    ("fx-rx-stamp", "p001", "mlx", "示例片", 1, "value"): None,
    ("fx-rx-stamp", "p001", "mlx", "示例片", 2, "value"): None,
}


def recording(engine: str, sha: str) -> dict:
    return json.loads((REC / engine / f"{sha}.json").read_text(encoding="utf-8"))


def route_lines(engine: str, sha: str) -> list[str]:
    rec = recording(engine, sha)
    if engine == "vision":
        size = G.image_size(FIXTURES / rec["image"])
        return [r["text"] for r in E.vision_lines(rec["raw"], size, page_id="x/p001", der_sha256=sha)]
    if engine == "mlx":
        size = G.image_size(FIXTURES / rec["image"])
        return [r["text"] for r in E.mlx_lines(rec["raw"], size, page_id="x/p001", der_sha256=sha)]
    parsed = AC.parse_result(rec["stdout"]) if engine == "claude" else AX.parse_events(rec["stdout"])
    assert parsed.lines is not None, parsed.problem
    return [line["text"] for line in parsed.lines]


def page_of(sha: str) -> tuple[str, str]:
    image = Path(recording("mlx", sha)["image"])
    return image.parts[0], image.name.split(".")[0]


def read_cell(lines: list[str], item: str, row: int, col: str) -> str | None:
    rows = [line for line in lines if item in line]
    if row >= len(rows):
        return None
    line = rows[row]
    fields = extract(line)
    for f in fields:
        assert line[f.start:f.end] == f.value
    drugs = [f for f in fields if f.kind == "drug"]
    if col == "value":
        if drugs:
            return drugs[0].dose
        nums = [f for f in fields if f.kind == "number"]
        return nums[0].norm if nums else None
    idx = next((i for i, f in enumerate(fields) if f.kind == "number"), None)
    if idx is None:
        return None
    if fields[idx].unit is not None:
        return fields[idx].unit
    nxt = fields[idx + 1] if idx + 1 < len(fields) else None
    return nxt.norm if nxt is not None and nxt.kind == "unit" else None


def test_recordings_cover_seven_pages_four_routes():
    """Guard against a vacuous run: seven recorded pages, each present on all four routes."""
    assert len(SHAS) == 7
    for engine in ROUTES:
        assert {p.name.split(".")[0] for p in (REC / engine).glob("*.json")} >= set(SHAS)


def test_fields_match_truth_except_pinned_misreads():
    """07a-10 (L1-S2): every truth value/unit cell read through extract() equals truth on all four routes,
    except the pinned known misreads, which must read exactly as pinned."""
    checked, seen_known, wrong = 0, set(), []
    for sha in SHAS:
        fixture, key = page_of(sha)
        truth = json.loads((FIXTURES / fixture / "truth.json").read_text(encoding="utf-8"))
        cells = [f for f in truth["fields"] if f["key"] == key]
        assert cells, (fixture, key)
        for route in ROUTES:
            lines = route_lines(route, sha)
            for cell in cells:
                # user-approved 2026-10-23: truth "row" is the table row; read_cell wants the n-th occurrence of the item
                occurrence = len({c["row"] for c in cells if c["item"] == cell["item"] and c["row"] < cell["row"]})
                got = read_cell(lines, cell["item"], occurrence, cell["col"])
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


def test_page_dates_match_scenario():
    """07a-11 (L1-S2): the date on every recorded page, all four routes, normalises to the scenario date."""
    wrong = []
    for sha in SHAS:
        fixture, key = page_of(sha)
        scenario = json.loads((FIXTURES / fixture / "scenario.json").read_text(encoding="utf-8"))
        want = next(p["date"] for p in scenario["pages"] if p["key"] == key)
        for route in ROUTES:
            line = next(x for x in route_lines(route, sha) if "日期" in x)
            dates = [f for f in extract(line) if f.kind == "date"]
            got = dates[0].norm if dates else None
            if got != want:
                wrong.append((fixture, key, route, line, got, want))
    assert wrong == []


def test_mlx_f2_lowres_power_units_normalise():
    """07a-12 (L1-S1): fx-lowres MLX writes {10}^{\\times 9}; after F-2 flattening the unit field keeps the
    literal 10^×9/L as value and normalises to 10^9/L."""
    sha = next(s for s in SHAS if page_of(s)[0] == "fx-lowres")
    line = next(x for x in route_lines("mlx", sha) if "白细胞" in x)
    units = [f for f in extract(line) if f.kind == "unit"]
    assert [(f.value, f.norm) for f in units] == [("10^×9/L", "10^9/L")]
