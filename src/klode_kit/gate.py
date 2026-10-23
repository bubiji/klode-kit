"""G2 real-call gate shared by the execution bridge (WI-00d2) and the cloud sender (WI-06a).

A model CLI (``claude`` / ``codex``) is started only when one of these holds:

- it resolves (real path, the same PATH lookup ``subprocess`` does) to the replay fake in this repository's
  ``tests/fakes/bin`` - compared by real path, never by name, so a look-alike directory does not count;
- the caller allows generated fakes (the sender does, the bridge does not) and the resolved file is a test fake
  written by ``tests/_support/send_fake.FakeCloud``: a small ``#!`` script carrying ``TEST_FAKE_MARKER``;
- the gate is open: ``KLODE_KIT_REAL == "1"`` and ``KLODE_KIT_GATE_ACK == G2-<yyyymmdd>`` (a calendar date;
  plan section 1, gate G2).

Otherwise ``vet`` raises (``GateClosed`` or the caller's subclass) before anything is started. Exit code of a
closed gate: ``GATE_EXIT`` (61).
"""

from __future__ import annotations

import os
import re
import shutil
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
FAKES_BIN = PLUGIN_ROOT / "tests" / "fakes" / "bin"
TEST_FAKE_MARKER = "klode-kit-test-fake: replay fake, never calls a model"
GATE_EXIT = 61
GATE_ACK_RE = re.compile(r"\AG2-([0-9]{8})\Z")  # ASCII digits; the date must also be a calendar date
_FAKE_MAX_BYTES = 64 * 1024


class GateClosed(RuntimeError):
    """A non-fake model CLI would be started without KLODE_KIT_REAL=1 and a G2 ack (exit 61, zero calls)."""


def ack_valid(ack: str) -> bool:
    match = GATE_ACK_RE.match(ack)
    if not match:
        return False
    try:
        datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        return False
    return True


def resolve(name: str, path: str | None = None) -> Path | None:
    """Absolute real path of the executable ``name`` names on ``path`` (default: this process's PATH), or None."""
    found = shutil.which(name, path=path)
    return Path(os.path.realpath(found)) if found else None


def is_repo_fake(exe: Path) -> bool:
    return exe.parent == Path(os.path.realpath(FAKES_BIN))


def is_generated_fake(exe: Path) -> bool:
    """A FakeCloud front: a small regular ``#!`` script holding the marker line."""
    try:
        st = os.stat(exe)
        if not stat.S_ISREG(st.st_mode) or st.st_size > _FAKE_MAX_BYTES:
            return False
        head = exe.read_bytes()
    except OSError:
        return False
    return head.startswith(b"#!") and f"# {TEST_FAKE_MARKER}\n".encode() in head


def vet(name: str, *, path: str | None = None, env: Mapping[str, Any] | None = None,
        allow_generated_fakes: bool = False, error: type[GateClosed] = GateClosed) -> Path | None:
    """The resolved executable for ``name`` (None when it does not resolve and the gate is open); raises
    ``error`` when it is not an allowed fake and the gate is closed. Starts nothing."""
    env = os.environ if env is None else env
    resolved = resolve(name, path)
    if resolved is not None and (is_repo_fake(resolved) or (allow_generated_fakes and is_generated_fake(resolved))):
        return resolved
    real = env.get("KLODE_KIT_REAL")
    ack = env.get("KLODE_KIT_GATE_ACK", "")
    if real != "1" or not ack_valid(ack):
        raise error(
            f"refusing to start {str(resolved) if resolved else name!r}: not a test fake, and the real-call gate "
            f"G2 is closed (needs KLODE_KIT_REAL=1 and KLODE_KIT_GATE_ACK=G2-<yyyymmdd>; got REAL={real!r}, "
            f"ACK={ack!r})")
    return resolved
