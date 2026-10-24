"""WI-09b: ``klode-kit records-to-txt`` - the command the build runs as its step 1 (``records_to_txt.main``).

The export itself is specified by WI-01a / 01c; here only the command surface: the exact snapshot it is given is
what gets exported (R3-07 / r3.1 version binding), the JSON summary on stdout, and the exit codes.
"""

from __future__ import annotations

import json

import pytest

from _support import export_fx as X
from klode_kit.build import deident as deident_mod
from klode_kit.build import records_to_txt as R

pytestmark = pytest.mark.unit

FIELDS = {"r-000000000001/r001-drug-0": "示例片", "r-000000000001/r001-number-0": "10", "r-000000000001/r001-unit-0": "mg"}


def _project(tmp_path, n=2):
    project = tmp_path / "proj"
    project.mkdir()
    for i in range(1, n + 1):
        rid = f"r-{i:012x}"
        fields = {k.replace("r-000000000001", rid): v for k, v in FIELDS.items()}
        X.put_record(project, rid, {1: {"status": "verified", "audit_rev": "0123456789ab/a1", "fields": fields}},
                     effective=1, date="2026-10-01", rtype="rx")
    return project


def _run(capsys, *args):
    code = R.main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_summary_and_files(tmp_path, capsys):
    project = _project(tmp_path)
    out = tmp_path / "out"
    code, text, _ = _run(capsys, "--project", str(project), "--out", str(out), "--kb-id", "k")
    assert code == 0
    summary = json.loads(text.splitlines()[-1])
    assert summary["kb_id"] == "k" and sorted(summary["audit_bindings"]) == ["r-000000000001", "r-000000000002"]
    assert summary["excluded"] == [] and summary["files"] == sorted(summary["files"])
    for rel in summary["files"]:
        assert (out / rel).is_file()
    assert (out / "src" / "export_map.jsonl").is_file()


def test_only_the_given_snapshot_is_exported(tmp_path, capsys):
    project = _project(tmp_path)
    snap = tmp_path / "bindings.json"
    snap.write_text(json.dumps({"r-000000000002": {"rev": 1, "audit_rev": "0123456789ab/a1"}}), encoding="utf-8")
    code, text, _ = _run(capsys, "--project", str(project), "--out", str(tmp_path / "o"), "--bindings", str(snap),
                         "--deident", "pseudonym")
    assert code == 0
    assert list(json.loads(text.splitlines()[-1])["audit_bindings"]) == ["r-000000000002"]


def test_existing_target_is_refused_exit_1(tmp_path, capsys):
    project = _project(tmp_path)
    out = tmp_path / "out"
    assert _run(capsys, "--project", str(project), "--out", str(out))[0] == 0
    code, text, err = _run(capsys, "--project", str(project), "--out", str(out))
    assert code == 1 and text == "" and "refusing to overwrite" in err


@pytest.mark.parametrize("content", ["not json", "[1]"])
def test_unreadable_bindings_is_usage_exit_2(tmp_path, capsys, content):
    project = _project(tmp_path)
    snap = tmp_path / "bad.json"
    snap.write_text(content, encoding="utf-8")
    code, text, err = _run(capsys, "--project", str(project), "--out", str(tmp_path / "o"), "--bindings", str(snap))
    assert code == 2 and text == "" and "bindings" in err
    assert not (tmp_path / "o").exists()


def test_missing_argument_is_usage_exit_2(capsys):
    assert _run(capsys, "--project", "x")[0] == 2


def test_broken_deident_key_is_exit_40(tmp_path, capsys):
    project = _project(tmp_path)
    key = project / deident_mod.KEY_REL
    key.parent.mkdir(parents=True)
    key.write_text("{broken", encoding="utf-8")
    code, text, err = _run(capsys, "--project", str(project), "--out", str(tmp_path / "o"), "--deident", "pseudonym")
    assert code == 40 and text == "" and "de-identification" in err
