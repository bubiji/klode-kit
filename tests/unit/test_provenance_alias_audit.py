"""WI-31 finding: a date alias ``work/<yyyymmdd>`` (symlink to a batch, WI-13b) made the audit revisions of its batch
count twice for the provenance validator (the ``*/audit/a*/`` glob walks through the link), so every decision looked
like two effective decisions (``multiple_effective_decisions``) and the build of a project that went through
``med-intake`` failed its ``self_check``. ``test_provenance_alias_links.py`` (WI-13b) covers the batch enumeration of
``Layout.for_project`` only; this file covers the audit revisions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from klode_kit.contract import provenance as P

pytestmark = pytest.mark.unit

BATCH = "a1b2c3d4e5f6"
FID = "r-0123456789ab/r005-number-0"


def _project(tmp_path: Path, aliases: tuple[str, ...]) -> Path:
    rev = tmp_path / "work" / BATCH / "audit" / "a1"
    rev.mkdir(parents=True)
    (rev / "runs.json").write_text("{}", encoding="utf-8")
    (rev / "cells.jsonl").write_text("", encoding="utf-8")
    decision = {"decision_id": f"{BATCH}/a1/d0001", "audit_rev": f"{BATCH}/a1", "field_id": FID, "rev_base": 0,
                "rev_new": 1, "choice": "none", "by": "human", "at": "2026-10-22T00:00:00Z",
                "commit_id": f"{BATCH}/a1/c0001"}
    (rev / "fixes.jsonl").write_text(json.dumps(decision) + "\n", encoding="utf-8")
    for alias in aliases:
        (tmp_path / "work" / alias).symlink_to(BATCH)
    return tmp_path


def _loaded(project: Path) -> P._Chain:
    chain = P._Chain(P._Ctx(P.Layout.for_project(project, project / "build")))
    chain.load_audit()
    return chain


@pytest.mark.parametrize("aliases", [(), ("20261022",), ("20261022", "20261022-2")], ids=["none", "one", "two"])
def test_each_audit_revision_and_decision_counts_once_whatever_links_point_at_its_batch(tmp_path, aliases):
    chain = _loaded(_project(tmp_path, aliases))
    assert list(chain.runs) == [f"work/{BATCH}/audit/a1"]
    assert [where for where, _ in chain.decisions[FID]] == [f"work/{BATCH}/audit/a1/fixes.jsonl:1"]
    assert chain.effective(FID, 1) == chain.decisions[FID]  # one effective decision, not "2 decisions effective"


def test_a_real_directory_next_to_the_batches_is_still_an_audit_directory(tmp_path):
    """The filter drops links only: a second real batch directory keeps its revisions."""
    project = _project(tmp_path, ("20261022",))
    other = project / "work" / "ffffffffffff" / "audit" / "a1"
    other.mkdir(parents=True)
    (other / "runs.json").write_text("{}", encoding="utf-8")
    (other / "cells.jsonl").write_text("", encoding="utf-8")
    chain = _loaded(project)
    assert sorted(chain.runs) == [f"work/{BATCH}/audit/a1", "work/ffffffffffff/audit/a1"]
