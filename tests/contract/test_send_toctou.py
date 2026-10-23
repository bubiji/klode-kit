"""WI-05b: swapping the source while a send is paused, and where the pause points really are (matrix 05b-08,
05b-16; section 0.3 v4-v8, K6).

The sender runs as ``python -m klode_kit.ocr.send`` with ``KLODE_KIT_TEST_SEAMS=1``. Pause parameters: for every
point of the fixed send list the child must write ``<point>.reached`` (else FAIL), the test then overwrites the
source IN PLACE (``r+b``, truncate, unregistered bytes; ``st_ino`` must not change) and releases it. Outcome per
point: exit 60 with no model call, or a model call that received registered bytes - never the new bytes.

Audit run (no ``PAUSE_AT``): ``sitecustomize`` records events of the sender process; asserted:
1. the observed ``send.*`` crash point sequence equals the fixed list;
2. at least one filesystem mutation inside the send directory (the directory itself counts, except the mkdir that
   creates it), and all of them after ``send.after_source_check``;
3. before ``send.after_source_check`` the source (by real path) is opened for reading exactly once, before
   ``send.after_source_read``, and no ``subprocess.Popen`` argv names the source.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _support.flows import audit_env, base_env
from _support.send_fake import FakeCloud, isolation_receipt, masked_project, routes_state, sha256, source_env
from klode_kit.devtools.audit import read_log

pytestmark = pytest.mark.contract_replay

POINTS = ("send.after_source_read", "send.after_source_check", "send.after_copy", "send.after_copy_check",
          "send.before_spawn")
REACH_TIMEOUT = 30
UNREGISTERED = b"\x89PNG\r\n\x1a\n" + b"unregistered replacement bytes " * 8


class Setup:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.systmp = root / "systmp"
        self.systmp.mkdir()
        self.home = root / "home"
        self.home.mkdir()
        self.fake = FakeCloud(root / "fake")
        self.project, self.row = masked_project(root / "project")
        self.src = self.project / "_masked" / self.row["path"]
        self.iso = isolation_receipt("claude", self.fake, self.home, root / "iso", root / "iso.json")
        self.state = routes_state(root / "routes.json")
        self.registered = {self.row["der_sha256"]}

    def argv(self) -> list[str]:
        return [sys.executable, "-m", "klode_kit.ocr.send", "--route", "claude", "--project", str(self.project),
                "--receipt-dir", str(self.root / "receipts"), "--isolation-receipt", str(self.iso),
                "--routes-state", str(self.state), str(self.src), self.row["der_sha256"]]

    def env(self, extra: dict[str, str]) -> dict[str, str]:
        cloud = source_env(self.fake, self.home, self.systmp)
        env = base_env({"PATH": cloud["PATH"], "HOME": cloud["HOME"], "TMPDIR": cloud["TMPDIR"], "LANG": "C.UTF-8",
                        "KLODE_KIT_TEST_SEAMS": "1", **extra})
        env.pop("KLODE_KIT_REAL", None)
        return env


def overwrite_in_place(path: Path, data: bytes) -> None:
    ino = os.stat(path).st_ino
    with open(path, "r+b") as fh:
        fh.truncate(0)
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    assert os.stat(path).st_ino == ino, "replacement changed the inode: not an in-place overwrite"


def run_paused(s: Setup, point: str, pause_dir: Path) -> tuple[int, bytes]:
    pause_dir.mkdir()
    proc = subprocess.Popen(s.argv(), env=s.env({"KLODE_KIT_PAUSE_AT": point, "KLODE_KIT_PAUSE_DIR": str(pause_dir)}),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        reached = pause_dir / f"{point}.reached"
        deadline = time.monotonic() + REACH_TIMEOUT
        while not reached.exists():
            if proc.poll() is not None or time.monotonic() > deadline:
                out, err = proc.communicate(timeout=5) if proc.poll() is not None else (b"", b"")
                pytest.fail(f"{point}.reached never appeared (exit {proc.poll()}): {err.decode()[-2000:]}")
            time.sleep(0.01)
        overwrite_in_place(s.src, UNREGISTERED)
        (pause_dir / f"{point}.go").write_text("")
        _, err = proc.communicate(timeout=60)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert not (pause_dir / f"{point}.go").exists(), "the .go file was not consumed"
    return proc.returncode, err


def outcome(s: Setup, code: int, err: bytes, before: int) -> str:
    calls = s.fake.model_calls()[before:]
    if code == 60:
        assert calls == [], "exit 60 but a model call happened"
        return "refused"
    assert code == 0, err.decode()[-2000:]
    assert len(calls) == 1
    got = set(calls[0]["pngs"].values())
    assert got <= s.registered, f"unregistered bytes reached the model: {got}"
    assert sha256(UNREGISTERED) not in got
    return "sent-registered"


def test_toctou_swap_after_check_deterministic(tmp_path):
    """05b-08 (S3): pause after the source check, overwrite the source in place, release; 20 runs, one outcome,
    and that outcome is safe."""
    seen = set()
    for i in range(20):
        s = Setup(tmp_path / f"r{i}")
        before = len(s.fake.model_calls())
        code, err = run_paused(s, "send.after_source_check", s.root / "pause")
        seen.add(outcome(s, code, err, before))
    assert len(seen) == 1, seen


@pytest.mark.parametrize("point", POINTS)
def test_toctou_swap_at_every_send_point(tmp_path, point):
    """05b-16 (S4), pause parameters: at every fixed send point the in-place swap never reaches the model."""
    s = Setup(tmp_path / "s")
    code, err = run_paused(s, point, s.root / "pause")
    outcome(s, code, err, 0)
    assert list(s.systmp.iterdir()) == []


def _inside(path: str | None, root: str) -> bool:
    return isinstance(path, str) and (path == root or path.startswith(root + os.sep))


def test_send_audit_positions(tmp_path):
    """05b-16 (S4), audit run: crash point order, send-dir mutations only after the source check (and at least
    one), one read-open of the source before the check and no subprocess naming it."""
    s = Setup(tmp_path / "s")
    log = tmp_path / "audit.jsonl"
    env = audit_env(log, {k: v for k, v in s.env({}).items() if k not in ("PYTHONPATH",)})
    res = subprocess.run(s.argv(), env=env, capture_output=True, timeout=120)
    assert res.returncode == 0, res.stderr.decode()[-2000:]
    [call] = s.fake.model_calls()
    send_dir = os.path.realpath(call["cwd"])
    source = os.path.realpath(s.src)

    events = [e for e in read_log(log)]
    points = [e["name"] for e in events if e.get("ev") == "klode_kit.crashpoint" and e["name"].startswith("send.")]
    assert tuple(points) == POINTS  # (1)

    idx = {name: i for i, e in enumerate(events) if e.get("ev") == "klode_kit.crashpoint"
           for name in [e["name"]] if name in POINTS}
    checked = idx["send.after_source_check"]
    muts = [i for i, e in enumerate(events)
            if e.get("mut") is True and _inside(e.get("path"), send_dir)
            and not (e.get("ev") == "os.mkdir" and e.get("path") == send_dir)]
    assert muts, "no filesystem mutation inside the send dir was observed"  # (2)
    assert all(i > checked for i in muts), [events[i] for i in muts if i < checked]

    opens = [i for i, e in enumerate(events[:checked])
             if e.get("ev") == "open" and e.get("mut") is False and e.get("path") == source]
    assert len(opens) == 1, opens  # (3)
    assert opens[0] < idx["send.after_source_read"]
    for e in events[:checked]:
        if e.get("ev") == "subprocess.Popen":
            assert not any(os.path.realpath(a) == source or source in a for a in e["argv"]), e
