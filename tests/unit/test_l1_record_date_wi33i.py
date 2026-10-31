"""WI-33i (real-data pilot P14): cards must show the record's date when the observation lines carry no positioned
``date`` field (real exports: the date field lives on a separate header row, the line text still ends with the
record date). The export map row schema is closed (no date key), so the fallback is the ISO date ``records_to_txt``
itself appends before the flag and `` ⟨rec_id⟩`` (shifted under ``dateshift``).

Matrix: law = every item card whose lines all have a record date shows a non-empty date range (property, S4);
examples for Thin/Full/record cards/aliases/dateshift (S1/S2). Lane unit; real export and real cards, no mock.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import export_fx as X
from klode_kit.build import records_to_l1 as L
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.unit

A1 = "aaaaaaaaaaaa/a1"
REC_A, REC_B = "r-aaaaaaaaaaaa", "r-bbbbbbbbbbbb"
ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def undated_project(root: Path, dates: dict[str, str]) -> Path:
    """Verified records whose lab rows have item/value/unit but NO date field (the date is the body's only)."""
    project = root / "proj"
    project.mkdir(parents=True)
    for n, (rec, date) in enumerate(dates.items()):
        fields = {f"{rec}/labs[0].item": "血红蛋白", f"{rec}/labs[0].value": f"{5 + n}.1",
                  f"{rec}/labs[0].unit": "g/L"}
        X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}},
                     effective=1, date=date)
    return project


def thin(card: str) -> str:
    return card.split("## Thin\n", 1)[1].split("\n\n## Full", 1)[0]


def test_export_map_rows_have_no_date_role_yet_cards_show_dates(tmp_path):
    project = undated_project(tmp_path, {REC_A: "2026-03-01", REC_B: "2026-04-02"})
    res = R.export(project, tmp_path / "build", kb_id="k")
    assert all(not any(f.endswith(".date") for f in r["positions"]) for r in res["export_map"])
    cards = L.generate(tmp_path / "build")["cards"]
    item = cards["hemoglobin"]
    assert "共 2 次观测（2026-03-01 至 2026-04-02）" in thin(item)
    assert "最新一次 2026-04-02：" in thin(item)
    assert "上一次（2026-03-01）为" in thin(item)
    assert "- 2026-03-01：5.1 g/L" in item and "- 2026-04-02：6.1 g/L" in item
    assert "# 单据 r-aaaaaaaaaaaa（2026-03-01）" in cards[REC_A]
    assert "日期 2026-03-01，" in thin(cards[REC_A])


def test_date_aliases_built_from_the_fallback_date(tmp_path):
    project = undated_project(tmp_path, {REC_A: "2026-03-01"})
    R.export(project, tmp_path / "build", kb_id="k")
    al = L.generate(tmp_path / "build")["aliases"][REC_A]
    for form in ("2026-03-01", "2026/03/01", "2026.03.01", "20260301", "2026年3月1日"):
        assert form in al


def test_dateshift_cards_use_shifted_dates_never_the_original(tmp_path):
    project = undated_project(tmp_path, {REC_A: "2026-03-01", REC_B: "2026-04-02"})
    res = R.export(project, tmp_path / "build", kb_id="k", deident="dateshift")
    shown = sorted(set(ISO.findall("".join(res["files"].values()))))
    assert len(shown) == 2 and "2026-03-01" not in shown and "2026-04-02" not in shown
    out = L.generate(tmp_path / "build")
    blob = json.dumps(out, ensure_ascii=False)
    assert "2026-03-01" not in blob and "2026-04-02" not in blob
    assert f"共 2 次观测（{shown[0]} 至 {shown[1]}）" in thin(out["cards"]["hemoglobin"])
    assert shown[0].replace("-", "") in out["aliases"][REC_A] + out["aliases"][REC_B]


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(days=st.lists(st.integers(0, 3000), min_size=1, max_size=4, unique=True))
def test_property_item_card_date_range_never_empty(days):
    """(S4) every line of the item has a record date -> the Thin date range / latest date is non-empty and is
    exactly the first and last date of the item."""
    dates = {f"r-{i:012d}": (dt.date(2015, 1, 1) + dt.timedelta(days=d)).isoformat() for i, d in enumerate(days)}
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        project = undated_project(root, dates)
        R.export(project, root / "build", kb_id="k")
        item = L.generate(root / "build")["cards"]["hemoglobin"]
    ordered = sorted(dates.values())
    t = thin(item)
    assert f"最新一次 {ordered[-1]}：" in t
    if len(ordered) > 1:
        assert f"（{ordered[0]} 至 {ordered[-1]}）" in t
    assert "（ 至 ）" not in t and "最新一次 ：" not in t
