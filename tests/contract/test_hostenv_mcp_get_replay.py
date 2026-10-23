"""WI-00d2: ``hostenv.mcp_get_status`` through the replay fake (child-process boundary; paired real path 00d2-06).

The fake ``claude`` replays the recorded ``claude mcp get`` texts (tests/fixtures/host/mcp_get/), so this checks the
runner half - argv ``[claude, "mcp", "get", <name>]``, cwd, stdout+stderr joined - and that the parse is applied.
"""

from __future__ import annotations

import pytest

from _support.flows import REPO
from _support.replay import log_entries, write_scenario
from klode_kit import hostenv

pytestmark = pytest.mark.contract_replay

REC = REPO / "tests" / "fixtures" / "host" / "mcp_get"


@pytest.mark.parametrize("name, expected, rc", [("connected", "connected", 0), ("pending", "pending", 0),
                                                ("failed", "failed", 0), ("missing", "missing", 1)])
def test_mcp_get_status_runs_cli_in_cwd_and_parses(tmp_path, monkeypatch, fake_log, name, expected, rc):
    replay = tmp_path / "replay"
    text = (REC / f"{name}.txt").read_text(encoding="utf-8")
    if name == "missing":  # the CLI prints this one on stderr and exits 1
        write_scenario(replay, "claude", ["mcp", "get", "spike-klode"], "", stderr=text.splitlines()[0] + "\n", exit_code=rc)
    else:
        write_scenario(replay, "claude", ["mcp", "get", "spike-klode"], text, exit_code=rc)
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(replay))
    project = tmp_path / "proj"
    project.mkdir()
    status, raw = hostenv.mcp_get_status("spike-klode", project)
    assert status == expected
    assert raw.strip()
    calls = log_entries(fake_log)
    assert [c["argv"] for c in calls] == [["mcp", "get", "spike-klode"]]
    assert calls[0]["cwd"] == str(project.resolve())
