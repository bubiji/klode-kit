"""WI-00b3: export transforms and validator guards (matrix 00b3-10 + added guard cases).

Law: ``unapply(t, apply(t, x)) == x`` for every transform sequence that applies (S5); ``alias``
changes only item fields, ``dateshift`` only date fields; no transform may touch a numeric field
(value / unit) - ``check_transforms`` flags exactly those (S4).

Public surface (``klode_kit.contract.provenance``):
- ``apply(transforms, values) -> dict`` / ``unapply(transforms, values) -> dict``: ``values`` maps
  field_id -> text; transforms run in order (``unapply`` in reverse), each requires the current value
  to equal its ``from`` (``to`` for unapply) else ``TransformError``; a transform without
  ``field_id`` is line-level (layout) and leaves field values alone;
- ``check_transforms(transforms, *, deident_mode) -> list[Violation]``;
- ``validate(root, layout=None)``; ``Layout.for_products(root)`` and explicit ``Layout(...)`` for
  pipeline products whose artifacts are not under one directory (WI-31 reuse);
- ``CODES``: every violation code the validator can emit.

Guards: a directory without the chain artifacts is never "0 violations"; unreadable JSON is a
``malformed_artifact`` violation (never an exception).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.contract import provenance as P

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
CLEAN = REPO / "tests" / "fixtures" / "fx-chain" / "clean"
REC = "r-0123456789ab"

KINDS = ("item", "value", "unit", "date")
FIELDS = [f"{REC}/date"] + [f"{REC}/labs[{i}].{k}" for i in range(3) for k in ("item", "value", "unit")]
text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=8)


def _kind(fid: str) -> str:
    return fid.split("/", 1)[1].rsplit(".", 1)[-1]


@st.composite
def legal_chain(draw):
    """Values for every field plus a legal transform sequence (alias on items, dateshift on the date)."""
    values = {f: draw(text) for f in FIELDS}
    current = dict(values)
    transforms = []
    for _ in range(draw(st.integers(min_value=0, max_value=6))):
        choice = draw(st.sampled_from(["alias", "dateshift", "layout"]))
        if choice == "layout":
            transforms.append({"kind": "layout", "from": draw(text), "to": draw(text)})
            continue
        pool = [f for f in FIELDS if _kind(f) == ("item" if choice == "alias" else "date")]
        fid = draw(st.sampled_from(pool))
        new = draw(text)
        transforms.append({"kind": choice, "field_id": fid, "from": current[fid], "to": new})
        current[fid] = new
    return values, transforms


@settings(max_examples=300)
@given(legal_chain())
def test_transforms_apply_unapply_identity(chain):
    """00b3-10 (S5): round trip; alias touches only items, dateshift only the date; numeric fields untouched."""
    values, transforms = chain
    out = P.apply(transforms, values)
    assert P.unapply(transforms, out) == values
    final = {t["field_id"]: t["to"] for t in transforms if "field_id" in t}
    assert {f: out[f] for f in final} == final  # apply really moves each field to its last ``to``
    changed = {f for f in values if out[f] != values[f]}
    alias_targets = {t["field_id"] for t in transforms if t["kind"] == "alias"}
    shift_targets = {t["field_id"] for t in transforms if t["kind"] == "dateshift"}
    assert changed <= alias_targets | shift_targets
    assert all(_kind(f) == "item" for f in alias_targets)
    assert all(out[f] == values[f] for f in values if _kind(f) in ("value", "unit"))
    assert P.check_transforms(transforms, deident_mode="dateshift") == []
    assert values == {f: values[f] for f in FIELDS}  # apply/unapply never mutate their input


@settings(max_examples=300)
@given(st.lists(st.tuples(st.sampled_from(["alias", "dateshift", "layout"]), st.sampled_from(FIELDS), text, text),
                max_size=8))
def test_check_transforms_flags_exactly_numeric_targets(raw):
    """00b3-10 (S4): the set of transforms on value / unit fields that check_transforms reports is exactly
    the generated set; alias off items and dateshift off the date are reported with their own codes."""
    transforms = [{"kind": k, "field_id": f, "from": a, "to": b} for k, f, a, b in raw]
    found = P.check_transforms(transforms, deident_mode="dateshift")
    numeric = sorted(i for i, t in enumerate(transforms) if _kind(t["field_id"]) in ("value", "unit"))
    flagged = sorted(int(v.location.rsplit("#", 1)[1]) for v in found if v.code == "transform_on_numeric")
    assert flagged == numeric
    alias_off = sorted(i for i, t in enumerate(transforms)
                       if t["kind"] == "alias" and _kind(t["field_id"]) in ("date",))
    assert sorted(int(v.location.rsplit("#", 1)[1]) for v in found if v.code == "alias_not_item") == alias_off
    shift_off = sorted(i for i, t in enumerate(transforms)
                       if t["kind"] == "dateshift" and _kind(t["field_id"]) == "item")
    assert sorted(int(v.location.rsplit("#", 1)[1]) for v in found if v.code == "dateshift_not_date") == shift_off
    assert {v.code for v in found} <= P.CODES


def test_dateshift_requires_dateshift_mode():
    t = [{"kind": "dateshift", "field_id": f"{REC}/date", "from": "2026-09-18", "to": "2026-10-02"}]
    assert P.check_transforms(t, deident_mode="dateshift") == []
    assert [v.code for v in P.check_transforms(t, deident_mode="pseudonym")] == ["dateshift_without_mode"]


def test_apply_rejects_from_mismatch():
    t = [{"kind": "alias", "field_id": f"{REC}/labs[0].item", "from": "HGB", "to": "血红蛋白"}]
    with pytest.raises(P.TransformError):
        P.apply(t, {f"{REC}/labs[0].item": "Hb"})
    with pytest.raises(P.TransformError):
        P.unapply(t, {f"{REC}/labs[0].item": "HGB"})
    with pytest.raises(P.TransformError):
        P.apply(t, {})


# --------------------------------------------------------------------------- validator guards


def test_empty_directory_is_not_clean(tmp_path):
    found = P.validate(tmp_path)
    assert found, "an empty directory must never validate as 0 violations"
    assert {v.code for v in found} == {"artifact_missing"}


def test_missing_directory_is_not_clean(tmp_path):
    assert {v.code for v in P.validate(tmp_path / "nope")} == {"artifact_missing"}


def test_malformed_jsonl_is_reported_not_raised(tmp_path):
    root = tmp_path / "c"
    shutil.copytree(CLEAN, root)
    cells = next(root.glob("audit/*/a1/cells.jsonl"))
    cells.write_text(cells.read_text(encoding="utf-8") + "{not json\n", encoding="utf-8")
    found = P.validate(root)
    bad = [v for v in found if v.code == "malformed_artifact"]
    assert len(bad) == 1
    assert bad[0].location.endswith("cells.jsonl:14")


def test_explicit_layout_for_pipeline_products(tmp_path):
    """WI-31 reuse: artifacts spread over work/, audit/, records/ and kb/<id>/builds/<build_id>/."""
    proj = tmp_path / "proj"
    batch = json.loads((CLEAN / "chain.json").read_text(encoding="utf-8"))["batch_id"]
    work = proj / "work" / batch
    work.mkdir(parents=True)
    for name in ("manifest.jsonl", "derived.jsonl"):
        shutil.copy(CLEAN / name, work / name)
    shutil.copytree(CLEAN / "ocr", work / "ocr")
    shutil.copytree(CLEAN / "audit", proj / "audit")
    shutil.copytree(CLEAN / "records", proj / "records")
    build = proj / "kb" / "x" / "builds" / "b-1"
    shutil.copytree(CLEAN / "build", build)
    for d in ("images", "derived_img"):
        shutil.copytree(CLEAN / d, proj / d)
    layout = P.Layout(
        root=proj,
        manifest=[work / "manifest.jsonl"],
        derived=[work / "derived.jsonl"],
        page_map=[],
        ocr_dirs=[work / "ocr"],
        audit_dir=proj / "audit",
        records_dir=proj / "records",
        build_dir=build,
        artifact_base=proj,
    )
    assert P.validate(proj, layout) == []
    # the same tree without an explicit layout is missing its artifacts, not clean
    assert "artifact_missing" in {v.code for v in P.validate(proj)}


def test_artifact_bytes_must_match_registered_sha(tmp_path):
    root = tmp_path / "c"
    shutil.copytree(CLEAN, root)
    (root / "derived_img" / "p001.m.png").write_bytes(b"not the derived image")
    found = P.validate(root)
    assert [v.code for v in found] == ["artifact_sha_mismatch"]
    assert found[0].location == "derived_img/p001.m.png"


def test_validate_is_deterministic():
    broken = REPO / "tests" / "fixtures" / "fx-chain" / "broken-4"
    assert P.validate(broken) == P.validate(broken)
    assert P.validate(CLEAN) == []
