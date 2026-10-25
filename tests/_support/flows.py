"""Helpers for running project processes under the audit hook (matrix section 0.3, 00s-14/15/16).

Only public entry points are used: ``python -m <module>`` subprocesses, plus the
``klode_kit.devtools.audit`` analysis functions applied to the recorded log.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
AUDIT_SITE = SRC / "klode_kit" / "devtools" / "audit_site"
SEAM_VARS = ("KLODE_KIT_CRASH_AT", "KLODE_KIT_PAUSE_AT", "KLODE_KIT_PAUSE_DIR")


def base_env(extra: Mapping[str, str] | None = None, drop: tuple[str, ...] = ()) -> dict[str, str]:
    """Copy of the current environment with seam/audit variables cleared, then ``extra`` applied."""
    env = dict(os.environ)
    for key in (*SEAM_VARS, "KLODE_KIT_AUDIT_ROLE", "KLODE_KIT_AUDIT_LOG", *drop):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    paths = [str(SRC)]
    if env.get("PYTHONPATH"):
        paths.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(paths)
    if extra:
        env.update(extra)
    return env


def audit_env(log: Path, extra: Mapping[str, str] | None = None, drop: tuple[str, ...] = ()) -> dict[str, str]:
    """Environment that makes ``sitecustomize`` install the audit hook in project processes."""
    env = base_env(extra, drop)
    env["KLODE_KIT_AUDIT_ROLE"] = "1"
    env["KLODE_KIT_AUDIT_LOG"] = str(log)
    env["PYTHONPATH"] = os.pathsep.join([str(AUDIT_SITE), env["PYTHONPATH"]])
    for key in drop:  # drop must also win over the audit keys set above (e.g. a missing ROLE)
        env.pop(key, None)
    return env


def run_module(
    module: str,
    args: list[str],
    env: Mapping[str, str],
    *,
    timeout: float = 60,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        env=dict(env),
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        timeout=timeout,
    )


def run_synthflow(
    scenario: str,
    root: Path,
    env: Mapping[str, str],
    *extra_args: str,
    timeout: float = 60,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    return run_module(
        "klode_kit.devtools.synthflow",
        [scenario, "--root", str(root), *extra_args],
        env,
        timeout=timeout,
        cwd=cwd,
    )


@dataclass(frozen=True)
class RealFlow:
    """One 00s-16 parameter: a real project flow paired 1:1 with an enumeration list.

    ``runner(tmp_path, env) -> list[Path]`` runs the flow to completion in a subprocess
    using ``env`` (the audit environment) and returns the durable roots for that flow
    (data project root, ``work/``, ``builds/``, receipts dir). ``expected_crashpoints``
    is the enumeration list of the paired case. Both are supplied by the WI that
    implements the flow; until then the parameter is a strict xfail.
    """

    name: str
    paired_case: str
    depends_on: str
    runner: Callable[[Path, Mapping[str, str]], list[Path]] | None = None
    expected_crashpoints: frozenset[str] | None = None


# --- receipts (WI-00b1) ------------------------------------------------------------------
# Enumeration list of 00b1-11 (tests/unit/test_receipts_write.py crashes at each point).
RECEIPT_CRASHPOINTS = frozenset({"receipts.mkdir", "receipts.before_replace"})
RECEIPT_EXAMPLE = REPO / "tests" / "contract" / "schema_examples" / "receipt" / "valid" / "step_done.json"


def _receipts_runner(tmp_path: Path, env: Mapping[str, str]) -> list[Path]:
    """Normal ``python -m klode_kit.receipts write`` into a data project whose receipts
    directory does not exist yet (so every durable write kind of the flow happens)."""
    root = tmp_path / "receipts-flow"
    root.mkdir()
    target = root / "work" / "b1" / "receipts" / "ocr.json"
    res = subprocess.run(
        [sys.executable, "-m", "klode_kit.receipts", "write", "--schema", "receipt", str(target)],
        input=RECEIPT_EXAMPLE.read_bytes(),
        env=dict(env),
        capture_output=True,
        timeout=60,
    )
    assert res.returncode == 0, res.stderr.decode()
    assert target.is_file()
    return [root]


REAL_FLOWS: dict[str, RealFlow] = {
    "writeback": RealFlow("writeback", "08c-13", "WI-08c"),
    "publish": RealFlow("publish", "09d-02", "WI-09d"),
    "receipts": RealFlow("receipts", "00b1-11", "WI-00b1", _receipts_runner, RECEIPT_CRASHPOINTS),
    "run_batch": RealFlow("run_batch", "04b-13", "WI-04b"),
}


@dataclass(frozen=True)
class RealEntry:
    """A real project entry point used by 00s-14 / 00s-15 (send, writeback).

    ``runner(tmp_path, env, timeout) -> (CompletedProcess, artifacts)`` prepares its own
    synthetic inputs under ``tmp_path``, runs the entry point as a subprocess with ``env``
    and returns the process result plus ``{relative path: bytes}`` of everything it
    produced. ``crash_point`` is one point from the paired enumeration list (08c-13 for
    writeback). Supplied by the WI that implements the entry; until then the cases are
    strict xfails.
    """

    name: str
    depends_on: str
    runner: Callable[[Path, Mapping[str, str], float], tuple[subprocess.CompletedProcess, dict[str, bytes]]] | None = None
    crash_point: str | None = None


REAL_ENTRIES: dict[str, RealEntry] = {
    "send": RealEntry("send", "WI-05b"),
    "writeback": RealEntry("writeback", "WI-08c"),
}


# --- run_batch (WI-04b) ------------------------------------------------------------------
# Enumeration list of 04b-13 (tests/contract/test_run_batch.py crashes at each point, then resumes).
RUN_BATCH_CRASHPOINTS = frozenset({
    "run_batch.mkdir",            # after each directory run_batch creates (work/, the batch dir)
    "run_batch.lock",             # after the project lock file is opened and locked
    "run_batch.meta_tmp",         # manifest / derived / batch receipt: temporary file written
    "run_batch.meta_replaced",    # ... and moved into place
    "ids.mkdir",                  # attempt allocation: ocr/, ocr/<engine>/
    "ids.attempt_mkdir",          # attempt directory claimed
    "run_batch.before_replace",   # page file: temporary written, not yet in place (matrix 04b-13)
    "run_batch.page_done",        # page file moved into place
    "receipts.mkdir",             # receipts/ created by the step receipt write
    "receipts.before_replace",    # attempt / step receipt temporary written
    "run_batch.receipt_written",  # after an attempt / step receipt is in place
})


def _run_batch_runner(tmp_path: Path, env: Mapping[str, str]) -> list[Path]:
    """Normal ``python -m klode_kit kb-ocr`` of fx-labs-1x on all four replayed routes into a fresh project."""
    fixture = REPO / "tests" / "fixtures" / "fx-labs-1x"
    root = tmp_path / "run-batch-flow"
    root.mkdir()
    flow_env = dict(env)
    flow_env.update({
        "KLODE_KIT_ENGINE_REPLAY": str(fixture / "engine"),
        "KLODE_KIT_FAKE_LOG": str(tmp_path / "run-batch-calls.jsonl"),
        "KLODE_KIT_TEST_SEAMS": "1",
    })
    flow_env.pop("KLODE_KIT_REAL", None)
    res = run_module("klode_kit", ["kb-ocr", "--batch", str(fixture / "images"), "--project", str(root)],
                     flow_env, timeout=120)
    assert res.returncode == 0, res.stderr.decode()
    assert len(list((root / "work").glob("*/ocr/*/*/p001.jsonl"))) == 4
    return [root]


REAL_FLOWS["run_batch"] = RealFlow("run_batch", "04b-13", "WI-04b", _run_batch_runner, RUN_BATCH_CRASHPOINTS)


# --- send (WI-05b) ------------------------------------------------------------------------
# 00s-14 / 00s-15 on the real sender: ``python -m klode_kit.ocr.send`` of a masked fx-rx-stamp page to a fake
# claude (tests/_support/send_fake.py). Artifacts: the CLI stdout, what the fake received (image sha per model
# call) and the receipt without its time fields and without the sha of the stored isolation receipt (each runner
# call prepares its own, with its own timestamp), so two runs compare equal.
def _send_runner(tmp_path: Path, env: Mapping[str, str], timeout: float):
    import json
    import tempfile

    from _support.send_fake import FakeCloud, isolation_receipt, masked_project, routes_state, source_env

    tmp_path.mkdir(parents=True, exist_ok=True)
    systmp = tmp_path / "systmp"
    systmp.mkdir(exist_ok=True)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    fake = FakeCloud(tmp_path / "fake", mirror_log=env.get("KLODE_KIT_FAKE_LOG"))
    saved = tempfile.tempdir
    try:
        tempfile.tempdir = str(systmp)
        project, row = masked_project(tmp_path / "project")
        other = FakeCloud(tmp_path / "fake-iso")
        iso = isolation_receipt("claude", other, home, tmp_path / "iso", tmp_path / "iso.json")
    finally:
        tempfile.tempdir = saved
    state = routes_state(tmp_path / "routes.json")
    receipts = tmp_path / "receipts"
    cloud = source_env(fake, home, systmp)
    run_env = dict(env)
    run_env.update({"PATH": cloud["PATH"], "HOME": cloud["HOME"], "TMPDIR": cloud["TMPDIR"]})
    res = run_module("klode_kit.ocr.send", ["--route", "claude", "--project", str(project), "--receipt-dir",
                                            str(receipts), "--isolation-receipt", str(iso), "--routes-state",
                                            str(state), str(project / "_masked" / row["path"]), row["der_sha256"]],
                     run_env, timeout=timeout)
    artifacts: dict[str, bytes] = {"stdout": res.stdout,
                                   "received": json.dumps([sorted(c["pngs"].values()) for c in fake.model_calls()])
                                   .encode()}
    for p in sorted(receipts.glob("*.json")) if receipts.is_dir() else []:
        rec = {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items()
               if k not in ("started", "ended", "duration_s", "isolation_receipt_sha")}
        artifacts[f"receipts/{p.name}"] = json.dumps(rec, sort_keys=True).encode()
    return res, artifacts


REAL_ENTRIES["send"] = RealEntry("send", "WI-05b", _send_runner)


# --- writeback (WI-08c) -------------------------------------------------------------------
# Enumeration list of 08c-13 (tests/contract/test_writeback_crash.py crashes at each point, then reruns). Every
# commit of one record is: begin marker -> decisions -> body -> front matter -> end marker; a rerun completes a
# commit whose begin marker has no end marker (roll forward), so a crash anywhere leaves all or nothing.
WRITEBACK_CRASHPOINTS = frozenset({
    "writeback.lock",            # the project lock work/.lock opened (shared with kb-ocr / kb-audit) and taken
    "writeback.begin",           # commit marker 'begin' (the whole planned commit) appended to a<N>/commits.jsonl
    "writeback.fixes_appended",  # the commit's decisions appended to a<N>/fixes.jsonl
    "writeback.mkdir",           # records/, records/_rev/, records/_by_id/ created (loop: one name)
    "writeback.rev_tmp",         # revision body written to its temporary file in records/_rev/
    "writeback.rev_renamed",     # ... moved to records/_rev/<rec_id>.r<N>.md
    "writeback.rev_written",     # ... made read-only
    "writeback.byid_tmp",        # record front matter written to its temporary file in records/_by_id/
    "writeback.byid_written",    # ... replaced records/_by_id/<rec_id>.md
    "writeback.committed",       # commit marker 'end' appended
    "writeback.receipt",         # the run's receipt line appended to a<N>/writeback_receipts.jsonl
})


def writeback_setup(root: Path):
    """fx-u8 audited, its confirm page written and every field to confirm clicked (route); ``(project, batch,
    a1, choices path)``. The choices file lives outside the data project."""
    from _support import writeback_fx as W

    root.mkdir(parents=True, exist_ok=True)
    project = root / "proj"
    project.mkdir()
    b, adir = W.audited(project, W.U8)
    choices = W.write_choices(root / "choices.json", W.export(project, b, adir, W.picks(adir)))
    return project, b, adir, choices


def _writeback_runner(tmp_path: Path, env: Mapping[str, str]) -> list[Path]:
    """Normal ``python -m klode_kit kb-audit --writeback <choices>`` on a fresh fx-u8 project (no records yet, so
    every durable write kind of the flow happens)."""
    project, _, _, choices = writeback_setup(tmp_path / "writeback-flow")
    res = run_module("klode_kit", ["kb-audit", "--project", str(project), "--writeback", str(choices)], env,
                     timeout=120)
    assert res.returncode == 0, res.stderr.decode()
    assert len(list((project / "records" / "_by_id").glob("*.md"))) == 2
    return [project]


REAL_FLOWS["writeback"] = RealFlow("writeback", "08c-13", "WI-08c", _writeback_runner, WRITEBACK_CRASHPOINTS)


def _writeback_entry(tmp_path: Path, env: Mapping[str, str], timeout: float):
    """00s-14 / 00s-15 on the real writeback; artifacts: everything the writeback owns plus its receipt log and
    stdout, wall-clock stamps normalised (each runner call writes at its own second)."""
    from _support import writeback_fx as W

    project, b, adir, choices = writeback_setup(tmp_path)
    res = run_module("klode_kit", ["kb-audit", "--project", str(project), "--writeback", str(choices)], env,
                     timeout=timeout)
    artifacts = dict(W.state(project, b))
    log = adir / "writeback_receipts.jsonl"
    if log.exists():
        artifacts["receipts"] = W.TS.sub(b"<ts>", log.read_bytes())
    artifacts["stdout"] = W.TS.sub(b"<ts>", res.stdout)
    return res, artifacts


REAL_ENTRIES["writeback"] = RealEntry("writeback", "WI-08c", _writeback_entry, "writeback.committed")


# --- publish (WI-09d) ---------------------------------------------------------------------
# Enumeration list of 09d-02 (tests/contract/test_publish_crash.py crashes at each point, then resumes) and of the
# 00s-16 publish parameter: a hooked ``pipeline --resume`` whose build and check stages are done, so that the
# publish stage runs - the pipeline kernel's own points around it and ``kb-publish``'s.
PUBLISH_ONLY_CRASHPOINTS = frozenset({
    "publish.lock",             # the publish lock (a flock on kb/<id>) taken
    "publish.tmp_link",         # current.tmp created, current not yet replaced
    "publish.switched",         # current replaced (one os.replace), the pointer row not yet appended
    "publish.logged",           # a PUBLISH.log row appended (loop: one name)
    "publish.mkdir",            # a directory of a registration created (.claude, .claude/agents; loop: one name)
    "publish.action_tmp",       # a registration written to its temporary file, not yet in place (loop)
    "publish.action_written",   # ... moved into place, its row not yet appended (loop)
    "publish.receipt_tmp",      # --receipt written to its temporary file
    "publish.receipt_written",  # ... moved into place
})
PIPELINE_PUBLISH_CRASHPOINTS = frozenset({
    "pipeline.mkdir",           # work/ and the stage receipts directory made (loop: one name)
    "pipeline.lock",
    "receipts.before_replace",  # the running / done stage receipt, temporary written
    "pipeline.stage_running",
    "pipeline.stage_done",
})
PUBLISH_CRASHPOINTS = PUBLISH_ONLY_CRASHPOINTS | PIPELINE_PUBLISH_CRASHPOINTS


def _publish_runner(tmp_path: Path, env: Mapping[str, str]) -> list[Path]:
    """``pipeline --resume`` of ``fx-u3-mixed`` whose stages up to check are done (run before, without the hook):
    only the publish stage runs under the audit hook, with the kernel and ``kb-publish`` as hooked processes."""
    from _support import publish_fx as PX

    root = tmp_path / "publish-flow"
    root.mkdir()
    rig = PX.pipeline_rig(root, "check")
    flow_env = dict(rig.env)
    for key in ("KLODE_KIT_AUDIT_ROLE", "KLODE_KIT_AUDIT_LOG", "PYTHONPATH"):
        flow_env[key] = env[key]
    res = subprocess.run(rig.argv("--resume"), env=flow_env, capture_output=True, timeout=300, cwd=str(rig.project))
    assert res.returncode == 0, res.stderr.decode()
    assert (PX.pipeline_kb(rig) / "current").is_symlink()
    return [rig.project]


REAL_FLOWS["publish"] = RealFlow("publish", "09d-02", "WI-09d", _publish_runner, PUBLISH_CRASHPOINTS)
