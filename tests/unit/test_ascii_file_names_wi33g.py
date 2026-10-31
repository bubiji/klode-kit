"""WI-33g (real-data pilot P12, decision D-38): every file name written under ``library/<shelf>/`` and every card
name is ASCII-safe (klode 0.5.0 ``src_path_re`` = ``[A-Za-z0-9._-]+\\.txt``), while the Chinese item name stays in the
card title, aliases and Thin text.

Law (S4/S5): ``items.file_stem`` is a deterministic, idempotent map into klode's name alphabet; distinct slugs
that land on one stem are an ``ExportError`` (never a silent merge). Synthetic data only.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from _support import export_fx as X
from klode_kit import items as items_mod
from klode_kit.build import gen_desk, records_to_l1 as L1
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.unit

SRC_PATH_NAME = re.compile(r"^[A-Za-z0-9._-]+$")  # klode common.src_path_re, name part
A1 = "aaaaaaaaaaaa/a1"
CJK = ("肌酸激酶", "总胆红素")


def _project(tmp: Path, items: tuple[str, ...], rec_prefix: str = "a") -> Path:
    project = tmp / "proj"
    project.mkdir(exist_ok=True)
    rec = f"r-{rec_prefix * 12}"
    fields = {f"{rec}/date": "2026-03-01"}
    for n, name in enumerate(items):
        fields[f"{rec}/labs[{n}].item"] = name
        fields[f"{rec}/labs[{n}].value"] = str(10 + n)
        fields[f"{rec}/labs[{n}].unit"] = "U/L"
    X.put_record(project, rec, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                 date="2026-03-01")
    return project


def test_cjk_item_files_are_ascii_safe_and_deterministic(tmp_path):
    project = _project(tmp_path, CJK)
    first = R.export(project, tmp_path / "b1", kb_id="k")
    second = R.export(project, tmp_path / "b2", kb_id="k")
    items = sorted(p for p in first["files"] if p.startswith("library/items/"))
    assert len(items) == 2
    for rel in first["files"]:
        assert SRC_PATH_NAME.match(rel.rsplit("/", 1)[1]), rel
    assert sorted(first["files"]) == sorted(second["files"])
    assert set(first["items_index"]) == {Path(p).stem for p in items}
    for rel in items:
        assert Path(rel).stem == items_mod.file_stem(next(s for s in CJK if items_mod.file_stem(items_mod.slugify(s)) == Path(rel).stem))
        assert re.fullmatch(r"u-[0-9a-f]{10}", Path(rel).stem)
    assert {r["source_file"] for r in first["export_map"]} == set(first["files"])


def test_ascii_slug_keeps_its_name(tmp_path):
    project = _project(tmp_path, ("hemoglobin",))
    res = R.export(project, tmp_path / "b", kb_id="k")
    assert "library/items/hemoglobin.txt" in res["files"]


def test_cards_are_named_after_the_stem_and_keep_the_chinese_name(tmp_path):
    project = _project(tmp_path, CJK)
    build = tmp_path / "b"
    R.export(project, build, kb_id="k")
    out = L1.generate(build)
    stem = items_mod.file_stem(items_mod.slugify("肌酸激酶"))
    assert set(out["cards"]) >= {stem}
    for cid in out["cards"]:
        assert SRC_PATH_NAME.match(cid), cid
    card = out["cards"][stem]
    assert "肌酸激酶" in out["aliases"][stem]
    assert "# 肌酸激酶（" + stem + "）" in card
    rec_card = next(c for cid, c in out["cards"].items() if cid.startswith("r-"))
    assert "肌酸激酶" in out["aliases"][next(cid for cid in out["cards"] if cid.startswith("r-"))]
    assert "肌酸激酶" in rec_card


def test_desk_records_mode_lists_stems_of_files_that_exist(tmp_path):
    project = _project(tmp_path, CJK)
    build = tmp_path / "b"
    R.export(project, build, kb_id="k")
    L1.write(build)
    plan, _ = gen_desk._records_plan(build, None)
    stems = {p.stem for p in (build / "library" / "items").glob("*.txt")}
    index = json.loads((build / "src" / "items_index.json").read_text(encoding="utf-8"))
    assert set(index) == stems
    cards = {c for d in plan for c in d["cards"]}
    assert stems <= cards
    assert all(SRC_PATH_NAME.match(c) for c in cards)
    assert cards == stems | {p.stem for p in (build / "library" / "records").glob("*.txt")}


def test_stem_collision_is_an_error(tmp_path, monkeypatch):
    project = _project(tmp_path, CJK)
    monkeypatch.setattr(items_mod, "file_stem", lambda s: "same" if s != "r-" + "a" * 12 else s)
    with pytest.raises(R.ExportError, match="collision"):
        R.export(project, tmp_path / "b", kb_id="k")


def test_literal_u_hash_slug_cannot_shadow_a_hashed_one(tmp_path):
    hashed = items_mod.file_stem(items_mod.slugify("肌酸激酶"))
    project = _project(tmp_path, ("肌酸激酶", hashed))
    with pytest.raises(R.ExportError, match="collision"):
        R.export(project, tmp_path / "b", kb_id="k")


@settings(max_examples=60, deadline=None)
@given(st.text(min_size=1, max_size=12).filter(lambda t: t.strip() and any(c.isalnum() for c in t)))
def test_file_stem_is_total_ascii_safe_stable_idempotent(label):
    try:
        slug = items_mod.slugify(label)
    except ValueError:
        return
    stem = items_mod.file_stem(slug)
    assert SRC_PATH_NAME.match(stem) and not stem.startswith(".")
    assert stem == items_mod.file_stem(slug)
    assert items_mod.file_stem(stem) == stem
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{2,}", slug):  # spec change WI-33j (klode card-name rule)
        assert stem == slug
