"""WI-00d1 (added cases): agents/*.md -> --agents JSON guards, task/option guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from klode_kit import bridge
from klode_kit.config import load

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
WRITER = "---\nname: klode-card-writer\ndescription: w\n---\nbody\n"


def _dir(tmp_path: Path, **files: str) -> Path:
    d = tmp_path / "agents"
    d.mkdir()
    for name, text in files.items():
        (d / f"{name}.md").write_text(text, encoding="utf-8")
    return d


@pytest.mark.parametrize("extra", [
    "no front matter\n",
    "---\nname: x\ndescription: y\n",
    "---\nname x\n---\nbody\n",
    "---\nname: x\n---\nbody\n",
    "---\nname: a:b\ndescription: y\n---\nbody\n",
    "---\nname: klode-card-writer\ndescription: again\n---\nbody\n",
])
def test_bad_agent_file_rejected(tmp_path, extra):
    agents_dir = _dir(tmp_path, **{"klode-card-writer": WRITER, "zz-extra": extra})
    with pytest.raises(bridge.BridgeUsageError):
        bridge.agents_json(agents_dir)


def test_writer_agent_required(tmp_path):
    agents_dir = _dir(tmp_path, other="---\nname: other\ndescription: o\n---\nb\n")
    with pytest.raises(bridge.BridgeUsageError):
        bridge.agents_json(agents_dir)


def test_comments_and_blank_lines_in_front_matter_ignored(tmp_path):
    agents_dir = _dir(tmp_path, **{"klode-card-writer": "---\n# note\n\nname: klode-card-writer\ndescription: w\n"
                                                        "disallowedTools: Write, NotebookEdit\n---\nbody\n"})
    assert bridge.agents_json(agents_dir) == {
        "klode-card-writer": {"description": "w", "prompt": "body", "disallowedTools": ["Write", "NotebookEdit"]}}


def test_repository_writer_agent_is_valid():
    """The placeholder agents/klode-card-writer.md (final text: WI-12) loads and keeps writes in cards_content/."""
    agents = bridge.agents_json(REPO / "agents")
    writer = agents["klode-card-writer"]
    assert "cards_content/" in writer["prompt"]
    assert json.loads(json.dumps(agents)) == agents


@pytest.mark.parametrize("kw", [{"task_id": ""}, {"task_id": "a/b"}, {"instructions": None}])
def test_task_guards(tmp_path, kw):
    build = tmp_path / "builds" / "b-1"
    args = {"task_id": "t-1", "build_dir": build, "card": "cards_content/x.md", "instructions": "x", **kw}
    with pytest.raises(bridge.BridgeUsageError):
        bridge.Task(**args)


@pytest.mark.parametrize("bad", [{"concurrency": True}, {"max_rounds": 1.5}, {"timeout_s": "1"},
                                 {"kill_grace_s": -1}, {"max_budget_usd": float("nan")}])
def test_option_type_guards(bad):
    with pytest.raises(bridge.BridgeUsageError):
        bridge.Options.from_config(load(), **bad)
