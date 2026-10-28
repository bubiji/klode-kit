"""WI-31 P-01 / P-02 (K4): the provenance chain on what the pipeline really produces - zero violations - and a validator
that notices when two records exchange values (impl-deviations WI-00b3 "待补").

P-01 (contract_replay): the whole user path on two fixtures - ``med-intake`` (which also makes the date alias
``work/<yyyymmdd>``), the clicks of the confirm page, ``kb-audit --writeback``, ``views``, ``build`` with the real
``klode`` (the replay lane practice, see ``tests/contract/test_build_steps.py``) - and then the 00b3 validator on the
data project and the finished build. ``fx-u1-3labs`` has three records (a page each) with a value in the same slot of
each; that is the product on which "two records exchange a value" is meaningful (``fx-chain`` has one record per
variant, so a validator that confused records could not be caught there).

P-02 (cli_integration): the same validator on the products the cli lane builds with the real ``klode``: the U5 medical
library (published) and the U7 book (a book has no audited records: nothing to chain, only the two artifacts a records
project would have are reported missing - and nothing else).

Found by this file (impl-deviations WI-31): with the date alias present the audit revisions were read twice and every
decision looked like two effective decisions - ``tests/unit/test_provenance_alias_audit.py`` is the unit reproducer.
"""

from __future__ import annotations

import itertools
import json
import shutil
from pathlib import Path

import pytest

from _support import accept_fx as A
from _support import build_fx as B
from klode_kit.contract import provenance as P

FIXTURES = ("fx-u8", "fx-u1-3labs")
SLOT = "r005-number-0"


def route_clicks(flow: A.Flow) -> list[dict]:
    """Every field decided by a route (the starred one when there is a star, else the first with a reading)."""
    out = []
    for fid, f in flow.fields().items():
        routes = [r for r in A.ROUTES if f["seen"].get(r) == f.get("recommended")] or \
            [r for r in A.ROUTES if f["seen"].get(r)]
        out.append({"field_id": fid, "kind": "route", "route": routes[0]})
    return out


def run_user_path(tmp: Path, fixture: str) -> tuple[A.Flow, Path]:
    flow = A.Flow(tmp, fixture)
    flow.intake()
    assert (flow.project / "work" / "20261022").is_symlink()  # the date alias of med-intake is part of the product
    done = flow.writeback(flow.export(route_clicks(flow)))
    assert done.returncode == 0, done.stderr.decode()
    # the second segment's other half: records nobody had to click (all four routes agree) are ingested
    ingest = flow.klode_kit("kb-audit", "--batch", flow.rig.batch_id, "--writeback")
    assert ingest.returncode == 0, ingest.stderr.decode()
    flow.views()
    build = flow.klode_kit("build")
    assert build.returncode == 0, build.stdout.decode()[-1500:] + build.stderr.decode()[-1500:]
    (receipt,) = sorted(flow.project.rglob("build_receipt.json"))
    return flow, receipt.parent


def validate(project: Path, build_dir: Path) -> list[P.Violation]:
    return P.validate(project, P.Layout.for_project(project, build_dir))


@pytest.fixture(scope="module")
def products(tmp_path_factory):
    cache: dict[str, tuple[A.Flow, Path]] = {}

    def get(fixture: str) -> tuple[A.Flow, Path]:
        if fixture not in cache:
            cache[fixture] = run_user_path(tmp_path_factory.mktemp(fixture), fixture)
        return cache[fixture]
    return get


@pytest.mark.contract_replay
@pytest.mark.parametrize("fixture", FIXTURES)
def test_provenance_roundtrip_on_pipeline_output(products, fixture):
    """P-01 (L1 S5): 0 violations on what the user path produced; and the check is not vacuous - several records,
    decisions by humans and by the audit, exported lines, a bound revision per record."""
    flow, build = products(fixture)
    assert validate(flow.project, build) == []
    receipt = B.receipt(build)
    rows = A.jsonl(build / "src" / "export_map.jsonl")
    records = sorted((flow.project / "records" / "_by_id").glob("*.md"))
    assert len(records) >= 2 and sorted(receipt["audit_bindings"]) == sorted(p.stem for p in records)
    assert len(rows) >= 2 * len(records)
    fixes = [json.loads(x) for p in sorted((flow.project / "work").glob("*/audit/a*/fixes.jsonl"))
             if not p.parent.parent.parent.is_symlink() for x in p.read_text(encoding="utf-8").splitlines() if x]
    assert {d["by"] for d in fixes} == {"auto", "human"}


def _unlock(root: Path) -> None:
    B.unlock(root)


def _copy(flow: A.Flow, build: Path, dest: Path) -> tuple[Path, Path]:
    project = dest / "proj"
    shutil.copytree(flow.project, project, symlinks=True)
    new_build = project / build.relative_to(flow.project)
    _unlock(project)
    return project, new_build


def _body_path(project: Path, rec: str) -> Path:
    front = A.front(project / "records" / "_by_id" / f"{rec}.md")
    return project / "records" / "_rev" / f"{rec}.r{front['effective']['rev']}.md"


def _read_body(path: Path) -> dict:
    return A.front(path)


def _write_body(path: Path, body: dict) -> None:
    path.write_text("---\n" + json.dumps(body, ensure_ascii=False, indent=1, sort_keys=True) + "\n---\n", encoding="utf-8")


@pytest.mark.contract_replay
def test_two_records_exchanging_a_value_is_a_violation(products, tmp_path):
    """P-01 (L1 S4/S5, the 00b3 "互换" case): on the three-record product, for every pair of records whose
    ``r005-number-0`` differ, exchanging those two values in the record revisions is reported - at both records."""
    flow, build = products("fx-u1-3labs")
    recs = sorted(p.stem for p in (flow.project / "records" / "_by_id").glob("*.md"))
    assert len(recs) == 3
    values = {r: _read_body(_body_path(flow.project, r))["fields"][f"{r}/{SLOT}"] for r in recs}
    pairs = [(a, b) for a, b in itertools.combinations(recs, 2) if values[a] != values[b]]
    assert len(pairs) >= 2, values
    for n, (a, b) in enumerate(pairs):
        project, new_build = _copy(flow, build, tmp_path / f"swap{n}")
        assert validate(project, new_build) == []  # the copy is clean before the swap
        body_a, body_b = _read_body(_body_path(project, a)), _read_body(_body_path(project, b))
        body_a["fields"][f"{a}/{SLOT}"], body_b["fields"][f"{b}/{SLOT}"] = values[b], values[a]
        _write_body(_body_path(project, a), body_a)
        _write_body(_body_path(project, b), body_b)
        found = validate(project, new_build)
        hits = {(v.code, v.field_id) for v in found}
        assert ("record_value_mismatch", f"{a}/{SLOT}") in hits and ("record_value_mismatch", f"{b}/{SLOT}") in hits, found


@pytest.mark.contract_replay
def test_two_export_rows_exchanging_their_audited_values_is_a_violation(products, tmp_path):
    """P-01 (L1 S4): the export map's ``value_audited`` of two records exchanged -> ``value_audited_mismatch`` on both
    rows (the export no longer says what the audit decided for that record)."""
    flow, build = products("fx-u1-3labs")
    project, new_build = _copy(flow, build, tmp_path)
    path = new_build / "src" / "export_map.jsonl"
    rows = A.jsonl(path)
    mine = [i for i, r in enumerate(rows) if any(f.endswith(f"/{SLOT}") for f in r["field_ids"]) and r["line_kind"] ==
            "observation"]
    pick = {}
    for i in mine:
        pick.setdefault(rows[i]["rec_id"], i)
    (i, j) = [pick[r] for r in sorted(pick)[:2]]
    assert rows[i]["value_audited"] != rows[j]["value_audited"]
    rows[i]["value_audited"], rows[j]["value_audited"] = rows[j]["value_audited"], rows[i]["value_audited"]
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    found = [v for v in validate(project, new_build) if v.code == "value_audited_mismatch"]
    assert {v.location.split(":")[-1] for v in found} >= {str(i + 1), str(j + 1)}, found


@pytest.mark.cli_integration
def test_provenance_roundtrip_on_cli_output(tmp_path):
    """P-02 (L1 S5): the validator on the cli lane's real-klode products. The U5 medical library (published): 0
    violations. The U7 book: it has no audited records, so the chain is empty; the only findings are the two
    artifacts of a records project (``records/_by_id``, ``work``) being absent - no chain violation of any code."""
    u5 = tmp_path / "u5"
    u5.mkdir()
    project = B.project(u5, "fx-rx-stamp")
    res = B.build(project, u5)
    assert res.returncode == 0, res.stderr.decode()
    (build,) = B.builds(project)
    A.check_and_publish(project, u5, build)
    assert validate(project, build) == []
    assert len(A.jsonl(build / "src" / "export_map.jsonl")) >= 3

    u7 = tmp_path / "u7"
    u7.mkdir()
    book = u7 / "proj"
    book.mkdir()
    done = B.build(book, u7, "--kb", "kb/demo-klode", *A.aurora_inputs(u7), "--loops", extra_env=B.writer_replay(u7))
    assert done.returncode == 0, done.stdout.decode()[-1200:] + done.stderr.decode()[-1200:]
    final = book / json.loads(done.stdout.decode().strip().splitlines()[-1])["build_dir"]
    found = validate(book, final)
    assert {(v.code, v.location) for v in found} == {("artifact_missing", "records/_by_id"),
                                                     ("artifact_missing", "work")}, found
    assert B.receipt(final)["audit_bindings"] == {}  # a book binds no audited record
