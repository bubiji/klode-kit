"""WI-01a on replayed chains: the export never depends on ``cells.jsonl`` (01a-04, S6) and its export map passes
the WI-00b3 provenance validator, segment (3) and the version binding (01a-07, S5).

Engine boundary: recorded engine outputs of the fixtures (pairing 06-11/12, 04a cli), as in WI-08a..08d.
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
from pathlib import Path

import pytest

from _support import export_fx as X
from klode_kit.build import records_to_txt as R
from klode_kit.contract import provenance as P
from klode_kit.contract import schema

pytestmark = pytest.mark.contract_replay

U8 = X.FIXTURES / "fx-u8"
CHAIN = X.FIXTURES / "fx-chain"


def tree_sha(root: Path) -> str:
    h = hashlib.sha256()
    for rel, data in X.tree(root).items():
        h.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return h.hexdigest()


def _delete(path: Path) -> None:
    path.rename(path.with_name("cells.jsonl.gone"))


def _shuffle(path: Path) -> None:
    path.chmod(0o644)  # kb-audit leaves cells.jsonl read-only (write-once, WI-08a)
    rows = path.read_text(encoding="utf-8").splitlines()
    random.Random(7).shuffle(rows)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _poison(path: Path) -> None:
    """Every candidate value and every item-free text in cells.jsonl replaced: a reader of cells would show it."""
    path.chmod(0o644)  # kb-audit leaves cells.jsonl read-only (write-once, WI-08a)
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    for r in rows:
        for c in r["candidates"].values():
            c["value"] = "999999"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


@pytest.mark.parametrize("mutate", [_delete, _shuffle, _poison], ids=["deleted", "shuffled", "poisoned"])
def test_cells_independence(tmp_path, mutate):
    """01a-04 (S6): on fx-u8 after the writeback, deleting, shuffling or rewriting every audit revision's
    ``cells.jsonl`` leaves the export byte-identical (tree sha and the returned result)."""
    project = tmp_path / "proj"
    project.mkdir()
    X.pipeline(project, U8)
    before = R.export(project, tmp_path / "before", kb_id="u8")
    cells = sorted(project.glob("work/*/audit/a*/cells.jsonl"))
    assert cells
    for path in cells:
        mutate(path)
    after = R.export(project, tmp_path / "after", kb_id="u8")
    assert tree_sha(tmp_path / "after") == tree_sha(tmp_path / "before")
    assert after == before
    assert "999999" not in json.dumps(after)
    assert X.lines(before, "items")


def _rebuild(tmp_path: Path, variant: str) -> tuple[Path, dict]:
    """Copy one fx-chain variant, set its fixture build aside, export into ``build/`` with the fixture's bindings
    and write the build receipt the validator reads."""
    root = tmp_path / variant
    shutil.copytree(CHAIN / variant, root)
    fixture_build = root / "build"
    old = json.loads((fixture_build / "build_receipt.json").read_text(encoding="utf-8"))
    fixture_build.rename(root / "build.fixture")
    res = R.export(root, root / "build", kb_id=old["kb_id"], bindings=old["audit_bindings"])
    receipt = {"audit_bindings": res["audit_bindings"], "deident_mode": old["deident_mode"], "kb_id": old["kb_id"]}
    (root / "build" / "build_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    return root, res


@pytest.mark.parametrize("variant", ["clean", "legal-alias", "legal-manual", "legal-oldbuild"])
def test_export_map_provenance(tmp_path, variant):
    """01a-07 (S5): fx-chain products exported by ``records_to_txt`` pass the 00b3 validator with zero
    violations (alias transform, manual value, an old build bound to rev 1 while effective is rev 2); export
    lines and export_map rows correspond one to one; every row is a legal ``export_map`` contract row."""
    root, res = _rebuild(tmp_path, variant)
    assert P.validate(root) == []
    rows = [json.loads(x) for x in (root / "build" / "src" / "export_map.jsonl").read_text(encoding="utf-8")
            .splitlines()]
    assert all(schema.issues("export_map", r) == [] for r in rows)
    on_disk = sorted((p.relative_to(root / "build").as_posix(), n, ln)
                     for p in (root / "build" / "library").rglob("*.txt")
                     for n, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1))
    assert sorted((r["source_file"], r["line_no"], r["text"]) for r in rows) == on_disk
    assert {r["rev"] for r in rows} == {1}


def test_export_map_tampered_value_is_caught(tmp_path):
    """01a-07 negative control: the same validator flags the export when one exported value is changed on disk
    and in the map (value_audited no longer the bound revision's value)."""
    root, _ = _rebuild(tmp_path, "legal-manual")
    path = root / "build" / "src" / "export_map.jsonl"
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    target = next(r for r in rows if r["value_audited"] == "1.05")
    target["value_audited"] = "10.5"
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    assert "value_audited_mismatch" in {v.code for v in P.validate(root)}
