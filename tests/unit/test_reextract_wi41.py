"""WI-41 part B: ``kb-audit --batch B --reextract`` and ``fields.FIELDS_RULES`` - unit lane (spec
``.claude/tdd-guardian/wi-41-spec.md``).

The project is the one the public stages leave for ``fx-u1-3labs`` (``_support/reopen_fx``): ``AUTO_REC`` is
``verified(auto)`` at r1, ``PENDING_REC`` / ``OTHER_REC`` wait for a human. A batch "audited under rules 1" is made
by deleting ``a1/rules.json`` in the tmp copy (an older revision has no such file). Everything is asserted on files,
exit codes and the stage's stdout line; no mock, no OCR call (the stage never makes one: the run set is read from the
kb-ocr receipt).

Laws (S5): ``--reextract`` is idempotent (a second run changes nothing) and never rewrites an existing revision
(every file of ``a1`` and every file outside ``audit/`` is byte-identical after it); the plain command never moves
off a revision whatever its rules.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from _support import reopen_fx as RF
from _support import writeback_fx as W
from _support.kb_ocr import tree
from _support.reopen_fx import AUTO_REC, PENDING_REC, base, template  # noqa: F401
from klode_kit.audit import fields as F
from klode_kit.audit import stage
from klode_kit.audit.stage import main as stage_main

pytestmark = pytest.mark.unit

OTHER_REC = "r-27bf950bdbbd"
R005, R007, R009 = (f"{PENDING_REC}/{s}" for s in ("r005-number-0", "r007-number-0", "r009-unit-0"))
PENDING_TODO = (R005, R007, R009)
NEW = "new (reextract: rules 1 -> 2)"


# --------------------------------------------------------------------------- helpers


def audit(project: Path, batch: str, *, reextract: bool = False) -> tuple[int, str]:
    err = io.StringIO()
    kw = {"reextract": True} if reextract else {}
    code = stage.run(project, batch, out=err, **kw)
    return code, err.getvalue()


def rules_of(adir: Path):
    path = adir / "rules.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def as_rules1(adir: Path) -> None:
    """Make revision ``adir`` look like one an older kb-audit wrote: no rules.json."""
    path = adir / "rules.json"
    if path.exists():
        os.chmod(path, 0o644)
        path.unlink()


def revs(project: Path, b: str) -> list[str]:
    root = project / "work" / b / "audit"
    return sorted((p.name for p in root.iterdir() if p.is_dir() and p.name.startswith("a")),
                  key=lambda n: int(n[1:]))


def outside_audit(project: Path) -> dict[str, bytes]:
    return {k: v for k, v in tree(project).items() if not k.endswith("/") and "/audit/" not in k
            and not k.endswith(".lock")}


def under(project: Path, b: str, rev: str) -> dict[str, bytes]:
    root = project / "work" / b / "audit" / rev
    return {k: v for k, v in tree(root).items() if not k.endswith("/")}


def records_tree(project: Path) -> dict[str, bytes]:
    return tree(project / "records") if (project / "records").is_dir() else {}


# --------------------------------------------------------------------------- the rules revision


def test_fields_rules_is_two_and_a_fresh_revision_records_it(base):
    """A new revision writes ``rules.json`` ``{"fields_rules": 2}`` (read-only like the other write-once files)."""
    assert F.FIELDS_RULES == 2
    assert rules_of(base.adir) == {"fields_rules": 2}
    assert os.stat(base.adir / "rules.json").st_mode & 0o222 == 0


# --------------------------------------------------------------------------- default behaviour is unchanged


def test_plain_audit_of_a_rules1_batch_reuses_a1(base):
    as_rules1(base.adir)
    before = tree(base.project)
    code, text = audit(base.project, base.batch)
    assert code == 10 and "audit revision a1 (reused)" in text
    assert revs(base.project, base.batch) == ["a1"]
    assert rules_of(base.adir) is None            # a revision is never rewritten, not even to add the file
    assert {k: v for k, v in tree(base.project).items() if not k.endswith(".lock")} == \
        {k: v for k, v in before.items() if not k.endswith(".lock")}


# --------------------------------------------------------------------------- --reextract


def test_reextract_opens_a2_from_the_same_run_set_without_touching_anything_else(base):
    p, b = base.project, base.batch
    as_rules1(base.adir)
    a1_before, outside_before, records_before = under(p, b, "a1"), outside_audit(p), records_tree(p)

    code, text = audit(p, b, reextract=True)

    assert code == 10, text
    assert f"audit revision a2 ({NEW})" in text
    assert revs(p, b) == ["a1", "a2"]
    a2 = p / "work" / b / "audit" / "a2"
    assert rules_of(a2) == {"fields_rules": 2}
    assert json.loads((a2 / "runs.json").read_text()) == json.loads((base.adir / "runs.json").read_text())
    assert (a2 / "cells.jsonl").is_file() and (a2 / "candidates.jsonl").is_file()
    cands = W.candidates(a2)
    assert {c["audit_rev"] for c in cands} == {f"{b}/a2"}
    assert [c["rec_id"] for c in cands] == [c["rec_id"] for c in W.candidates(base.adir)]
    assert under(p, b, "a1") == a1_before          # a1 not rewritten
    assert outside_audit(p) == outside_before      # no OCR run, no receipt change, no derived image touched
    assert records_tree(p) == records_before       # candidates only: effective untouched


def test_reextract_with_current_rules_is_the_plain_command(base):
    """Same rules -> reuse, ``unchanged``: not a file written."""
    before = tree(base.project)
    code, text = audit(base.project, base.batch, reextract=True)
    assert code == 10 and "audit revision a1 (reused)" in text and "reextract" not in text
    assert {k: v for k, v in tree(base.project).items() if not k.endswith(".lock")} == \
        {k: v for k, v in before.items() if not k.endswith(".lock")}


def test_second_reextract_reuses_a2(base):
    p, b = base.project, base.batch
    as_rules1(base.adir)
    assert audit(p, b, reextract=True)[0] == 10
    after_first = tree(p)
    code, text = audit(p, b, reextract=True)
    assert code == 10 and "audit revision a2 (reused)" in text
    assert revs(p, b) == ["a1", "a2"]
    assert {k: v for k, v in tree(p).items() if not k.endswith(".lock")} == \
        {k: v for k, v in after_first.items() if not k.endswith(".lock")}


def test_a_revision_recording_rules_1_explicitly_is_reextracted_too(base):
    as_rules1(base.adir)
    path = base.adir / "rules.json"
    path.write_text(json.dumps({"fields_rules": 1}), encoding="utf-8")
    code, text = audit(base.project, base.batch, reextract=True)
    assert code == 10 and NEW in text and revs(base.project, base.batch) == ["a1", "a2"]


def test_reextract_does_not_open_a_revision_when_the_run_set_changed_for_other_reasons(base):
    """A changed run set is a new revision for the plain command too; the line then does not claim a reextract."""
    p, b = base.project, base.batch
    rp = p / "work" / b / "receipt.json"
    rec = json.loads(rp.read_text(encoding="utf-8"))
    first = next(iter(rec["attempts"]))
    rec["attempts"][first] = {**rec["attempts"][first], "run_id": rec["attempts"][first]["run_id"] + "x"}
    rp.write_text(json.dumps(rec), encoding="utf-8")
    code, text = audit(p, b, reextract=True)
    assert code == 10 and "audit revision a2 (new)" in text and "reextract" not in text


# --------------------------------------------------------------------------- records across a reextract


def test_verified_record_keeps_effective_and_a2_writeback_works(base, tmp_path):
    p, b = base.project, base.batch
    as_rules1(base.adir)
    front_before = W.front(p, AUTO_REC)
    assert front_before["status"] == "verified"
    assert audit(p, b, reextract=True)[0] == 10
    a2 = p / "work" / b / "audit" / "a2"
    assert W.front(p, AUTO_REC) == front_before

    sel = W.picks(a2)                                   # every field the a2 page asks for
    assert sel
    code, receipt, err = W.submit(p, W.export(p, b, a2, sel), tmp_path / "w")
    assert code == 0, err
    results = {r["rec_id"]: r["result"] for r in receipt["per_record"]}
    assert results[PENDING_REC] == "committed" and results[OTHER_REC] == "committed"
    assert W.front(p, PENDING_REC)["status"] == "verified"
    assert W.front(p, PENDING_REC)["effective"]["audit_rev"] == f"{b}/a2"
    # the record verified at a1 is not churned by the new revision's auto-verified candidate
    assert W.front(p, AUTO_REC)["effective"] == front_before["effective"]


def test_again_works_on_a2(base, tmp_path):
    from klode_kit.audit import again as AG

    p, b = base.project, base.batch
    as_rules1(base.adir)
    assert audit(p, b, reextract=True)[0] == 10
    a2 = p / "work" / b / "audit" / "a2"
    assert W.submit(p, None, tmp_path / "ingest", batch_id=b)[0] == 10      # the records catch up with a2 first
    sel = [{"field_id": f, "kind": "none"} for f in PENDING_TODO] + W.picks(a2, [OTHER_REC])
    code, _, err = W.submit(p, W.export(p, b, a2, sel), tmp_path / "w")
    assert code == 10, err
    assert W.front(p, PENDING_REC)["status"] == "disputed"

    out, stdout = io.StringIO(), io.StringIO()
    code = AG.run(p, batch_id=b, out=out, stdout=stdout)
    assert code == 10, out.getvalue()
    rc = json.loads([x for x in stdout.getvalue().splitlines() if x.strip()][-1])
    assert (rc["result"], rc["audit_rev"], rc["again_id"]) == ("opened", f"{b}/a2", f"{b}/a2/g0001")
    assert (p / rc["page"]).is_file() and rc["page"].startswith(f"work/{b}/audit/a2/again/")


def test_reextract_is_idempotent_and_rewrites_no_existing_file(base):
    """LAW (S5): after any number of ``--reextract`` runs the files of a1 and everything outside audit/ are as they
    were, and the second run changes nothing at all."""
    p, b = base.project, base.batch
    as_rules1(base.adir)
    a1, outside = under(p, b, "a1"), outside_audit(p)
    assert audit(p, b, reextract=True)[0] == 10
    snap = {k: v for k, v in tree(p).items() if not k.endswith(".lock")}
    for _ in range(2):
        assert audit(p, b, reextract=True)[0] == 10
    assert {k: v for k, v in tree(p).items() if not k.endswith(".lock")} == snap
    assert under(p, b, "a1") == a1 and outside_audit(p) == outside


# --------------------------------------------------------------------------- usage


@pytest.mark.parametrize("extra", [
    [],                                                  # no --batch
    ["--batch", "0123456789ab", "--writeback"],
    ["--batch", "0123456789ab", "--reopen", "r-000000000000/r001-number-0"],
    ["--batch", "0123456789ab", "--again"],
    ["--batch", "0123456789ab", "--attribute", "--reviewer-model", "m", "--reviewer-rule", "r"],
    ["--batch", "0123456789ab", "--redate"],
    ["--batch", "0123456789ab", "--redate", "--apply"],
])
def test_reextract_usage_errors_exit_2(tmp_path, extra, capsys):
    code = stage_main(["--project", str(tmp_path), "--reextract", *extra])
    assert code == 2
    named = "kb-audit: --reextract" in capsys.readouterr().err   # our own usage line, not the parser's complaint
    assert named, "the usage line must name --reextract"
    assert not (tmp_path / "work").exists()


def test_reextract_with_a_bad_batch_id_is_usage(tmp_path, capsys):
    code = stage_main(["--project", str(tmp_path), "--batch", "nothex", "--reextract"])
    named = "12 hex characters" in capsys.readouterr().err
    assert code == 2 and named, "the batch id must be rejected by the batch-id check"


def test_reextract_through_main_runs_the_stage(base, capsys):
    as_rules1(base.adir)
    code = stage_main(["--project", str(base.project), "--batch", base.batch, "--reextract"])
    capsys.readouterr()
    assert code == 10 and revs(base.project, base.batch) == ["a1", "a2"]


def test_reextract_flag_is_documented_in_the_skill_and_the_command_list():
    root = Path(__file__).resolve().parents[2]
    assert "--reextract" in (root / "skills" / "kb-audit" / "SKILL.md").read_text(encoding="utf-8")
    assert "--reextract" in (root / "src" / "klode_kit" / "__main__.py").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- observed behaviour across the revisions


def test_ingest_of_a2_commits_a_new_revision_of_every_record_even_the_verified_one(base, tmp_path):
    """Observed (existing writeback rules, unchanged): ``--writeback --batch`` ingests the newest revision, so the
    record verified at a1 gets r2 (``verified``, auto, ``audit_rev`` a2) with the same values; the waiting ones get a
    new pending_confirm r2. Nothing else about them changes."""
    p, b = base.project, base.batch
    as_rules1(base.adir)
    assert audit(p, b, reextract=True)[0] == 10
    before = W.body(p, AUTO_REC, 1)["fields"]
    code, receipt, err = W.submit(p, None, tmp_path / "w", batch_id=b)
    assert code == 10, err
    got = {r["rec_id"]: (r["result"], r["rev_new"], r["status"]) for r in receipt["per_record"]}
    assert got == {AUTO_REC: ("committed", 2, "verified"), PENDING_REC: ("committed", 2, "pending_confirm"),
                   OTHER_REC: ("committed", 2, "pending_confirm")}
    front = W.front(p, AUTO_REC)
    assert front["effective"] == {"rev": 2, "audit_rev": f"{b}/a2", "status": "verified", "verified_by": "auto"}
    assert W.body(p, AUTO_REC, 2)["fields"] == before          # rules did not change what the routes agreed on


def test_again_waits_for_the_ingest_of_a2(base):
    """Observed: ``--again`` refuses (exit 30, "run kb-audit --writeback --batch first") while a record's candidate is
    older than the newest revision - the existing rule, now reached through a reextract."""
    from klode_kit.audit import again as AG

    p, b = base.project, base.batch
    as_rules1(base.adir)
    assert audit(p, b, reextract=True)[0] == 10
    out = io.StringIO()
    code = AG.run(p, batch_id=b, out=out, stdout=io.StringIO())
    waits = "kb-audit --writeback" in out.getvalue()
    assert code == 30 and waits
