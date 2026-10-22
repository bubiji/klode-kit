"""WI-00b3: provenance-chain validator + roundtrip (matrix 00b3-01..09, 00b3-11..19; K4).

Law (matrix WI-00b3):
- the validator is complete over every r3.1 invariant: a single corruption of any invariant on
  any route (cloud routes included) is detected (S4, 00b3-12);
- after the audit revision advances, the old build is still self-consistent (S6, 00b3-11);
- ``unapply(apply(t, x)) == x`` (S5, unit lane: tests/unit/test_provenance_transforms.py).

Public surface (``klode_kit.contract.provenance``):
- ``validate(root, layout=None) -> list[Violation]`` - reads a product directory, never writes;
- ``Violation(code, rec_id, field_id, engine, location, detail)``; ``engine`` is the route.

Each broken fixture variant must yield exactly its declared violation and nothing else
(``variant.json`` ``expect_violations``); each legal variant yields ``[]``.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from klode_kit.contract import provenance as P

pytestmark = pytest.mark.contract_replay

REPO = Path(__file__).resolve().parents[2]
CHAIN = REPO / "tests" / "fixtures" / "fx-chain"
ROUTES = ("vision", "mlx", "claude", "codex")


def _chain(variant: str) -> dict:
    return json.loads((CHAIN / variant / "chain.json").read_text(encoding="utf-8"))


REC = _chain("clean")["rec_ids"][0]
BATCH = _chain("clean")["batch_id"]


def _codes(root: Path) -> list[str]:
    return [v.code for v in P.validate(root)]


def _only(variant: str, code: str) -> P.Violation:
    meta = json.loads((CHAIN / variant / "variant.json").read_text(encoding="utf-8"))
    assert [e["code"] for e in meta["expect_violations"]] == [code], "fixture declares a different violation"
    found = P.validate(CHAIN / variant)
    assert [v.code for v in found] == [code], found
    return found[0]


# --------------------------------------------------------------------------- file helpers (tmp copies only)


def _copy(variant: str, dest: Path) -> Path:
    target = dest / variant
    shutil.copytree(CHAIN / variant, target)
    return target


def _jl_read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _jl_write(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def _fm_read(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return json.loads(text[4:text.index("\n---\n", 4)])


def _fm_write(path: Path, meta: dict) -> None:
    path.write_text("---\n" + json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n---\n", encoding="utf-8")


def _audit(root: Path) -> Path:
    batch = json.loads((root / "chain.json").read_text(encoding="utf-8"))["batch_id"]
    return root / "audit" / batch / "a1"


def _tree_sha(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        h.update(rel.encode() + b"\0")
        if p.is_file():
            h.update(p.read_bytes())
        h.update(oct(p.stat().st_mode).encode())
    return h.hexdigest()


# --------------------------------------------------------------------------- 00b3-01 .. 00b3-09


def test_clean_chain_zero_violations():
    """00b3-01."""
    assert P.validate(CHAIN / "clean") == []


def test_variant_span_text_ne_candidate():
    """00b3-02: broken-1, vision prov span shifted by one."""
    v = _only("broken-1", "span_mismatch")
    assert (v.rec_id, v.field_id, v.engine) == (REC, f"{REC}/labs[0].value", "vision")


def test_variant_der_parent_mismatch():
    """00b3-03: broken-2, derived parent != manifest img_sha256."""
    v = _only("broken-2", "der_parent_mismatch")
    assert v.location.startswith("derived.jsonl")


def test_variant_manual_missing_region():
    """00b3-04: broken-3, manual decision without evidence.region."""
    v = _only("broken-3", "manual_evidence_missing")
    assert (v.rec_id, v.field_id) == (REC, f"{REC}/labs[3].value")


def test_variant_two_effective_decisions():
    """00b3-05: broken-4, two effective decisions for one field."""
    v = _only("broken-4", "multiple_effective_decisions")
    assert (v.rec_id, v.field_id) == (REC, f"{REC}/labs[2].value")


def test_variant_alias_on_numeric():
    """00b3-06: broken-5, alias transform on a numeric field (text unchanged)."""
    v = _only("broken-5", "transform_on_numeric")
    assert (v.rec_id, v.field_id) == (REC, f"{REC}/labs[0].value")


def test_variant_export_rev_ne_binding():
    """00b3-07: broken-6, export_map.rev != audit_bindings[rec_id].rev."""
    v = _only("broken-6", "rev_binding_mismatch")
    assert v.rec_id == REC
    assert v.location.startswith("build/src/export_map.jsonl")


def test_manual_fix_passes_without_any_route_correct():
    """00b3-08: four routes read 10.5, human manual 1.05 -> 0 violations; segment 1 still holds on 10.5."""
    root = CHAIN / "legal-manual"
    cell = next(c for c in _jl_read(_audit(root) / "cells.jsonl") if c["field_id"] == f"{REC}/labs[3].value")
    assert {e: c["value"] for e, c in cell["candidates"].items()} == {e: "10.5" for e in ROUTES}
    assert P.validate(root) == []
    # Segment 1 is checked against what the routes read, not against the manual value:
    with tempfile.TemporaryDirectory() as tmp:
        bad = _copy("legal-manual", Path(tmp))
        cells = _jl_read(_audit(bad) / "cells.jsonl")
        for c in cells:
            if c["field_id"] == f"{REC}/labs[3].value":
                c["candidates"]["vision"]["value"] = "1.05"
        _jl_write(_audit(bad) / "cells.jsonl", cells)
        found = P.validate(bad)
    assert [(v.code, v.field_id, v.engine) for v in found if v.code == "span_mismatch"] == [
        ("span_mismatch", f"{REC}/labs[3].value", "vision")
    ]


@pytest.mark.parametrize("variant", ["legal-dateshift", "legal-alias", "legal-oldbuild", "legal-pdftext_dpi"])
def test_legal_variants_pass(variant):
    """00b3-09."""
    assert P.validate(CHAIN / variant) == []


# --------------------------------------------------------------------------- 00b3-11, 00b3-13


def _advance_rev(root: Path) -> None:
    """Append rev 2: the human re-picks labs[1].value from mlx (6.8); the build is not touched."""
    target = f"{REC}/labs[1].value"
    audit = _audit(root)
    cells = {c["field_id"]: c for c in _jl_read(audit / "cells.jsonl")}
    fixes = _jl_read(audit / "fixes.jsonl")
    cands = cells[target]["candidates"]
    fixes.append({"at": "2026-10-23T00:00:00Z", "audit_rev": f"{BATCH}/a1", "by": "human", "choice": "route",
                  "commit_id": f"{BATCH}/a1/c0002", "decision_id": f"{BATCH}/a1/d{len(fixes) + 1:04d}",
                  "evidence": {"seen": {e: v["value"] for e, v in cands.items()}}, "field_id": target,
                  "rev_base": 1, "rev_new": 2, "route": "mlx", "value": cands["mlx"]["value"]})
    _jl_write(audit / "fixes.jsonl", fixes)
    r1 = _fm_read(root / "records" / "_rev" / f"{REC}.r1.md")
    r2 = copy.deepcopy(r1)
    r2.update(rev=2, commit_id=f"{BATCH}/a1/c0002")
    r2["fields"][target] = cands["mlx"]["value"]
    _fm_write(root / "records" / "_rev" / f"{REC}.r2.md", r2)
    by_id_path = root / "records" / "_by_id" / f"{REC}.md"
    by_id = _fm_read(by_id_path)
    by_id["candidates"][0]["superseded_by"] = 2
    by_id["candidates"].append({"audit_rev": f"{BATCH}/a1", "created": "2026-10-23T00:00:00Z", "rev": 2,
                                "status": "verified", "uncertain": []})
    by_id["effective"] = {"audit_rev": f"{BATCH}/a1", "rev": 2, "status": "verified", "verified_by": "human"}
    _fm_write(by_id_path, by_id)


def test_advance_rev_keeps_old_build_valid(tmp_path):
    """00b3-11 (S6): rev advancing leaves the old build consistent; binding the old map to the new rev is caught."""
    root = _copy("clean", tmp_path)
    build_before = _tree_sha(root / "build")
    assert P.validate(root) == []
    _advance_rev(root)
    assert _tree_sha(root / "build") == build_before
    assert P.validate(root) == []
    receipt_path = root / "build" / "build_receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["audit_bindings"][REC]["rev"] = 2
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    found = P.validate(root)
    assert "rev_binding_mismatch" in {v.code for v in found}
    assert all(v.rec_id == REC for v in found if v.code == "rev_binding_mismatch")


def test_validator_is_readonly(tmp_path):
    """00b3-13."""
    root = _copy("clean", tmp_path)
    before = _tree_sha(root)
    P.validate(root)
    assert _tree_sha(root) == before


# --------------------------------------------------------------------------- 00b3-14 .. 00b3-19


def test_variant_verified_field_no_decision():
    """00b3-14: broken-7, a verified field without any decision (exactly one, not <= 1)."""
    v = _only("broken-7", "missing_effective_decision")
    assert (v.rec_id, v.field_id) == (REC, f"{REC}/date")


def test_variant_route_value_ne_candidate():
    """00b3-15: broken-8, route decision wrote the recommended value, not candidates[route]."""
    v = _only("broken-8", "route_value_mismatch")
    assert (v.rec_id, v.field_id, v.engine) == (REC, f"{REC}/labs[1].value", "vision")


def test_variant_record_value_ne_decision():
    """00b3-16: broken-9, record rev field value != decision value."""
    v = _only("broken-9", "record_value_mismatch")
    assert (v.rec_id, v.field_id) == (REC, f"{REC}/labs[0].value")


def test_variant_export_text_ne_applied():
    """00b3-17: broken-10, exported text at the declared position != apply(transforms, revision value)."""
    v = _only("broken-10", "export_text_mismatch")
    assert (v.rec_id, v.field_id) == (REC, f"{REC}/labs[0].value")


def test_variant_pdftext_bbox_wrong_dpi():
    """00b3-18: broken-11, pdftext bbox stored at 72 dpi instead of page_map.dpi."""
    v = _only("broken-11", "pdftext_dpi_mismatch")
    assert v.engine == "pdftext"


def test_variant_cloud_route_span_mismatch():
    """00b3-19: broken-12, codex route (bbox_px null) span text != candidate."""
    v = _only("broken-12", "span_mismatch")
    assert (v.rec_id, v.field_id, v.engine) == (REC, f"{REC}/labs[3].value", "codex")


# --------------------------------------------------------------------------- 00b3-12 (S4)

CLASSES = {
    "span": ("clean", {"span_mismatch"}),
    "der_parent": ("clean", {"der_parent_mismatch"}),
    "manual_evidence": ("legal-manual", {"manual_evidence_missing"}),
    "one_effective": ("clean", {"multiple_effective_decisions", "missing_effective_decision"}),
    "route_value": ("clean", {"route_value_mismatch"}),
    "record_value": ("clean", {"record_value_mismatch"}),
    "export_text": ("clean", {"export_text_mismatch"}),
    "pdftext_dpi": ("legal-pdftext_dpi", {"pdftext_dpi_mismatch"}),
    "rev_binding": ("clean", {"rev_binding_mismatch"}),
    "alias_item": ("clean", {"transform_on_numeric", "alias_not_item"}),
}


def _fields(root: Path) -> list[str]:
    return [c["field_id"] for c in _jl_read(_audit(root) / "cells.jsonl")]


def _corrupt(cls: str, root: Path, route: str, pick: int, knob: int) -> None:
    audit = _audit(root)
    fields = _fields(root)
    fid = fields[pick % len(fields)]
    if cls == "span":
        cells = _jl_read(audit / "cells.jsonl")
        cell = next(c for c in cells if c["field_id"] == fid)
        s, e = cell["prov"][route]["span"]
        if knob == 0:
            cell["prov"][route]["span"] = [s, e + 1]
        elif knob == 1:
            cell["candidates"][route]["value"] += "x"
        elif knob == 2:
            cell["prov"][route]["span"] = [s, e - 1]
        else:
            ocr = next((root / "ocr" / route).glob("*/*.jsonl"))
            lines = _jl_read(ocr)
            line = lines[cell["prov"][route]["line_idx"]]
            line["text"] = "#" * len(line["text"])
            _jl_write(ocr, lines)
        _jl_write(audit / "cells.jsonl", cells)
    elif cls == "der_parent":
        if knob % 2 == 0:
            derived = _jl_read(root / "derived.jsonl")
            derived[0]["parent"] = hashlib.sha256(b"another original").hexdigest()
            _jl_write(root / "derived.jsonl", derived)
        else:
            cells = _jl_read(audit / "cells.jsonl")
            cell = next(c for c in cells if c["field_id"] == fid)
            cell["prov"][route]["der_sha256"] = hashlib.sha256(b"unregistered derived").hexdigest()
            _jl_write(audit / "cells.jsonl", cells)
    elif cls == "manual_evidence":
        fixes = _jl_read(audit / "fixes.jsonl")
        d = next(d for d in fixes if d["choice"] == "manual")
        if knob == 0:
            del d["evidence"]["region"]
        elif knob == 1:
            del d["evidence"]["seen"]
        elif knob == 2:
            d["evidence"]["region"]["der_sha256"] = hashlib.sha256(b"unregistered derived").hexdigest()
        else:
            del d["evidence"]["region"]["bbox_px"]
        _jl_write(audit / "fixes.jsonl", fixes)
    elif cls == "one_effective":
        fixes = _jl_read(audit / "fixes.jsonl")
        if knob % 2 == 0:
            dup = copy.deepcopy(next(d for d in fixes if d["field_id"] == fid))
            dup["decision_id"] = f"{BATCH}/a1/d{len(fixes) + 1:04d}"
            fixes.append(dup)
        else:
            fixes = [d for d in fixes if d["field_id"] != fid]
        _jl_write(audit / "fixes.jsonl", fixes)
    elif cls == "route_value":
        cells = {c["field_id"]: c for c in _jl_read(audit / "cells.jsonl")}
        fixes = _jl_read(audit / "fixes.jsonl")
        d = next(d for d in fixes if d["field_id"] == fid)
        d["route"] = route
        d["value"] = cells[fid]["candidates"][route]["value"] + "9"
        _jl_write(audit / "fixes.jsonl", fixes)
    elif cls == "record_value":
        path = root / "records" / "_rev" / f"{REC}.r1.md"
        body = _fm_read(path)
        body["fields"][fid] += "9"
        _fm_write(path, body)
    elif cls == "export_text":
        path = root / "build" / "src" / "export_map.jsonl"
        rows = _jl_read(path)
        row = next(r for r in rows if fid in r["positions"])
        s, _ = row["positions"][fid]
        row["text"] = row["text"][:s] + "#" + row["text"][s + 1:]
        _jl_write(path, rows)
    elif cls == "pdftext_dpi":
        ocr = next((root / "ocr" / "pdftext").glob("*/*.jsonl"))
        lines = _jl_read(ocr)
        line = lines[pick % len(lines)]
        line["bbox_px"] = [round(v) for v in line["bbox_pt"]]
        for t in line["tokens"] or []:
            t["bbox_px"] = [round(v) for v in t["bbox_pt"]]
        _jl_write(ocr, lines)
    elif cls == "rev_binding":
        if knob % 2 == 0:
            path = root / "build" / "src" / "export_map.jsonl"
            rows = _jl_read(path)
            next(r for r in rows if fid in r["positions"])["rev"] += 1
            _jl_write(path, rows)
        else:
            path = root / "build" / "build_receipt.json"
            receipt = json.loads(path.read_text(encoding="utf-8"))
            receipt["audit_bindings"][REC]["rev"] += 1
            path.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    elif cls == "alias_item":
        non_item = [f for f in fields if not f.endswith(".item")]
        target = non_item[pick % len(non_item)]
        path = root / "build" / "src" / "export_map.jsonl"
        rows = _jl_read(path)
        row = next(r for r in rows if target in r["positions"])
        s, e = row["positions"][target]
        current = row["text"][s:e]
        row["transforms"].append({"kind": "alias", "field_id": target, "from": current, "to": current})
        _jl_write(path, rows)
    else:  # pragma: no cover - the strategy only samples CLASSES
        raise AssertionError(cls)


@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(cls=st.sampled_from(sorted(CLASSES)), route=st.sampled_from(ROUTES),
       pick=st.integers(min_value=0, max_value=50), knob=st.integers(min_value=0, max_value=3))
def test_single_corruption_always_detected(cls, route, pick, knob):
    """00b3-12 (S4): any single corruption of any r3.1 invariant, on any route, is detected."""
    variant, expected = CLASSES[cls]
    with tempfile.TemporaryDirectory() as tmp:
        root = _copy(variant, Path(tmp))
        assert P.validate(root) == []
        _corrupt(cls, root, route, pick, knob)
        found = {v.code for v in P.validate(root)}
    assert found & expected, (cls, route, pick, knob, found)
