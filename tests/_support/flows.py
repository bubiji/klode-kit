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


REAL_FLOWS: dict[str, RealFlow] = {
    "writeback": RealFlow("writeback", "08c-13", "WI-08c"),
    "publish": RealFlow("publish", "09d-02", "WI-09d"),
    "receipts": RealFlow("receipts", "00b1-11", "WI-00b1"),
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
