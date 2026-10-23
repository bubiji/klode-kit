"""WI-05b: sender contract (matrix 05b-01..07, 05b-09, 05b-10, 05b-14, 05b-15; K6; RR-7; effort lock).

Public surface under test (``klode_kit.ocr.send``):

- ``send(path, expect_sha, *, route, project, receipt_dir, isolation_receipt, routes_state=None, config=None,
  source_env=None, timeout_s=None, now=None) -> SendResult`` (``returncode``, ``timed_out``, ``stdout``,
  ``receipt``, ``receipt_path``);
- refusals raise ``SendRefused`` with ``exit_code`` 60 (privacy: the bytes now at ``path`` do not hash to
  ``expect_sha``, their sha is not registered - ``mask.read_manifest(project)`` rows or the synthetic fixtures
  MANIFEST - or ``path`` lies in a ``_mask_review`` directory) or 61 (isolation: route disabled in
  ``routes_state``, stored isolation receipt not a valid PASS for this route and the current CLI version, or the
  layer (a) review of the exact invocation fails - including a data project under ``/tmp`` (RR-7) and an effort
  other than the configured one);
- ``python -m klode_kit.ocr.send --route R --project P --receipt-dir D --isolation-receipt F
  [--routes-state S] PATH SHA`` exits 0 / 1 (child failed) / 60 / 61 / 130 (interrupted).

Boundary: the cloud CLIs are ``_support.send_fake.FakeCloud`` (records argv, env as received, cwd state, image
sha). "0 calls" for exit 60 means the fake log is empty (not even ``--version``); for exit 61 it means no model
call (``--version`` probes of the review are allowed: an expired CLI version can only be seen by asking).
Paired real path: model lane 06-11/12, 00c2-06/07.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from _support.flows import base_env
from _support.send_fake import (
    FIXTURE_PNG,
    FakeCloud,
    invocation_of,
    isolation_receipt,
    masked_project,
    rebuild_cwd,
    routes_state,
    sha256,
    source_env,
)
from klode_kit.config import CLOUD_ENV_ALLOWLIST
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import send as S

pytestmark = pytest.mark.contract_replay

ROUTES = ("claude", "codex")


@pytest.fixture
def systmp(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def fake(tmp_path) -> FakeCloud:
    return FakeCloud(tmp_path / "fake")


@pytest.fixture
def project(tmp_path):
    return masked_project(tmp_path / "数据项目 甲")


class Rig:
    def __init__(self, tmp_path: Path, fake: FakeCloud, home: Path, systmp: Path, project: Path):
        self.tmp, self.fake, self.home, self.systmp, self.project = tmp_path, fake, home, systmp, project
        self.receipts = tmp_path / "receipts"
        self.state = routes_state(tmp_path / "routes.json")
        self.env = source_env(fake, home, systmp)
        self._iso: dict[str, Path] = {}

    def iso(self, route: str) -> Path:
        """Stored PASS receipt, made through a separate fake (same versions) so its --version probe never shows
        up in the call log of the fake under test."""
        if route not in self._iso:
            other = FakeCloud(self.tmp / "fake-iso", versions=self.fake.state["versions"])
            self._iso[route] = isolation_receipt(route, other, self.home, self.tmp / "iso",
                                                 self.tmp / f"iso-{route}.json")
        return self._iso[route]

    def send(self, path: Path, sha: str, route: str = "claude", **kw):
        kw.setdefault("project", self.project)
        kw.setdefault("receipt_dir", self.receipts)
        if "isolation_receipt" not in kw:
            kw["isolation_receipt"] = self.iso(route)
        kw.setdefault("routes_state", self.state)
        kw.setdefault("source_env", self.env)
        return S.send(path, sha, route=route, **kw)

    def refused(self, path: Path, sha: str, route: str = "claude", **kw) -> int:
        with pytest.raises(S.SendRefused) as info:
            self.send(path, sha, route, **kw)
        return info.value.exit_code


@pytest.fixture
def rig(tmp_path, fake, home, systmp, project) -> Rig:
    return Rig(tmp_path, fake, home, systmp, project[0])


def masked_path(project: Path, row: dict) -> Path:
    return project / "_masked" / row["path"]


def receipts_in(d: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))] if d.is_dir() else []


def path_parts(p: Path, base: Path) -> list[str]:
    """Components of ``p`` below ``base`` (pytest's own tmp prefix such as ``T`` is not the data's path)."""
    return list(Path(os.path.realpath(p)).relative_to(os.path.realpath(base)).parts)


# --------------------------------------------------------------------------- 05b-01 / 05b-05


@pytest.mark.parametrize("route", ROUTES)
def test_listed_sha_sends(rig, project, route):
    """05b-01 (S1): a registered masked image is sent once; the CLI got exactly those bytes; the send dir is
    gone afterwards; the receipt carries the der sha and no path."""
    proj, row = project
    src = masked_path(proj, row)

    res = rig.send(src, row["der_sha256"], route)

    calls = rig.fake.model_calls()
    assert len(calls) == 1
    assert list(calls[0]["pngs"].values()) == [row["der_sha256"]]
    assert calls[0]["tool"] == route
    assert not Path(calls[0]["cwd"]).exists()
    assert res.returncode == 0 and res.timed_out is False
    assert res.stdout == rig.fake.state["stdout"]
    assert res.receipt["der_sha256"] == row["der_sha256"]
    assert res.receipt["status"] == "sent" and res.receipt["route"] == route
    text = res.receipt_path.read_text(encoding="utf-8")
    assert json.loads(text) == res.receipt
    for part in path_parts(src, rig.tmp) + path_parts(Path(calls[0]["cwd"]), rig.tmp):
        assert part not in text, part


def test_fixture_manifest_sha_accepted(rig, tmp_path):
    """05b-05 (S2): an image registered in tests/fixtures/MANIFEST.json is accepted with an empty data project."""
    empty = tmp_path / "empty-project"
    empty.mkdir()
    data = FIXTURE_PNG.read_bytes()

    res = rig.send(FIXTURE_PNG, sha256(data), project=empty)

    assert res.returncode == 0
    assert [list(c["pngs"].values()) for c in rig.fake.model_calls()] == [[sha256(data)]]


# --------------------------------------------------------------------------- 05b-02 / 03 / 04 / 14


def test_unlisted_sha_exit60_no_spawn(rig, tmp_path):
    """05b-02 (S3): an unregistered png -> 60, and the fake was never started (not even --version)."""
    stray = tmp_path / "project" / "_inbox" / "stray.png"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"\x89PNG\r\n\x1a\n not registered")

    assert rig.refused(stray, sha256(stray.read_bytes())) == 60
    assert rig.fake.calls() == []
    assert list(rig.systmp.iterdir()) == []


def test_listed_path_tampered_bytes(rig, project):
    """05b-03 (S3): the path is the registered one but one bit changed -> 60, 0 calls (sha decides, not path)."""
    proj, row = project
    src = masked_path(proj, row)
    data = bytearray(src.read_bytes())
    data[len(data) // 2] ^= 0x01
    src.write_bytes(bytes(data))

    assert rig.refused(src, row["der_sha256"]) == 60
    assert rig.fake.calls() == []


def test_expect_sha_mismatch_refused(rig, project, tmp_path):
    """Guard: registered bytes sent under another expected sha (the caller meant a different image) -> 60."""
    proj, row = project
    src = masked_path(proj, row)
    assert rig.refused(src, sha256(FIXTURE_PNG.read_bytes())) == 60
    assert rig.fake.calls() == []


@pytest.mark.parametrize("where", ["project", "elsewhere"])
def test_review_dir_rejected(rig, project, tmp_path, where):
    """05b-04 (S3): a copy inside any ``_mask_review/`` directory whose sha IS registered -> 60, 0 calls."""
    proj, row = project
    base = proj if where == "project" else tmp_path / "other"
    copy = base / "_mask_review" / "x" / "copy.png"
    copy.parent.mkdir(parents=True, exist_ok=True)
    copy.write_bytes(masked_path(proj, row).read_bytes())

    assert rig.refused(copy, row["der_sha256"]) == 60
    assert rig.fake.calls() == []


def test_review_dir_symlink_rejected(rig, project, tmp_path):
    """Boundary: a path outside ``_mask_review`` that is a symlink into it is judged by its real path -> 60."""
    proj, row = project
    review_png = next((proj / "_mask_review").rglob("*.png"))
    link = tmp_path / "innocent.png"
    link.symlink_to(review_png)
    assert rig.refused(link, sha256(review_png.read_bytes())) == 60
    assert rig.fake.calls() == []


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_unreadable_source_refused(rig, tmp_path, kind):
    """Guard: a path that cannot be read as a file -> 60 (nothing to verify), 0 calls."""
    target = tmp_path / "nothing.png"
    if kind == "directory":
        target.mkdir()
    assert rig.refused(target, "0" * 64) == 60
    assert rig.fake.calls() == []


def test_corrupt_mask_manifest_refused(rig, project):
    """Guard: a MANIFEST that ``mask.read_manifest`` rejects is no whitelist at all -> 60 (fail closed)."""
    proj, row = project
    manifest = proj / "_masked" / "MANIFEST.jsonl"
    manifest.write_text(manifest.read_text(encoding="utf-8") + '{"der_sha256": "nope"}\n', encoding="utf-8")
    assert rig.refused(masked_path(proj, row), row["der_sha256"]) == 60
    assert rig.fake.calls() == []


def test_hook_absent_sender_still_enforces(rig, tmp_path):
    """05b-14 (S3): no hooks.json in play at all (plain library call) -> the sender alone refuses an unregistered
    image with 60 and starts nothing."""
    stray = tmp_path / "loose.png"
    stray.write_bytes(FIXTURE_PNG.read_bytes() + b"\0")
    assert rig.refused(stray, sha256(stray.read_bytes()), route="codex") == 60
    assert rig.fake.calls() == []


def test_refusal_writes_pathless_receipt(rig, tmp_path):
    """A refusal leaves a ``refused`` receipt with the exit code and reason, and still no path."""
    stray = tmp_path / "项目 甲" / "stray copy.png"
    stray.parent.mkdir()
    stray.write_bytes(b"not registered either")
    rig.refused(stray, sha256(stray.read_bytes()))
    [rec] = receipts_in(rig.receipts)
    assert rec["status"] == "refused" and rec["exit_code"] == 60 and rec["reason"] == "not_registered"
    text = json.dumps(rec, ensure_ascii=False)
    for part in path_parts(stray, tmp_path):
        assert part not in text


# --------------------------------------------------------------------------- 05b-06 and route state


@pytest.mark.parametrize("route", ROUTES)
def test_invalid_isolation_receipt_exit61(rig, project, tmp_path, route):
    """05b-06 (S3): the stored receipt was taken on an older CLI than the one now on PATH -> 61, no model call."""
    proj, row = project
    old = isolation_receipt(route, rig.fake, rig.home, tmp_path / "iso-old", tmp_path / "old.json",
                            cli_version="2.1.0" if route == "claude" else "0.1.0")

    code = rig.refused(masked_path(proj, row), row["der_sha256"], route, isolation_receipt=old)

    assert code == 61
    assert rig.fake.model_calls() == []
    assert [p for p in rig.systmp.iterdir()] == []


@pytest.mark.parametrize("change", ["fail", "wrong_route", "missing", "not_json", "bad_schema", "check_false"])
def test_isolation_receipt_defects_exit61(rig, project, tmp_path, change):
    """Guard: anything but a schema-valid PASS receipt of this route -> 61, no model call."""
    proj, row = project
    path = tmp_path / f"bad-{change}.json"
    if change == "missing":
        pass
    elif change == "not_json":
        path.write_text("{", encoding="utf-8")
    else:
        good = json.loads(rig.iso("claude").read_text(encoding="utf-8"))
        if change == "fail":
            good["cloud_isolation"] = "FAIL"
        elif change == "wrong_route":
            good = json.loads(rig.iso("codex").read_text(encoding="utf-8"))
        elif change == "bad_schema":
            good["extra"] = 1
        elif change == "check_false":
            good["checks"][0]["ok"] = False
        path.write_text(json.dumps(good), encoding="utf-8")

    assert rig.refused(masked_path(proj, row), row["der_sha256"], "claude", isolation_receipt=path) == 61
    assert rig.fake.model_calls() == []


@pytest.mark.parametrize("state", ["disabled", "missing", "garbage"])
def test_route_disabled_zero_calls(rig, project, tmp_path, state):
    """routes_enabled says no (or cannot be read) -> 61 and nothing is started, not even --version."""
    proj, row = project
    path = tmp_path / "routes-x.json"
    if state == "disabled":
        routes_state(path, claude=False)
    elif state == "garbage":
        path.write_text("[]", encoding="utf-8")
    assert rig.refused(masked_path(proj, row), row["der_sha256"], "claude", routes_state=path) == 61
    assert rig.fake.calls() == []


def test_unknown_route_rejected(rig, project):
    proj, row = project
    with pytest.raises(ValueError):
        rig.send(masked_path(proj, row), row["der_sha256"], "vision")
    assert rig.fake.calls() == []


# --------------------------------------------------------------------------- RR-7 and the effort lock


def test_project_under_shared_tmp_exit61(rig, tmp_path):
    """RR-7: codex ``:minimal`` can read /tmp, so a data project whose real path is under /tmp (here through a
    symlink, so nothing is created in /tmp) is refused with 61 before any model call."""
    link = tmp_path / "tmp-link"
    link.symlink_to("/tmp")
    proj = link / f"klode-kit-05b-absent-{os.getpid()}"
    data = FIXTURE_PNG.read_bytes()

    for route in ROUTES:
        assert rig.refused(FIXTURE_PNG, sha256(data), route, project=proj) == 61
    assert rig.fake.model_calls() == []


def test_configured_effort_used_and_required(rig, project):
    """The effort in the argv is the project's configured one (``klode-kit.toml``) and the send succeeds with
    it; the review requires exactly that value (unit cases: tests/unit/test_isolation_05b.py)."""
    proj, row = project
    (proj / "klode-kit.toml").write_text('[codex]\neffort = "high"\n[claude]\neffort = "xhigh"\n', encoding="utf-8")

    rig.send(masked_path(proj, row), row["der_sha256"], "codex")
    rig.send(masked_path(proj, row), row["der_sha256"], "claude")

    codex_call, claude_call = rig.fake.model_calls()
    assert "model_reasoning_effort=high" in codex_call["argv"]
    i = claude_call["argv"].index("--effort")
    assert claude_call["argv"][i + 1] == "xhigh"


# --------------------------------------------------------------------------- 05b-09 / 05b-10


@pytest.mark.parametrize("route", ROUTES)
def test_tmpdir_properties(rig, project, route):
    """05b-09 (S2): during the call the cwd is a 0700 dir of ours directly in the system TMPDIR, outside the
    project, holding exactly <sha>.png, prompt.txt and schema.json."""
    proj, row = project
    rig.send(masked_path(proj, row), row["der_sha256"], route)

    [call] = rig.fake.model_calls()
    cwd = Path(call["cwd"])
    assert cwd.parent == rig.systmp.resolve()
    assert not cwd.is_relative_to(proj.resolve())
    assert call["cwd_mode"] == 0o700 and call["cwd_uid"] == os.getuid()
    assert call["cwd_files"] == sorted([f"{row['der_sha256']}.png", "prompt.txt", "schema.json"])


def test_tmpdir_cleanup_on_child_failure(rig, project):
    """05b-10 (S3), case 1: the CLI exits non-zero -> the result says so, the send dir is gone."""
    proj, row = project
    rig.fake.set(exit_code=3, stdout="")

    res = rig.send(masked_path(proj, row), row["der_sha256"])

    [call] = rig.fake.model_calls()
    assert res.returncode == 3 and res.receipt["exit_code"] == 3 and res.receipt["status"] == "sent"
    assert not Path(call["cwd"]).exists()
    assert list(rig.systmp.iterdir()) == []


def test_timeout_kills_and_cleans(rig, project):
    """Failure path: a CLI that outlives ``timeout_s`` is killed; timed_out recorded; send dir gone."""
    proj, row = project
    rig.fake.set(sleep_s=30)
    start = time.monotonic()

    res = rig.send(masked_path(proj, row), row["der_sha256"], timeout_s=1)

    assert time.monotonic() - start < 20
    assert res.timed_out is True and res.returncode is None and res.receipt["timed_out"] is True
    assert list(rig.systmp.iterdir()) == []


def _cli(rig: Rig, src: Path, sha: str, route: str = "claude", extra_env=None) -> list[str]:
    return [sys.executable, "-m", "klode_kit.ocr.send", "--route", route, "--project", str(rig.project),
            "--receipt-dir", str(rig.receipts), "--isolation-receipt", str(rig.iso(route)),
            "--routes-state", str(rig.state), str(src), sha]


def _cli_env(rig: Rig) -> dict[str, str]:
    env = base_env({"PATH": rig.env["PATH"], "HOME": rig.env["HOME"], "TMPDIR": rig.env["TMPDIR"],
                    "LANG": "C.UTF-8"})
    env.pop("KLODE_KIT_REAL", None)
    return env


def test_tmpdir_cleanup_on_keyboard_interrupt(rig, project):
    """05b-10 (S3), case 2: SIGINT while the CLI runs -> the sender stops (exit 130), the child is gone and the
    send dir does not exist."""
    proj, row = project
    rig.fake.set(sleep_s=60, started=True)
    proc = subprocess.Popen(_cli(rig, masked_path(proj, row), row["der_sha256"]), env=_cli_env(rig),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while not rig.fake.started():
            assert time.monotonic() < deadline, "fake never started"
            assert proc.poll() is None, proc.communicate()
            time.sleep(0.05)
        proc.send_signal(signal.SIGINT)
        _, err = proc.communicate(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
    [call] = rig.fake.model_calls()
    assert proc.returncode == 130, err.decode()
    assert not Path(call["cwd"]).exists()
    assert list(rig.systmp.iterdir()) == []
    with pytest.raises(ProcessLookupError):
        os.kill(call["pid"], 0)


@pytest.mark.parametrize("exit_code", [0, 4])
def test_cli_exit_codes(rig, project, exit_code):
    """CLI: 0 when the CLI succeeded, 1 when it failed; 60 for an unregistered image; stdout is the CLI's."""
    proj, row = project
    rig.fake.set(exit_code=exit_code)
    res = subprocess.run(_cli(rig, masked_path(proj, row), row["der_sha256"]), env=_cli_env(rig),
                         capture_output=True, timeout=60)
    assert res.returncode == (0 if exit_code == 0 else 1), res.stderr.decode()
    assert res.stdout.decode() == rig.fake.state["stdout"]

    stray = rig.tmp / "stray.png"
    stray.write_bytes(b"x")
    res = subprocess.run(_cli(rig, stray, sha256(b"x")), env=_cli_env(rig), capture_output=True, timeout=60)
    assert res.returncode == 60
    assert b"not_registered" in res.stderr


def test_cli_usage_error(rig):
    res = subprocess.run([sys.executable, "-m", "klode_kit.ocr.send", "--route", "claude"], env=_cli_env(rig),
                         capture_output=True, timeout=60)
    assert res.returncode == 2


def test_launcher_registers_send(rig, project):
    """``klode-kit ocr send`` is the same entry point."""
    proj, row = project
    launcher = Path(__file__).resolve().parents[2] / "bin" / "klode-kit"
    res = subprocess.run([str(launcher), "ocr", "send", *_cli(rig, masked_path(proj, row), row["der_sha256"])[3:]],
                         env=_cli_env(rig), capture_output=True, timeout=60)
    assert res.returncode == 0, res.stderr.decode()
    assert len(rig.fake.model_calls()) == 1


# --------------------------------------------------------------------------- guard log into the receipt dir


def test_guard_log_moved_into_receipt_dir(rig, project):
    """WI-00c1 deviation 6: the isolation guard's ``<send dir>.guard.jsonl`` ends up in the receipt directory
    (named after the receipt, no path in the name) and nothing is left next to the send dir."""
    proj, row = project
    rig.fake.set(guard=True)

    res = rig.send(masked_path(proj, row), row["der_sha256"])

    [call] = rig.fake.model_calls()
    assert not Path(call["cwd"] + ".guard.jsonl").exists()
    assert list(rig.systmp.iterdir()) == []
    guard = res.receipt["guard_log"]
    assert guard["lines"] == 1 and guard["deny"] == 0
    moved = rig.receipts / guard["file"]
    lines = [json.loads(x) for x in moved.read_text(encoding="utf-8").splitlines()]
    assert [x["decision"] for x in lines] == ["allow"]
    assert row["der_sha256"] in guard["file"] and os.sep not in guard["file"]


def test_no_guard_log_recorded_as_none(rig, project):
    proj, row = project
    res = rig.send(masked_path(proj, row), row["der_sha256"], "codex")
    assert res.receipt["guard_log"] is None


def test_receipts_do_not_overwrite(rig, project):
    """State: sending the same image twice keeps both receipts (one file per send)."""
    proj, row = project
    a = rig.send(masked_path(proj, row), row["der_sha256"])
    b = rig.send(masked_path(proj, row), row["der_sha256"])
    assert a.receipt_path != b.receipt_path
    assert len(receipts_in(rig.receipts)) == 2


# --------------------------------------------------------------------------- 05b-15


@pytest.mark.parametrize("route", ROUTES)
def test_executed_env_cwd_argv_equal_checked(rig, project, monkeypatch, route):
    """05b-15 (S3): the process that ran saw only allowlisted env keys (even with ANTHROPIC_BASE_URL,
    CLAUDE_CONFIG_DIR and FOO in the sender's own environment), its argv sha is the reviewed one, and the
    recorded (argv, env, cwd) triple passes ``isolation_check`` again.

    The allowlist is ``config.CLOUD_ENV_ALLOWLIST`` (r5 keys plus ``USER``, user decision 2026-10-23, WI-00c1b)."""
    proj, row = project
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://example.invalid")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(rig.tmp / "cfg"))
    monkeypatch.setenv("FOO", "bar")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-synthetic")
    monkeypatch.setenv("PATH", rig.env["PATH"])
    monkeypatch.setenv("HOME", rig.env["HOME"])
    monkeypatch.setenv("TMPDIR", rig.env["TMPDIR"])

    res = rig.send(masked_path(proj, row), row["der_sha256"], route, source_env=None)

    [call] = rig.fake.model_calls()
    assert set(call["env"]) <= CLOUD_ENV_ALLOWLIST
    assert {"ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "FOO"}.isdisjoint(call["env"])
    assert IC.argv_sha((route, *call["argv"])) == res.receipt["argv_sha"]
    cwd = rebuild_cwd(call)
    try:
        again = IC.check(invocation_of(call), project=proj, expected_effort="max", refuse_shared_tmp=True)
        assert again.ok, again.failed
        assert again.argv_sha == res.receipt["argv_sha"]
    finally:
        shutil.move(str(cwd), str(rig.tmp / "rebuilt"))


def test_review_recorded_in_receipt(rig, project):
    """The receipt lists every review check by name with its result (no details: they carry paths)."""
    proj, row = project
    res = rig.send(masked_path(proj, row), row["der_sha256"])
    names = {c["name"] for c in res.receipt["review"]}
    assert {"effort:configured", "project:outside_shared_tmp", "tmpdir:png_approved"} <= names
    assert all(set(c) == {"name", "ok"} and c["ok"] is True for c in res.receipt["review"])
