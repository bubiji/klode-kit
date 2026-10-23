"""WI-05b: sender edge cases added after the first green run (coverage of ``ocr/send.py``, a criticalPaths entry).

Same boundary and public surface as tests/contract/test_send.py (``send.send`` / the ``python -m klode_kit.ocr.send``
CLI; ``_support.send_fake.FakeCloud`` for the cloud CLIs; paired real path: model lane 06-11/12, 00c2-06/07).
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from _support.flows import base_env
from _support.send_fake import FakeCloud, isolation_receipt, masked_project, routes_state, source_env
from klode_kit.ocr import send as S

pytestmark = pytest.mark.contract_replay


@pytest.fixture
def env(tmp_path, monkeypatch):
    systmp = tmp_path / "systmp"
    systmp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(systmp))
    home = tmp_path / "home"
    home.mkdir()
    fake = FakeCloud(tmp_path / "fake")
    project, row = masked_project(tmp_path / "数据项目")
    iso = isolation_receipt("claude", FakeCloud(tmp_path / "fake-iso"), home, tmp_path / "iso", tmp_path / "iso.json")
    return {"tmp": tmp_path, "systmp": systmp, "home": home, "fake": fake, "project": project, "row": row,
            "iso": iso, "state": routes_state(tmp_path / "routes.json"), "receipts": tmp_path / "receipts",
            "src": project / "_masked" / row["path"]}


def _send(e, **kw):
    kw.setdefault("isolation_receipt", e["iso"])
    return S.send(e["src"], e["row"]["der_sha256"], route="claude", project=e["project"], receipt_dir=e["receipts"],
                  routes_state=e["state"], source_env=source_env(e["fake"], e["home"], e["systmp"]), **kw)


def test_stored_claude_receipt_with_other_settings_sha_exit61(env):
    """A claude receipt taken with another ``cloud_settings.json`` is no evidence for the pinned one -> 61."""
    rec = json.loads(env["iso"].read_text(encoding="utf-8"))
    rec["cloud_settings_sha"] = "0" * 64
    other = env["tmp"] / "other-settings.json"
    other.write_text(json.dumps(rec), encoding="utf-8")
    with pytest.raises(S.SendRefused) as info:
        _send(env, isolation_receipt=other)
    assert (info.value.exit_code, info.value.reason) == (61, "isolation_receipt_settings")
    assert env["fake"].model_calls() == []


def test_leftover_in_send_dir_moved_not_deleted(env):
    """Something the CLI left in its cwd is not deleted: the send dir moves to ``<receipts>/_send_leftovers/``."""
    env["fake"].set(write_extra="left.txt")
    res = _send(env)
    [call] = env["fake"].model_calls()
    moved = env["receipts"] / S.LEFTOVERS / Path(call["cwd"]).name
    assert res.returncode == 0
    assert (moved / "left.txt").read_text() == "left behind by the fake\n"
    assert sorted(p.name for p in moved.iterdir()) == ["left.txt"]
    assert list(env["systmp"].iterdir()) == []


def test_send_dir_gone_is_not_an_error(env):
    """The cwd vanished under the CLI: cleanup has nothing to do and the result is still recorded."""
    env["fake"].set(move_cwd=True)
    res = _send(env)
    assert res.returncode == 0 and res.receipt["status"] == "sent"
    assert list(env["systmp"].iterdir()) == []


@pytest.mark.parametrize("bad", ["", "ABC", "0" * 63, "g" * 64, None])
def test_malformed_expect_sha_rejected(env, bad):
    with pytest.raises(ValueError):
        S.send(env["src"], bad, route="claude", project=env["project"], receipt_dir=env["receipts"],
               isolation_receipt=env["iso"], routes_state=env["state"])
    assert env["fake"].calls() == []


def _cli(e, sha=None) -> list[str]:
    return [sys.executable, "-m", "klode_kit.ocr.send", "--route", "claude", "--project", str(e["project"]),
            "--receipt-dir", str(e["receipts"]), "--isolation-receipt", str(e["iso"]), "--routes-state",
            str(e["state"]), str(e["src"]), sha or e["row"]["der_sha256"]]


def _cli_env(e, extra=None) -> dict[str, str]:
    cloud = source_env(e["fake"], e["home"], e["systmp"])
    env = base_env({"PATH": cloud["PATH"], "HOME": cloud["HOME"], "TMPDIR": cloud["TMPDIR"], **(extra or {})})
    env.pop("KLODE_KIT_REAL", None)
    return env


def test_cli_configuration_error_exit40(env):
    (env["project"] / "klode-kit.toml").write_text("[claude]\nno_such_key = 1\n", encoding="utf-8")
    res = subprocess.run(_cli(env), env=_cli_env(env), capture_output=True, timeout=60)
    assert res.returncode == 40, res.stderr.decode()
    assert env["fake"].calls() == []


def test_cli_malformed_sha_exit2(env):
    res = subprocess.run(_cli(env, sha="not-a-sha"), env=_cli_env(env), capture_output=True, timeout=60)
    assert res.returncode == 2
    assert env["fake"].calls() == []


def test_copy_tampered_after_copy_exit60(env):
    """TOCTOU on the copy: overwrite ``<send dir>/<sha>.png`` while paused at ``send.after_copy`` -> the re-hash
    refuses (60), no model call, the send dir is removed."""
    pause = env["tmp"] / "pause"
    pause.mkdir()
    proc = subprocess.Popen(_cli(env), env=_cli_env(env, {"KLODE_KIT_TEST_SEAMS": "1",
                                                          "KLODE_KIT_PAUSE_AT": "send.after_copy",
                                                          "KLODE_KIT_PAUSE_DIR": str(pause)}),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not (pause / "send.after_copy.reached").exists():
            assert proc.poll() is None and time.monotonic() < deadline, proc.communicate()
            time.sleep(0.01)
        [send_dir] = list(env["systmp"].glob("klode-kit-send-*"))
        copy = send_dir / f"{env['row']['der_sha256']}.png"
        copy.write_bytes(copy.read_bytes() + b"\0")
        (pause / "send.after_copy.go").write_text("")
        _, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 60, err.decode()
    assert b"copy_mismatch" in err
    assert env["fake"].model_calls() == []
    assert list(env["systmp"].iterdir()) == []
