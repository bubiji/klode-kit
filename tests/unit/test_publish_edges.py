"""WI-09d: the edges of the publish module that need no real build - an unreadable log, the registrations' files in odd
states, the move of a sealed build that cannot be moved. Cases added while implementing (the specification files
``test_publish_logic.py`` / ``tests/contract/test_publish_*.py`` are unchanged by them).
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from _support import publish_fx as F
from klode_kit import publish as P

pytestmark = pytest.mark.unit

A = F.build_id(0)


# --------------------------------------------------------------------------- the log


@pytest.mark.parametrize("content,fragment", [
    (b"\xff\xfe\n", "cannot be read"),
    (b"not json\n", "not JSON"),
    (json.dumps({"seq": 1, "target_build_id": A, "prev_build_id": None, "action": "bogus", "status": "done",
                 "at": "2026-10-25T00:00:00Z"}).encode() + b"\n", "action"),
    (json.dumps(F.row(2, A, "switch")).encode() + b"\n" + json.dumps(F.row(2, A, "mcp_json")).encode() + b"\n",
     "does not follow"),
], ids=["not-utf8", "not-json", "contract", "seq-order"])
def test_read_log_refuses_what_it_cannot_trust(tmp_path, content, fragment):
    kb, _ = F.fake_kb(tmp_path, 1)
    (kb / "PUBLISH.log").write_bytes(content)
    with pytest.raises(P.LogError, match=fragment):
        P.read_log(kb)


def test_read_log_of_a_directory_is_a_log_error(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    (kb / "PUBLISH.log").mkdir()
    with pytest.raises(P.LogError):
        P.read_log(kb)


def test_append_row_refuses_a_row_that_breaks_the_contract_and_writes_nothing(tmp_path):
    kb, _ = F.fake_kb(tmp_path, 1)
    with pytest.raises(ValueError):
        P.append_row(kb, A, "bogus", "done")
    assert not (kb / "PUBLISH.log").exists()
    row = P.append_row(kb, A, "switch", "done", detail="x" * 900)
    assert len(row["detail"]) == 500


def test_list_builds(tmp_path):
    assert P.list_builds(tmp_path / "nowhere") == []
    kb, ids = F.fake_kb(tmp_path, 2)
    os.symlink(ids[0], kb / "builds" / F.build_id(5))  # a link named like a build is not one
    (kb / "builds" / "_trash").mkdir()
    assert P.list_builds(kb) == ids


# --------------------------------------------------------------------------- moving a sealed build


def test_rename_that_cannot_succeed_leaves_the_seal_as_it_was(tmp_path):
    src = tmp_path / "builds" / A
    src.mkdir(parents=True)
    (src / "f").write_text("x", encoding="utf-8")
    os.chmod(src / "f", 0o444)
    os.chmod(src, 0o555)
    dest_parent = tmp_path / "locked"
    dest_parent.mkdir()
    os.chmod(dest_parent, 0o555)
    try:
        with pytest.raises(PermissionError):
            P._rename_build(src, dest_parent / A)
        assert stat.S_IMODE(os.stat(src).st_mode) == 0o555 and src.is_dir()
    finally:
        os.chmod(dest_parent, 0o755)
        os.chmod(src, 0o755)
        os.chmod(src / "f", 0o644)


# --------------------------------------------------------------------------- atomic writes and directories


def test_ensure_dir_refuses_a_file_in_the_way(tmp_path):
    (tmp_path / "blocker").write_text("x", encoding="utf-8")
    with pytest.raises(P.ActionError):
        P._ensure_dir(tmp_path / "blocker" / "sub")
    with pytest.raises(P.ActionError):
        P._ensure_dir(tmp_path / "blocker")


def test_ensure_dir_when_the_path_becomes_a_file_while_it_is_made(tmp_path, monkeypatch):
    real = os.mkdir

    def racing(path, *a, **k):
        Path(path).write_text("someone else's file", encoding="utf-8")
        raise FileExistsError(path)

    monkeypatch.setattr(os, "mkdir", racing)
    with pytest.raises(P.ActionError):
        P._ensure_dir(tmp_path / "d")
    monkeypatch.setattr(os, "mkdir", real)


def test_ensure_dir_when_someone_else_made_the_directory(tmp_path, monkeypatch):
    real = os.mkdir

    def racing(path, *a, **k):
        real(path)
        raise FileExistsError(path)

    monkeypatch.setattr(os, "mkdir", racing)
    P._ensure_dir(tmp_path / "d" / "e")
    assert (tmp_path / "d" / "e").is_dir()


def test_atomic_failure_leaves_the_old_file_and_no_temp(tmp_path, monkeypatch):
    target = tmp_path / "f.json"
    target.write_text("old", encoding="utf-8")

    def broken(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken)
    with pytest.raises(OSError):
        P._atomic(target, b"new")
    assert target.read_text(encoding="utf-8") == "old" and list(tmp_path.glob("*.tmp")) == []


# --------------------------------------------------------------------------- the registrations' files


def target(tmp_path: Path, env: dict | None = None) -> P.Target:
    kb = tmp_path / "proj" / "kb" / "demo"
    (kb / "builds" / A / "agent").mkdir(parents=True)
    return P.Target(kb, tmp_path / "proj", A, env if env is not None else {"KLODE_REGISTRY": str(tmp_path / "r.toml")})


@pytest.mark.parametrize("content,fragment", [
    ("[1, 2]", "not a JSON object"),
    ("{ broken", "cannot be read as JSON"),
    ('{"mcpServers": [1]}', "mcpServers is not an object"),
])
def test_mcp_json_in_an_odd_state_is_left_alone(tmp_path, content, fragment):
    t = target(tmp_path)
    (t.project / ".mcp.json").write_text(content, encoding="utf-8")
    with pytest.raises(P.ActionError, match=fragment):
        P.ACTION_IMPL["mcp_json"][1](t)
    assert (t.project / ".mcp.json").read_text(encoding="utf-8") == content


def test_mcp_json_keeps_the_other_keys_of_our_entry(tmp_path):
    t = target(tmp_path, {"KLODE_REGISTRY": str(tmp_path / "r.toml"), "KLODE_KIT_BIN": "/opt/klode-kit"})
    (t.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"demo-klode": {
        "type": "stdio", "env": {"OTHER": "1", "KLODE_KIT_BUILD": F.build_id(3)}}}}), encoding="utf-8")
    P.ACTION_IMPL["mcp_json"][1](t)
    entry = json.loads((t.project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["demo-klode"]
    assert entry == {"type": "stdio", "command": "/opt/klode-kit", "args": ["mcp", "--kb", str(t.kb)],
                     "env": {"OTHER": "1", "KLODE_KIT_BUILD": A}}
    assert P.ACTION_IMPL["mcp_json"][0](t) == A


def test_mcp_marker_is_none_for_a_marker_that_is_not_a_build_id(tmp_path):
    t = target(tmp_path)
    (t.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"demo-klode": {
        "env": {"KLODE_KIT_BUILD": "latest"}}}}), encoding="utf-8")
    assert P.ACTION_IMPL["mcp_json"][0](t) is None
    (t.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"demo-klode": {"env": [1]}}}), encoding="utf-8")
    assert P.ACTION_IMPL["mcp_json"][0](t) is None
    (t.project / ".mcp.json").write_text(json.dumps({"mcpServers": []}), encoding="utf-8")
    assert P.ACTION_IMPL["mcp_json"][0](t) is None


def test_registry_default_path_is_below_home(tmp_path):
    t = target(tmp_path, {"HOME": str(tmp_path / "home")})
    assert P.ACTION_IMPL["registry"][0](t) is None  # nothing there yet
    P.ACTION_IMPL["registry"][1](t)
    assert (tmp_path / "home" / ".klode" / "registry.toml").is_file()
    assert P.ACTION_IMPL["registry"][0](t) == A


def test_registry_that_is_not_toml_is_left_alone(tmp_path):
    t = target(tmp_path)
    (tmp_path / "r.toml").write_text("this is = not = toml", encoding="utf-8")
    with pytest.raises(P.ActionError, match="TOML"):
        P.ACTION_IMPL["registry"][1](t)
    assert (tmp_path / "r.toml").read_text(encoding="utf-8") == "this is = not = toml"
    (tmp_path / "r.toml").write_bytes(b"\xff\xfe")
    with pytest.raises(P.ActionError):
        P.ACTION_IMPL["registry"][0](t)


def test_registry_update_in_place_keeps_neighbours_and_blank_lines(tmp_path):
    t = target(tmp_path)
    (tmp_path / "r.toml").write_text(
        '[[kb]]\nid = "first"\npath = "/a"\n\n[[kb]]\nid = "demo"\npath = "old"\n\n[[kb]]\nid = "last"\npath = "/z"',
        encoding="utf-8")
    P.ACTION_IMPL["registry"][1](t)
    text = (tmp_path / "r.toml").read_text(encoding="utf-8")
    import tomllib

    entries = tomllib.loads(text)["kb"]
    assert [e["id"] for e in entries] == ["first", "demo", "last"]
    assert entries[1]["build_id"] == A and entries[0]["path"] == "/a" and entries[2]["path"] == "/z"
    assert text.count("[[kb]]") == 3 and "\n\n[[kb]]\nid = \"last\"" in text


def test_registry_appends_after_a_file_without_a_final_newline(tmp_path):
    t = target(tmp_path)
    (tmp_path / "r.toml").write_text('[other]\nkey = 1', encoding="utf-8")
    P.ACTION_IMPL["registry"][1](t)
    import tomllib

    doc = tomllib.loads((tmp_path / "r.toml").read_text(encoding="utf-8"))
    assert doc["other"] == {"key": 1} and doc["kb"][0]["id"] == "demo"


def test_agent_without_a_file_fails_and_a_marker_is_read_from_the_installed_one(tmp_path):
    t = target(tmp_path)
    with pytest.raises(P.ActionError, match="no agent file"):
        P.ACTION_IMPL["agent"][1](t)
    (t.build / "agent" / "demo-klode-expert.md").write_text(f"---\nname: x\n---\n<!-- klode-kit build {A} -->\n",
                                                            encoding="utf-8")
    assert P.ACTION_IMPL["agent"][0](t) is None  # not installed yet
    P.ACTION_IMPL["agent"][1](t)
    assert P.ACTION_IMPL["agent"][0](t) == A
    (t.project / ".claude" / "agents" / "demo-klode-expert.md").write_text("no marker here\n", encoding="utf-8")
    assert P.ACTION_IMPL["agent"][0](t) is None


def test_approve_edges(tmp_path):
    t = target(tmp_path)
    settings = t.project / ".claude" / "settings.local.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"enabledMcpjsonServers": "demo-klode"}), encoding="utf-8")
    with pytest.raises(P.ActionError, match="not a list"):
        P.ACTION_IMPL["approve"][1](t)
    settings.write_text(json.dumps({"enabledMcpjsonServers": ["demo-klode"]}), encoding="utf-8")
    before = settings.stat().st_mtime_ns
    assert P.ACTION_IMPL["approve"][1](t) == "demo-klode already enabled"
    assert settings.stat().st_mtime_ns == before  # nothing to change, nothing written


def test_project_of():
    assert P._project_of(Path("/x/proj/kb/demo"), None) == Path("/x/proj")
    assert P._project_of(Path("/x/elsewhere/demo"), None) is None
    assert P._project_of(Path("/x/elsewhere/demo"), "/y") == Path("/y")


def test_verify_target_names_what_is_wrong(tmp_path):
    kb, ids = F.fake_kb(tmp_path, 1)
    gone = P.verify_target(kb, F.build_id(9))
    assert gone == [f"no such build: builds/{F.build_id(9)}"]
    bare = P.verify_target(kb, ids[0])  # a directory that is no build at all
    assert any(p.startswith("build_receipt.json") for p in bare)
    assert any(p.startswith("MANIFEST.sha256") for p in bare)
    assert any(p.startswith("check_receipt.json") for p in bare)
    assert any(p.startswith("writable") for p in bare)
    assert P.verify_target(kb, "../escape") == ["no such build: builds/../escape"]
