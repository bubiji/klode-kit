"""WI-10b: doctor, data-project and test-seam checks (matrix 10b-03, 10b-07, 10b-08; design r2.5 doctor row,
r3.4 R2-07; decision F9; impl-deviations WI-04b ``KLODE_KIT_ENGINE_REPLAY``, WI-05a ``_masked/ _mask_review/``,
WI-01c ``_private/``).

The public entry is ``doctor.main(argv)``; ``--only project`` / ``--only seams`` restrict the run to the
sections under test, so these cases need no external program. Every case asserts the exit code, the receipt
written with ``--receipt`` and the fix text on stderr.

Trust (10b-03) is read from a recorded ``~/.claude.json`` shape (the store ``hostenv.trusted`` reads, WI-00d2 ⑬)
under a temporary ``HOME``; paired real path: 00d2-05.

No law: the doctor checks the environment item by item; the only stated relation (exit 0 iff no check failed,
``missing`` == number of failed checks) is asserted in every case.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from klode_kit.check import doctor

pytestmark = pytest.mark.unit

GITIGNORE_ENTRIES = ("_work/", "_raw/", "_masked/", "_mask_review/", "_private/", "work/")
SEAM_VARS = ("KLODE_KIT_CRASH_AT", "KLODE_KIT_PAUSE_AT", "KLODE_KIT_PAUSE_DIR", "KLODE_KIT_ENGINE_REPLAY")
TRUST_HINT = "在数据项目目录交互运行一次 claude"


def _receipt(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    failed = [c for c in data["checks"] if not c["ok"]]
    assert data["missing"] == len(failed)
    assert data["ok"] is (not failed)
    return data


def _check(receipt: dict, check_id: str) -> dict:
    found = [c for c in receipt["checks"] if c["id"] == check_id]
    assert len(found) == 1, [c["id"] for c in receipt["checks"]]
    return found[0]


def _project(root: Path, entries=GITIGNORE_ENTRIES) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    if entries is not None:
        (root / ".gitignore").write_text("".join(f"{e}\n" for e in entries), encoding="utf-8")
    return root


def _home(tmp_path: Path, monkeypatch, projects: dict | None) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    if projects is not None:
        (home / ".claude.json").write_text(json.dumps({"projects": projects}), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return home


def _trusting(project: Path, accepted: bool = True) -> dict:
    return {os.path.realpath(project): {"hasTrustDialogAccepted": accepted, "allowedTools": []}}


def _clean_seams(monkeypatch) -> None:
    for name in (*SEAM_VARS, "KLODE_KIT_TEST_SEAMS", "KLODE_KIT_REAL"):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- 10b-03 trust (R2-07)


def test_untrusted_project_exit40(tmp_path, monkeypatch, capsys):
    """10b-03 (L1/S3): the recorded probe says "not trusted" -> exit 40 with the R2-07 hint."""
    project = _project(tmp_path / "data")
    _home(tmp_path, monkeypatch, _trusting(project, accepted=False))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    err = capsys.readouterr().err
    assert rc == 40
    receipt = _receipt(out)
    trust = _check(receipt, "project_trust")
    assert trust["ok"] is False
    assert TRUST_HINT in trust["fix"]
    assert TRUST_HINT in err
    assert [c["id"] for c in receipt["checks"] if not c["ok"]] == ["project_trust"]


def test_trusted_project_passes(tmp_path, monkeypatch, capsys):
    """10b-03 boundary (L1/S2): the same project, trust accepted -> exit 0, nothing missing."""
    project = _project(tmp_path / "data")
    _home(tmp_path, monkeypatch, _trusting(project, accepted=True))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    assert rc == 0, capsys.readouterr().err
    receipt = _receipt(out)
    assert receipt["missing"] == 0
    assert _check(receipt, "project_trust")["ok"] is True


def test_trust_undetectable_exit40(tmp_path, monkeypatch, capsys):
    """10b-03 failure mode (L1/S3): no readable trust store -> the precondition cannot be shown -> exit 40."""
    project = _project(tmp_path / "data")
    _home(tmp_path, monkeypatch, None)
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    err = capsys.readouterr().err
    assert rc == 40
    trust = _check(_receipt(out), "project_trust")
    assert trust["ok"] is False
    assert "undetectable" in trust["detail"]
    assert TRUST_HINT in err


def test_trusted_parent_does_not_count(tmp_path, monkeypatch, capsys):
    """10b-03 (L1/S3): only the data project's own trust activates settings.local.json (WI-00d2 ⑬)."""
    project = _project(tmp_path / "parent" / "data")
    _home(tmp_path, monkeypatch, _trusting(tmp_path / "parent"))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    capsys.readouterr()
    assert rc == 40
    assert _check(_receipt(out), "project_trust")["ok"] is False


# --------------------------------------------------------------------------- 10b-07 .gitignore


@pytest.mark.parametrize("missing", GITIGNORE_ENTRIES)
def test_gitignore_missing_entries_exit40(tmp_path, monkeypatch, capsys, missing):
    """10b-07 (L1/S2): each required entry left out on its own -> exit 40 naming exactly that entry."""
    project = _project(tmp_path / "data", [e for e in GITIGNORE_ENTRIES if e != missing])
    _home(tmp_path, monkeypatch, _trusting(project))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    err = capsys.readouterr().err
    assert rc == 40
    receipt = _receipt(out)
    check = _check(receipt, "project_gitignore")
    assert check["ok"] is False
    assert check["missing_entries"] == [missing]
    assert missing in err
    assert missing in check["fix"]
    assert [c["id"] for c in receipt["checks"] if not c["ok"]] == ["project_gitignore"]


def test_gitignore_absent_lists_every_entry(tmp_path, monkeypatch, capsys):
    """10b-07 boundary (L1/S2): no .gitignore at all -> every required entry is missing."""
    project = _project(tmp_path / "data", None)
    _home(tmp_path, monkeypatch, _trusting(project))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    capsys.readouterr()
    assert rc == 40
    assert _check(_receipt(out), "project_gitignore")["missing_entries"] == list(GITIGNORE_ENTRIES)


def test_gitignore_equivalent_spellings_accepted(tmp_path, monkeypatch, capsys):
    """10b-07 boundary (L1/S2): ``/work/``, ``_raw`` (no slash) and comments / blank lines are fine."""
    text = "# data project\n\n/_work/\n_raw\n/_masked\n_mask_review/\n  _private/  \n/work/\n"
    project = _project(tmp_path / "data", None)
    (project / ".gitignore").write_text(text, encoding="utf-8")
    _home(tmp_path, monkeypatch, _trusting(project))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    assert rc == 0, capsys.readouterr().err
    assert _check(_receipt(out), "project_gitignore")["ok"] is True


def test_gitignore_negation_or_comment_does_not_count(tmp_path, monkeypatch, capsys):
    """10b-07 (L1/S3): ``!_private/`` and ``# _private/`` do not ignore the directory."""
    entries = [e for e in GITIGNORE_ENTRIES if e != "_private/"] + ["!_private/", "# _private/"]
    project = _project(tmp_path / "data", entries)
    _home(tmp_path, monkeypatch, _trusting(project))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    capsys.readouterr()
    assert rc == 40
    assert _check(_receipt(out), "project_gitignore")["missing_entries"] == ["_private/"]


# --------------------------------------------------------------------------- 10b-08 leftover test seams (F9)


@pytest.mark.parametrize("var", SEAM_VARS)
@pytest.mark.parametrize("mode", ["no_test_seams", "real"])
def test_doctor_reports_ignored_seam_vars(tmp_path, monkeypatch, capsys, var, mode):
    """10b-08 (L1/S3): a seam variable left set outside test mode -> exit 40, the variable named, `unset` given."""
    _clean_seams(monkeypatch)
    monkeypatch.setenv(var, "x")
    if mode == "real":
        monkeypatch.setenv("KLODE_KIT_TEST_SEAMS", "1")
        monkeypatch.setenv("KLODE_KIT_REAL", "1")
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--only", "seams", "--receipt", str(out)])
    err = capsys.readouterr().err
    assert rc == 40
    check = _check(_receipt(out), "seams")
    assert check["ok"] is False
    assert var in check["leftover"]
    assert f"unset {' '.join(check['leftover'])}" in check["fix"]
    assert var in err and "unset" in err
    if mode == "real":
        assert check["leftover"] == ["KLODE_KIT_TEST_SEAMS", var]
    else:
        assert check["leftover"] == [var]


def test_doctor_seams_clean_env_passes(tmp_path, monkeypatch, capsys):
    """10b-08 (L1/S2): none of the variables set -> this item passes (exit 0)."""
    _clean_seams(monkeypatch)
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--only", "seams", "--receipt", str(out)])
    assert rc == 0, capsys.readouterr().err
    receipt = _receipt(out)
    assert _check(receipt, "seams")["ok"] is True
    assert receipt["test_mode"] is False


def test_doctor_seams_test_mode_passes(tmp_path, monkeypatch, capsys):
    """10b-08 (L1/S2): inside test mode (TEST_SEAMS=1, REAL unset) the seams are meant to be there."""
    _clean_seams(monkeypatch)
    monkeypatch.setenv("KLODE_KIT_TEST_SEAMS", "1")
    monkeypatch.setenv("KLODE_KIT_PAUSE_AT", "x")
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--only", "seams", "--receipt", str(out)])
    assert rc == 0, capsys.readouterr().err
    receipt = _receipt(out)
    assert _check(receipt, "seams")["ok"] is True
    assert receipt["test_mode"] is True


def test_doctor_seams_test_flag_under_real_is_leftover(tmp_path, monkeypatch, capsys):
    """10b-08 (L1/S3): KLODE_KIT_TEST_SEAMS=1 together with KLODE_KIT_REAL=1 is itself a leftover."""
    _clean_seams(monkeypatch)
    monkeypatch.setenv("KLODE_KIT_TEST_SEAMS", "1")
    monkeypatch.setenv("KLODE_KIT_REAL", "1")
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--only", "seams", "--receipt", str(out)])
    err = capsys.readouterr().err
    assert rc == 40
    assert _check(_receipt(out), "seams")["leftover"] == ["KLODE_KIT_TEST_SEAMS"]
    assert "unset KLODE_KIT_TEST_SEAMS" in err


def test_doctor_seams_report_does_not_stop_seams_module(tmp_path, monkeypatch, capsys):
    """10b-08 / F9 (L1/S2): the doctor only reports; it does not change the variables of its own process."""
    _clean_seams(monkeypatch)
    monkeypatch.setenv("KLODE_KIT_CRASH_AT", "x")
    doctor.main(["--only", "seams", "--receipt", str(tmp_path / "d.json")])
    capsys.readouterr()
    assert os.environ["KLODE_KIT_CRASH_AT"] == "x"


# --------------------------------------------------------------------------- receipt / usage


def test_doctor_stdout_is_the_receipt(tmp_path, monkeypatch, capsys):
    """The receipt goes to stdout as JSON as well as to --receipt; both are the same document."""
    _clean_seams(monkeypatch)
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--only", "seams", "--receipt", str(out)])
    stdout = capsys.readouterr().out
    assert rc == 0
    assert json.loads(stdout) == json.loads(out.read_text(encoding="utf-8"))
    assert json.loads(stdout)["schema"] == "klode-kit.doctor/1"


@pytest.mark.parametrize("argv", [["--only", "nonsense"], ["--only", "project"], ["--bogus"]])
def test_doctor_usage_errors_exit2(tmp_path, monkeypatch, capsys, argv):
    """Guard (L1/S3): an unknown section, ``--only project`` without ``--project``, an unknown flag -> exit 2."""
    _clean_seams(monkeypatch)
    rc = doctor.main(argv)
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""


# --------------------------------------------------------------------------- added while implementing (project)


def test_settings_local_not_writable_exit40(tmp_path, monkeypatch, capsys):
    """R2-07 "未跟踪可写" (L1/S3): an untracked but read-only settings.local.json -> exit 40, chmod fix."""
    project = _project(tmp_path / "data")
    (project / ".claude").mkdir()
    target = project / ".claude" / "settings.local.json"
    target.write_text("{}\n", encoding="utf-8")
    target.chmod(0o444)
    _home(tmp_path, monkeypatch, _trusting(project))
    out = tmp_path / "doctor.json"
    try:
        rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    finally:
        target.chmod(0o644)
    capsys.readouterr()
    assert rc == 40
    check = _check(_receipt(out), "project_settings_local")
    assert check["ok"] is False and "not writable" in check["detail"] and "chmod u+w" in check["fix"]


@pytest.mark.parametrize("content", ["{not json", '{"mcpServers": []}'])
def test_mcp_json_unreadable_exit40(tmp_path, monkeypatch, capsys, content):
    """V5 (L1/S3): an unparseable `.mcp.json` is reported, never silently skipped."""
    project = _project(tmp_path / "data")
    (project / ".mcp.json").write_text(content, encoding="utf-8")
    _home(tmp_path, monkeypatch, _trusting(project))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    capsys.readouterr()
    assert rc == 40
    assert _check(_receipt(out), "project_mcp")["ok"] is False


def test_mcp_json_launcher_not_executable_and_others_ignored(tmp_path, monkeypatch, capsys):
    """V5 (L1/S3): a registered launcher without the x bit fails; servers not started by klode-kit are not ours."""
    project = _project(tmp_path / "data")
    launcher = tmp_path / "plugin" / "bin" / "klode-kit"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o644)
    servers = {"a-klode": {"command": str(launcher), "args": ["mcp"]},
               "other": {"command": "/nonexistent/other-server"},
               "relative": {"command": "bin/klode-kit"}}
    (project / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    _home(tmp_path, monkeypatch, _trusting(project))
    out = tmp_path / "doctor.json"
    rc = doctor.main(["--project", str(project), "--only", "project", "--receipt", str(out)])
    capsys.readouterr()
    assert rc == 40
    check = _check(_receipt(out), "project_mcp")
    assert check["servers"] == ["a-klode", "relative"]
    assert "not executable" in check["detail"] and "relative: registered launcher missing" in check["detail"]
    assert "other" not in check["detail"].replace("other-server", "")


def test_project_not_a_directory_exit2(tmp_path, monkeypatch, capsys):
    """Guard (L1/S3): --project naming no directory -> usage exit 2, nothing on stdout."""
    _clean_seams(monkeypatch)
    rc = doctor.main(["--project", str(tmp_path / "nope"), "--only", "seams"])
    assert rc == 2
    assert capsys.readouterr().out == ""
