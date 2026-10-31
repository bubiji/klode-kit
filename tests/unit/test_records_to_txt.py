"""WI-01a: ``records_to_txt.export`` - the deterministic text sources and their export map (plan WI-01a; matrix
01a-01/02/03/05/06/08/09/11; design 02 r2.2-C, r3.1 segment 3, R2-02, R3-07).

Contract under test (``export(project, out=None, *, kb_id=None, bindings=None) -> dict``):

- Only records whose ``effective`` revision is verified are exported (or exactly the records of ``bindings``, a
  build's snapshot ``{rec_id: {rev, audit_rev}}``); every value is read from the bound revision body
  ``records/_rev/<rec_id>.r<rev>.md`` - never ``cells.jsonl``, never a newer candidate.
- Two shelves: ``library/items/<item_slug>.txt`` holds one **observation line** per observation,
  ``<item> <value>[ <unit>] <date>[ <flag>] ⟨<rec_id>⟩`` (the item's display name from ``items.yaml``);
  ``library/records/<rec_id>.txt`` holds the record's observation lines plus its **source lines** (rows without
  an item value, ``<label> <values...> ⟨<rec_id>⟩``), in row order.
- ``export_map`` has one row per exported line (contract ``export_map``; ``line_kind`` = observation | source).
- A record without fields is ``excluded`` with reason ``empty_record``; a record that is not effective-verified
  with ``not_verified``.

Fixtures: the real stages on ``fx-labs-1x`` / ``fx-labs-3x`` (``export_fx.pipeline``), or records written by hand
in the writeback's format (``export_fx.put_record``). No mock anywhere.
"""

from __future__ import annotations

import json
import tempfile
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import export_fx as X
from klode_kit import items as items_mod
from klode_kit.build import records_to_txt as R
from klode_kit.contract import schema

pytestmark = pytest.mark.unit

LABS1 = X.FIXTURES / "fx-labs-1x"
LABS3 = X.FIXTURES / "fx-labs-3x"
A1, A2 = "aaaaaaaaaaaa/a1", "aaaaaaaaaaaa/a2"
REC_A, REC_B, REC_C, REC_D, REC_E = (f"r-{c * 12}" for c in "abcde")


def proj(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


def truth(fx: Path) -> dict:
    return json.loads((fx / "truth.json").read_text(encoding="utf-8"))


def obs_line(item: str, value: str, unit: str, date: str, flag: str, rec: str) -> str:
    """The observation line, spelled out independently of the implementation."""
    parts = [item, value] + ([unit] if unit else []) + [date] + ([flag] if flag else []) + [f"⟨{rec}⟩"]
    return " ".join(parts)


def slug(item: str) -> str:
    return items_mod.default().resolve(item)[0]


def lab(rec: str, n: int, *, item: str, value: str, date: str, flag: str = "", unit: str = "") -> dict:
    """fx-chain style fields of one lab row ``labs[n]`` plus the record date."""
    f = {f"{rec}/labs[{n}].item": item, f"{rec}/labs[{n}].value": value, f"{rec}/date": date}
    if unit:
        f[f"{rec}/labs[{n}].unit"] = unit
    if flag:
        f[f"{rec}/labs[{n}].flag"] = flag
    return f


# --------------------------------------------------------------------------- 01a-01


def test_line_format(tmp_path):
    """01a-01 (S1): fx-labs-1x through the real stages: each item file is exactly the one observation line
    ``<item> <value> <unit> <date>[ <flag>] ⟨rec_id⟩`` spelled from the fixture truth; the record file holds the
    date source line, then the observation lines in row order."""
    project = proj(tmp_path)
    X.pipeline(project, LABS1)
    res = R.export(project)
    (rec,) = truth(LABS1)["records"]
    rid, date = rec["rec_id"], rec["date"]
    expected = [obs_line(r["item"], r["value"], r["unit"], date, r["flag"], rid) for r in rec["rows"]]
    for r, line in zip(rec["rows"], expected):
        assert res["files"][f"library/items/{slug(r['item'])}.txt"] == line + "\n"
    assert res["files"][f"library/records/{rid}.txt"] == "\n".join([f"日期 {date} ⟨{rid}⟩", *expected]) + "\n"
    assert set(res["files"]) == {f"library/items/{slug(r['item'])}.txt" for r in rec["rows"]} | {
        f"library/records/{rid}.txt"}
    assert res["audit_bindings"] == {rid: {"rev": 1, "audit_rev": res["audit_bindings"][rid]["audit_rev"]}}
    assert res["excluded"] == []


# --------------------------------------------------------------------------- 01a-02


def test_only_verified_exported(tmp_path):
    """01a-02 (S3): verified (A), pending (B), disputed (C), and verified-with-a-newer-disputed-candidate (D)
    side by side - even when B/C/D's other bodies hold values - only A and D appear anywhere in the output; B
    and C are excluded as ``not_verified`` with their status."""
    project = proj(tmp_path)
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1,
                                      "fields": lab(REC_A, 0, item="血红蛋白", value="128", date="2026-09-18")}},
                 effective=1, date="2026-09-18")
    X.put_record(project, REC_B, {1: {"status": "pending_confirm", "audit_rev": A1,
                                      "fields": lab(REC_B, 0, item="血红蛋白", value="111", date="2026-09-18")}},
                 effective=None, date="2026-09-18")
    X.put_record(project, REC_C, {1: {"status": "disputed", "audit_rev": A1,
                                      "fields": lab(REC_C, 0, item="血红蛋白", value="122", date="2026-09-18")}},
                 effective=None, date="2026-09-18")
    X.put_record(project, REC_D, {1: {"status": "verified", "audit_rev": A1,
                                      "fields": lab(REC_D, 0, item="白细胞", value="6.2", date="2026-09-19")},
                                  2: {"status": "disputed", "audit_rev": A2,
                                      "fields": lab(REC_D, 0, item="白细胞", value="9.9", date="2026-09-19")}},
                 effective=1, date="2026-09-19")
    res = R.export(project)
    texts = X.lines(res)
    assert X.recs_in(texts) == {REC_A, REC_D}
    assert X.recs_in(json.dumps(row) for row in res["export_map"]) == {REC_A, REC_D}
    assert set(res["audit_bindings"]) == {REC_A, REC_D}
    assert res["excluded"] == [{"rec_id": REC_B, "reason": "not_verified", "status": "pending_confirm"},
                               {"rec_id": REC_C, "reason": "not_verified", "status": "disputed"}]
    assert not any(v in t for t in texts for v in (" 111 ", " 122 ", " 9.9 "))


# --------------------------------------------------------------------------- 01a-03


def test_values_from_binding_rev_item_fields(tmp_path):
    """01a-03 (S3, v2): effective r1 and candidate r2 differ in item, value, date and flag (r2's body holds its
    values; the record front names r2's date): the whole line is r1's projection, export_map rev is 1, and no
    column of r2 appears anywhere."""
    project = proj(tmp_path)
    r1 = lab(REC_A, 0, item="血红蛋白", value="128", date="2026-09-18", flag="H")
    r2 = lab(REC_A, 0, item="白细胞", value="6.2", date="2026-10-01", flag="L")
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1, "fields": r1, "date": "2026-09-18"},
                                  2: {"status": "disputed", "audit_rev": A2, "fields": r2, "date": "2026-10-01"}},
                 effective=1, date="2026-10-01")
    res = R.export(project)
    line = obs_line("血红蛋白", "128", "", "2026-09-18", "H", REC_A)
    assert res["files"] == {"library/items/hemoglobin.txt": line + "\n", f"library/records/{REC_A}.txt": line + "\n"}
    assert {row["rev"] for row in res["export_map"]} == {1}
    assert res["audit_bindings"] == {REC_A: {"rev": 1, "audit_rev": A1}}
    blob = json.dumps(res, ensure_ascii=False)
    assert not any(v in blob for v in ("白细胞", "6.2", "2026-10-01", " L "))


def test_values_from_binding_rev_pipeline_slots(tmp_path):
    """01a-03 (S3, v2) on the kb-audit slot shape (``r<row>-<kind>-<k>``, item from the item label of the
    revision's own audit revision): r1 and r2 differ in all four columns; the line is r1's projection."""
    project = proj(tmp_path)
    r1 = {f"{REC_A}/r003-date-0": "2026-09-18", f"{REC_A}/r005-number-0": "128", f"{REC_A}/r005-unit-0": "g/L",
          f"{REC_A}/r005-flag-0": "H"}
    r2 = {f"{REC_A}/r003-date-0": "2026-10-01", f"{REC_A}/r005-number-0": "6.2", f"{REC_A}/r005-unit-0": "g/L",
          f"{REC_A}/r005-flag-0": "L"}
    X.put_labels(project, A1, {f"{REC_A}/r003-date-0": "日期", **{f"{REC_A}/r005-{k}": "hemoglobin"
                                                                  for k in ("number-0", "unit-0", "flag-0")}})
    X.put_labels(project, A2, {f"{REC_A}/r003-date-0": "日期", **{f"{REC_A}/r005-{k}": "wbc"
                                                                  for k in ("number-0", "unit-0", "flag-0")}})
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1, "fields": r1, "date": "2026-09-18"},
                                  2: {"status": "disputed", "audit_rev": A2, "fields": r2, "date": "2026-10-01"}},
                 effective=1, date="2026-10-01")
    res = R.export(project)
    line = obs_line("血红蛋白", "128", "g/L", "2026-09-18", "H", REC_A)
    assert res["files"] == {"library/items/hemoglobin.txt": line + "\n",
                            f"library/records/{REC_A}.txt": f"日期 2026-09-18 ⟨{REC_A}⟩\n{line}\n"}
    blob = json.dumps(res, ensure_ascii=False)
    assert not any(v in blob for v in ("白细胞", "wbc", "6.2", "2026-10-01", "WRONG-IF-READ"))


def test_bindings_snapshot_wins_over_effective(tmp_path):
    """01a-03 / r3.1 version binding: a build snapshot binding r1 exports r1 although ``effective`` has since
    moved to a verified r2; records outside the snapshot are not exported."""
    project = proj(tmp_path)
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1,
                                      "fields": lab(REC_A, 0, item="血红蛋白", value="128", date="2026-09-18")},
                                  2: {"status": "verified", "audit_rev": A2,
                                      "fields": lab(REC_A, 0, item="血红蛋白", value="135", date="2026-09-18")}},
                 effective=2, date="2026-09-18")
    X.put_record(project, REC_B, {1: {"status": "verified", "audit_rev": A1,
                                      "fields": lab(REC_B, 0, item="白细胞", value="6.2", date="2026-09-18")}},
                 effective=1, date="2026-09-18")
    assert X.lines(R.export(project), "items") == [obs_line("血红蛋白", "135", "", "2026-09-18", "", REC_A),
                                                   obs_line("白细胞", "6.2", "", "2026-09-18", "", REC_B)]
    res = R.export(project, bindings={REC_A: {"rev": 1, "audit_rev": A1}})
    assert X.lines(res, "items") == [obs_line("血红蛋白", "128", "", "2026-09-18", "", REC_A)]
    assert res["audit_bindings"] == {REC_A: {"rev": 1, "audit_rev": A1}}
    assert {row["rev"] for row in res["export_map"]} == {1}


# --------------------------------------------------------------------------- 01a-05

ITEMS = ("血红蛋白", "白细胞", "示例指标", "某某指数")
row_st = st.fixed_dictionaries({"item": st.sampled_from(ITEMS), "value": st.sampled_from(("128", "6.2")),
                                "unit": st.sampled_from(("", "g/L")), "flag": st.sampled_from(("", "H"))})
rec_st = st.fixed_dictionaries({"date": st.sampled_from(("2026-09-18", "2026-09-19")),
                                "rows": st.lists(row_st, min_size=1, max_size=4)})


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(rec_st, min_size=1, max_size=4))
def test_lines_unique_by_construction(records):
    """01a-05 (S4): any observations - same day, same item, same value, also repeated inside one record - give
    pairwise different lines on each shelf; every observation is exported exactly once on the items shelf; every
    export_map row is the line at its ``source_file:line_no``."""
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        project.mkdir()
        n_obs = 0
        for i, rec in enumerate(records):
            rid = f"r-{i:012x}"
            fields = {f"{rid}/date": rec["date"]}
            for n, row in enumerate(rec["rows"]):
                fields.update(lab(rid, n, date=rec["date"], **row))
                n_obs += 1
            X.put_record(project, rid, {1: {"status": "verified", "audit_rev": A1, "fields": fields}},
                         effective=1, date=rec["date"])
        res = R.export(project)
        for shelf in ("items", "records"):
            got = X.lines(res, shelf)
            assert len(got) == len(set(got)), Counter(got).most_common(2)
        assert len(X.lines(res, "items")) == n_obs
        for row in res["export_map"]:
            assert res["files"][row["source_file"]].splitlines()[row["line_no"] - 1] == row["text"]


# --------------------------------------------------------------------------- 01a-06 / 01a-09


def test_fx_labs_3x_three_distinct(tmp_path):
    """01a-06 (S2): fx-labs-3x - hemoglobin 128, 128, 135 on three dates - gives three different lines in
    ``hemoglobin.txt``, in date order, each the truth's observation."""
    project = proj(tmp_path)
    X.pipeline(project, LABS3)
    res = R.export(project)
    got = res["files"]["library/items/hemoglobin.txt"].splitlines()
    recs = sorted(truth(LABS3)["records"], key=lambda r: r["date"])
    want = [obs_line(r["item"], r["value"], r["unit"], rec["date"], r["flag"], rec["rec_id"])
            for rec in recs for r in rec["rows"] if r["item"] == "血红蛋白"]
    assert got == want and len(set(got)) == 3
    assert [ln.split()[1] for ln in got] == ["128", "128", "135"]


def test_two_shelves(tmp_path):
    """01a-09 (S2): fx-labs-3x written to ``out``: ``library/records/<rec_id>.txt`` per record and
    ``library/items/<slug>.txt`` per item exist on disk with the returned text; a record file only carries its
    own rec_id, an item file only its item; each record's observation lines are exactly its lines on the items
    shelf; ``src/export_map.jsonl`` holds one legal row per line."""
    project = proj(tmp_path)
    X.pipeline(project, LABS3)
    out = tmp_path / "build"
    res = R.export(project, out, kb_id="fx-klode")
    recs = truth(LABS3)["records"]
    for rel, text in res["files"].items():
        assert (out / rel).read_text(encoding="utf-8") == text
    for rec in recs:
        rid = rec["rec_id"]
        own = (out / "library" / "records" / f"{rid}.txt").read_text(encoding="utf-8").splitlines()
        assert own and all(ln.endswith(f"⟨{rid}⟩") for ln in own)
        on_items = [ln for ln in X.lines(res, "items") if ln.endswith(f"⟨{rid}⟩")]
        obs = [r["text"] for r in res["export_map"] if r["rec_id"] == rid and r["line_kind"] == "observation"
               and r["source_file"].startswith("library/records/")]
        assert sorted(obs) == sorted(on_items) and len(on_items) == len(rec["rows"])
    for row in (r for rec in recs for r in rec["rows"]):
        path = out / "library" / "items" / f"{slug(row['item'])}.txt"
        assert all(ln.startswith(row["item"] + " ") for ln in path.read_text(encoding="utf-8").splitlines())
    rows = [json.loads(x) for x in (out / "src" / "export_map.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == res["export_map"]
    assert len(rows) == len(X.lines(res))
    assert all(schema.issues("export_map", r) == [] for r in rows)
    assert {r["kb_id"] for r in rows} == {"fx-klode"}
    index = json.loads((out / "src" / "items_index.json").read_text(encoding="utf-8"))
    assert [e["date"] for e in index["hemoglobin"]] == sorted(r["date"] for r in recs)


def test_source_and_observation_lines_classified(tmp_path):
    """Plan WI-01a 「源行与观测行分开归类」: a row without a value (the date row, a row whose value was dropped)
    is a source line, on the record shelf only; observation lines are on both shelves; ``line_kind`` says which."""
    project = proj(tmp_path)
    fields = {f"{REC_A}/r003-date-0": "2026-09-18", f"{REC_A}/r005-number-0": "128", f"{REC_A}/r005-unit-0": "g/L",
              f"{REC_A}/r006-unit-0": "10^9/L"}
    X.put_labels(project, A1, {f"{REC_A}/r003-date-0": "日期", f"{REC_A}/r005-number-0": "hemoglobin",
                               f"{REC_A}/r005-unit-0": "hemoglobin", f"{REC_A}/r006-unit-0": "wbc"})
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1, "fields": fields,
                                      "dropped": [f"{REC_A}/r006-number-0"]}}, effective=1, date="2026-09-18")
    res = R.export(project)
    obs = obs_line("血红蛋白", "128", "g/L", "2026-09-18", "", REC_A)
    assert res["files"] == {
        "library/items/hemoglobin.txt": obs + "\n",
        f"library/records/{REC_A}.txt": f"日期 2026-09-18 ⟨{REC_A}⟩\n{obs}\n白细胞 10^9/L ⟨{REC_A}⟩\n"}
    kinds = {(r["source_file"], r["text"]): r["line_kind"] for r in res["export_map"]}
    assert kinds == {("library/items/hemoglobin.txt", obs): "observation",
                     (f"library/records/{REC_A}.txt", obs): "observation",
                     (f"library/records/{REC_A}.txt", f"日期 2026-09-18 ⟨{REC_A}⟩"): "source",
                     (f"library/records/{REC_A}.txt", f"白细胞 10^9/L ⟨{REC_A}⟩"): "source"}


# --------------------------------------------------------------------------- 01a-08 and guards


def test_empty_record_rejected(tmp_path):
    """01a-08 (S3): a verified record without fields is excluded (``empty_record``) and yields no line; the other
    record is exported as usual."""
    project = proj(tmp_path)
    X.put_record(project, REC_E, {1: {"status": "verified", "audit_rev": A1, "fields": {}}}, effective=1,
                 date="2026-09-18")
    res = R.export(project)
    assert res["excluded"] == [{"rec_id": REC_E, "reason": "empty_record", "status": "verified"}]
    assert res["files"] == {} and res["export_map"] == [] and res["audit_bindings"] == {}
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1,
                                      "fields": lab(REC_A, 0, item="血红蛋白", value="128", date="2026-09-18")}},
                 effective=1, date="2026-09-18")
    res = R.export(project)
    assert X.recs_in(X.lines(res)) == {REC_A}
    assert [e["rec_id"] for e in res["excluded"]] == [REC_E]


def test_value_with_line_break_rejected(tmp_path):
    """Guard: a value holding a line break would split one observation over two lines - the record is excluded
    (``malformed_value``), never half exported."""
    project = proj(tmp_path)
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1,
                                      "fields": lab(REC_A, 0, item="血红蛋白", value="12\n8", date="2026-09-18")}},
                 effective=1, date="2026-09-18")
    res = R.export(project)
    assert res["excluded"] == [{"rec_id": REC_A, "reason": "malformed_value", "status": "verified"}]
    assert res["files"] == {}


def test_alias_and_new_items(tmp_path):
    """r2.2-C / r3.1 segment 3: an item field written as an alias (``HGB``) is shown under the item's display
    name with an ``alias`` transform; an unknown item keeps its own text, gets its slug file and is listed in
    ``new_items``; values and units carry no transform."""
    project = proj(tmp_path)
    fields = {**lab(REC_A, 0, item="HGB", value="128", unit="g/L", date="2026-09-18"),
              **lab(REC_A, 1, item="某某指数", value="3.3", date="2026-09-18")}
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1, "fields": fields}}, effective=1,
                 date="2026-09-18")
    res = R.export(project)
    assert res["files"]["library/items/hemoglobin.txt"] == obs_line("血红蛋白", "128", "g/L", "2026-09-18", "",
                                                                    REC_A) + "\n"
    assert res["files"][f"library/items/{items_mod.file_stem(items_mod.slugify('某某指数'))}.txt"] == obs_line("某某指数", "3.3", "", "2026-09-18", "",
                                                                  REC_A) + "\n"
    assert res["new_items"] == ["某某指数"]
    hgb = next(r for r in res["export_map"] if r["source_file"] == "library/items/hemoglobin.txt")
    assert hgb["transforms"] == [{"kind": "alias", "field_id": f"{REC_A}/labs[0].item", "from": "HGB",
                                  "to": "血红蛋白"}]
    assert hgb["value_audited"] == "128"
    assert all(t["field_id"].endswith(".item") for r in res["export_map"] for t in r["transforms"])


def test_existing_target_refused_nothing_written(tmp_path):
    """Guard: ``out`` already holding one of the target files - ExportError, that file unchanged and no other
    file written (the export never overwrites)."""
    project = proj(tmp_path)
    X.put_record(project, REC_A, {1: {"status": "verified", "audit_rev": A1,
                                      "fields": lab(REC_A, 0, item="血红蛋白", value="128", date="2026-09-18")}},
                 effective=1, date="2026-09-18")
    out = tmp_path / "build"
    target = out / "library" / "items" / "hemoglobin.txt"
    target.parent.mkdir(parents=True)
    target.write_text("keep\n", encoding="utf-8")
    with pytest.raises(R.ExportError):
        R.export(project, out)
    assert X.tree(out) == {"library/items/hemoglobin.txt": b"keep\n"}


def test_no_records_exports_nothing(tmp_path):
    """Boundary: a project without ``records/`` exports no file and excludes nothing."""
    res = R.export(proj(tmp_path))
    assert (res["files"], res["export_map"], res["excluded"], res["audit_bindings"]) == ({}, [], [], {})


# --------------------------------------------------------------------------- 01a-11


def test_export_deterministic_rerun(tmp_path):
    """01a-11 (S5): exporting fx-labs-3x twice (two output directories, and in memory) gives byte-identical
    trees and equal results."""
    project = proj(tmp_path)
    X.pipeline(project, LABS3)
    first = R.export(project, tmp_path / "o1", kb_id="k")
    second = R.export(project, tmp_path / "o2", kb_id="k")
    assert first == second == R.export(project, kb_id="k")
    assert X.tree(tmp_path / "o1") == X.tree(tmp_path / "o2") != {}
