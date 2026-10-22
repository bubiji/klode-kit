"""Test seams: crash points and pause points (test matrix section 0.3, decision F9).

``crashpoint(name)`` is called by production code at fixed places. It ALWAYS emits
``sys.audit("klode_kit.crashpoint", name)`` - in production too - so audit-hook runs can
observe the order and position of crash points.

The seam variables only take effect when BOTH hold:
- ``KLODE_KIT_TEST_SEAMS=1`` (set only by tests/conftest.py), and
- ``KLODE_KIT_REAL != 1``.

Otherwise they are ignored, and stderr says so once, naming the variables.

When active:
- ``KLODE_KIT_CRASH_AT=<point>``: the process SIGKILLs itself on reaching the point.
- ``KLODE_KIT_PAUSE_AT=<point>`` needs ``KLODE_KIT_PAUSE_DIR`` (an existing directory);
  without it the run stops at once with exit 2 and never falls back to a global
  directory. On reaching the point (first pass only) the process writes
  ``<dir>/<point>.reached``, blocks until ``<dir>/<point>.go`` exists, deletes it and goes on.
"""

from __future__ import annotations

import os
import signal
import sys
import time

SEAM_VARS = ("KLODE_KIT_CRASH_AT", "KLODE_KIT_PAUSE_AT", "KLODE_KIT_PAUSE_DIR")
AUDIT_EVENT = "klode_kit.crashpoint"
USAGE_EXIT = 2

_started = False
_paused: set[str] = set()


def seams_active(env=None) -> bool:
    env = os.environ if env is None else env
    return env.get("KLODE_KIT_TEST_SEAMS") == "1" and env.get("KLODE_KIT_REAL") != "1"


def startup(stream=None) -> None:
    """Validate seam variables once per process; call at every entry point."""
    global _started
    if _started:
        return
    _started = True
    stream = sys.stderr if stream is None else stream
    present = [name for name in SEAM_VARS if os.environ.get(name)]
    if not seams_active():
        if present:
            print(
                "klode-kit: 测试缝变量已忽略（未处于测试模式：需要 KLODE_KIT_TEST_SEAMS=1 且 "
                f"KLODE_KIT_REAL!=1）：{', '.join(present)}",
                file=stream,
                flush=True,
            )
        return
    if os.environ.get("KLODE_KIT_PAUSE_AT"):
        pause_dir = os.environ.get("KLODE_KIT_PAUSE_DIR", "")
        if not pause_dir:
            print(
                "klode-kit: usage error: KLODE_KIT_PAUSE_AT is set but KLODE_KIT_PAUSE_DIR is missing "
                "(refusing to fall back to a global directory)",
                file=stream,
                flush=True,
            )
            raise SystemExit(USAGE_EXIT)
        if not os.path.isdir(pause_dir):
            print(
                f"klode-kit: usage error: KLODE_KIT_PAUSE_DIR is not an existing directory: {pause_dir}",
                file=stream,
                flush=True,
            )
            raise SystemExit(USAGE_EXIT)


def crashpoint(name: str) -> None:
    sys.audit(AUDIT_EVENT, name)
    startup()
    if not seams_active():
        return
    if os.environ.get("KLODE_KIT_CRASH_AT") == name:
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        os.kill(os.getpid(), signal.SIGKILL)
    if os.environ.get("KLODE_KIT_PAUSE_AT") == name and name not in _paused:
        _paused.add(name)
        _pause(name, os.environ["KLODE_KIT_PAUSE_DIR"])


def _pause(name: str, pause_dir: str) -> None:
    reached = os.path.join(pause_dir, f"{name}.reached")
    go = os.path.join(pause_dir, f"{name}.go")
    with open(reached, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    while not os.path.exists(go):
        time.sleep(0.01)
    os.remove(go)
