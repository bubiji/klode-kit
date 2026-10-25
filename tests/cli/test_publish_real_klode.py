"""WI-09d: what ``current`` points to is a library the real ``klode-mcp`` serves (design 02 r4.2: the registrations
point to ``kb/<id>/current/library.toml``, never to a build).

Paired with the contract cases of ``tests/contract/test_publish_protocol.py`` (they check the pointer and the log;
this one checks that the thing behind the pointer is usable through the link, before and after a switch). A missing
``klode-mcp`` FAILS - the lane is about the real path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from _support import publish_fx as F
from klode_kit.build import smoke_mcp
from klode_kit.check import kb_check

pytestmark = pytest.mark.cli_integration


def smoke_through_current(kb: Path) -> list[str]:
    exe = shutil.which("klode-mcp")
    assert exe, "klode-mcp 0.5.0 is required on PATH"
    card, phrase = kb_check.smoke_target(kb / "current")
    assert card, "the published build has no card with an anchor"
    return [r.label for r in smoke_mcp.run(kb / "current", card, phrase, exe=exe) if not r.ok]


def test_current_is_served_by_the_real_klode_mcp(tmp_path):
    ab = F.ab(tmp_path)
    for build in (ab.a, ab.b):
        proc = F.cli(ab.kb, ab.project, tmp_path, "--build", build)
        assert proc.returncode == 0, proc.stderr.decode()
        assert smoke_through_current(ab.kb) == []  # six checks of the real klode-mcp, through the link
        receipt = json.loads((ab.kb / "current" / "build_receipt.json").read_text(encoding="utf-8"))
        assert receipt["build_id"] == build and F.sound(ab.kb) == []
    mcp = json.loads((ab.project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][f"{ab.kb.name}-klode"]
    assert Path(mcp["command"]).is_file()  # the launcher the registration names exists
    assert mcp["env"] == {"KLODE_KIT_BUILD": ab.b}
