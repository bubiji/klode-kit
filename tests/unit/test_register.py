"""WI-11b: the registrar (``klode_kit.register`` / ``klode_kit.registry_write``) - matrix 11b-01 ... 11b-06, 11b-10,
11b-11. Law: a registration changes only its own keys (merge, S5) and an upsert is idempotent (S5).

Everything happens in tmp synthetic data projects with ``HOME`` and ``KLODE_REGISTRY`` inside tmp (gate G3: no test
reaches ``~/.claude``, ``~/.claude.json``, ``~/.klode`` or ``~/.codex``).

Deviation notes (impl-deviations WI-11b): 11b-06 "第一行有 build 标记" is read as the first line after the frontmatter
(WI-02d / R4-03: the file starts with ``---``); the standalone registrar exits 40 where publish exits 21.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support import publish_fx as F
from _support.flows import REPO, audit_env, base_env
from klode_kit import register as R
from klode_kit import registry_write as RW
from klode_kit.devtools import audit

pytestmark = pytest.mark.unit

A = F.build_id(0)
B = F.build_id(1)
LAUNCHER = str(REPO / "bin" / "klode-kit")
AGENT_TEXT = "---\nname: demo-klode-expert\n---\n<!-- klode-kit build {b} -->\nanswer from the library\n"


def project(tmp: Path, build_id: str = A, kb_name: str = "demo") -> tuple[Path, Path]:
    """A tmp synthetic data project with ``kb/<kb_name>/builds/<build_id>/agent/<kb_name>-klode-expert.md``."""
    proj = tmp / "proj"
    kb = proj / "kb" / kb_name
    agent = kb / "builds" / build_id / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / f"{kb_name}-klode-expert.md").write_text(AGENT_TEXT.format(b=build_id), encoding="utf-8")
    return proj, kb


def env_for(tmp: Path) -> dict[str, str]:
    return {"HOME": str(tmp / "home"), "KLODE_REGISTRY": str(tmp / "reg" / "registry.toml")}


def do(tmp: Path, build_id: str = A, kb_name: str = "demo") -> tuple[int, dict]:
    proj, kb = project(tmp, build_id, kb_name)
    return R.run(kb, build_id, project=proj, env=env_for(tmp))


# --------------------------------------------------------------------------- 11b-01: merge, only our key


json_values = st.recursive(
    st.none() | st.booleans() | st.integers(-1000, 1000) | st.text(max_size=12),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=8), inner, max_size=3),
    max_leaves=8,
)
other_servers = st.dictionaries(st.text(min_size=1, max_size=10).filter(lambda s: s != "demo-klode"), json_values,
                                max_size=4)
other_top = st.dictionaries(st.text(min_size=1, max_size=8).filter(lambda s: s != "mcpServers"), json_values,
                            max_size=3)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(servers=other_servers, top=other_top)
def test_mcp_json_merge_preserves_others(servers, top):
    """11b-01 (L2, S5): whatever else ``.mcp.json`` holds is untouched; our entry points at the launcher by absolute
    path and carries ``env.KLODE_KIT_BUILD``; the second registration leaves the file byte-identical."""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        proj, kb = project(tmp)
        (proj / ".mcp.json").write_text(json.dumps({**top, "mcpServers": servers}), encoding="utf-8")
        code, _ = R.run(kb, A, project=proj, env=env_for(tmp))
        assert code == 0
        first = (proj / ".mcp.json").read_bytes()
        doc = json.loads(first)
        assert {k: v for k, v in doc.items() if k != "mcpServers"} == top
        assert {k: v for k, v in doc["mcpServers"].items() if k != "demo-klode"} == servers
        ours = doc["mcpServers"]["demo-klode"]
        assert ours["command"] == LAUNCHER and os.path.isabs(ours["command"])
        assert ours["args"] == ["mcp", "--kb", str(kb)]
        assert ours["env"]["KLODE_KIT_BUILD"] == A
        code, _ = R.run(kb, A, project=proj, env=env_for(tmp))
        assert code == 0 and (proj / ".mcp.json").read_bytes() == first


def test_mcp_json_newer_build_changes_only_the_marker(tmp_path):
    """11b-01 widened (L2, S2): registering the next build moves the marker and nothing else."""
    proj, kb = project(tmp_path)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}), encoding="utf-8")
    assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    before = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    project(tmp_path, B)
    assert R.run(kb, B, project=proj, env=env_for(tmp_path))[0] == 0
    after = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    before["mcpServers"]["demo-klode"]["env"]["KLODE_KIT_BUILD"] = B
    assert after == before


def test_launcher_can_be_overridden_by_klode_kit_bin(tmp_path):
    proj, kb = project(tmp_path)
    assert R.run(kb, A, project=proj, env={**env_for(tmp_path), "KLODE_KIT_BIN": "/opt/klode-kit/bin/klode-kit"})[0] == 0
    entry = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["demo-klode"]
    assert entry["command"] == "/opt/klode-kit/bin/klode-kit"


# --------------------------------------------------------------------------- 11b-02 / 03


def test_mcp_json_absent_created(tmp_path):
    """11b-02 (L2, S2): no ``.mcp.json`` -> a new file holding only our key."""
    proj, kb = project(tmp_path)
    assert not (proj / ".mcp.json").exists()
    assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    doc = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    assert list(doc) == ["mcpServers"] and list(doc["mcpServers"]) == ["demo-klode"]
    assert not list(proj.glob(".*.tmp")) and not list(proj.glob("*.tmp"))


@pytest.mark.parametrize("content", [b"{ broken", b"[1, 2]", b"\xff\xfe", b'{"mcpServers": [1]}'],
                         ids=["syntax", "not-object", "not-utf8", "servers-not-object"])
def test_mcp_json_malformed_refuse(tmp_path, content, capsys, monkeypatch):
    """11b-03 (L2, S3): an unreadable ``.mcp.json`` -> exit 40, the file's bytes unchanged. The other registrations are
    independent of it and still happen."""
    proj, kb = project(tmp_path)
    (proj / ".mcp.json").write_bytes(content)
    env = env_for(tmp_path)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    code = R.main(["--kb", str(kb), "--build", A, "--project", str(proj)])
    assert code == 40
    assert (proj / ".mcp.json").read_bytes() == content
    body = json.loads(capsys.readouterr().out)
    status = {a["action"]: a["status"] for a in body["actions"]}
    assert status == {"mcp_json": "failed", "registry": "done", "agent": "done", "approve": "done"}
    assert body["exit_code"] == 40 and body["build_id"] == A
    assert tomllib.loads(Path(env["KLODE_REGISTRY"]).read_text(encoding="utf-8"))["kb"][0]["id"] == "demo"


# --------------------------------------------------------------------------- 11b-04


def test_settings_local_append(tmp_path):
    """11b-04 (L2, S1): the server name joins ``enabledMcpjsonServers`` once; the rest of the file is kept."""
    proj, kb = project(tmp_path)
    path = proj / ".claude" / "settings.local.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"enabledMcpjsonServers": ["other-klode"], "permissions": {"allow": ["Read"]}}),
                    encoding="utf-8")
    for _ in range(2):
        assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc == {"enabledMcpjsonServers": ["other-klode", "demo-klode"], "permissions": {"allow": ["Read"]}}


def test_settings_local_created_when_absent(tmp_path):
    proj, kb = project(tmp_path)
    assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    doc = json.loads((proj / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert doc == {"enabledMcpjsonServers": ["demo-klode"]}


# --------------------------------------------------------------------------- 11b-05 registry_write


names = st.text(alphabet="abcdefghij-", min_size=1, max_size=6)


def entries_of(path: Path) -> list[dict]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get("kb", [])


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(others=st.lists(names, unique=True, max_size=4), target=names, n=st.integers(1, 3))
def test_registry_upsert_idempotent(others, target, n):
    """11b-05 (L2, S5): upserting the same id again changes nothing (the bytes stay), leaves one entry for it, keeps every
    other entry, and the entry points at ``<kb>/current/library.toml`` with the build id."""
    others = [o for o in others if o != target]
    with tempfile.TemporaryDirectory() as raw:
        reg = Path(raw) / ".klode" / "registry.toml"
        for o in others:
            RW.upsert(reg, o, f"/data/{o}/current/library.toml", A)
        before = {e["id"]: e for e in entries_of(reg)} if reg.exists() else {}
        assert set(before) == set(others)
        RW.upsert(reg, target, f"/data/{target}/kb/current/library.toml", B)
        first = reg.read_bytes()
        for _ in range(n):
            RW.upsert(reg, target, f"/data/{target}/kb/current/library.toml", B)
        assert reg.read_bytes() == first
        got = entries_of(reg)
        assert [e["id"] for e in got].count(target) == 1
        mine = next(e for e in got if e["id"] == target)
        assert mine["path"].endswith("/current/library.toml") and mine["build_id"] == B
        assert {e["id"]: e for e in got if e["id"] != target} == before
        assert RW.marker(reg, target) == B and RW.marker(reg, "no-such-id") is None


def test_register_points_the_registry_at_current(tmp_path):
    """11b-05 through the registrar: ``KLODE_REGISTRY`` is honoured; the entry names ``kb/<id>/current/library.toml``."""
    proj, kb = project(tmp_path)
    env = env_for(tmp_path)
    for _ in range(2):
        assert R.run(kb, A, project=proj, env=env)[0] == 0
    got = entries_of(Path(env["KLODE_REGISTRY"]))
    assert got == [{"id": "demo", "path": str(kb / "current" / "library.toml"), "build_id": A}]


def test_registry_path_resolution(tmp_path):
    assert RW.registry_path({"KLODE_REGISTRY": str(tmp_path / "x.toml"), "HOME": "/h"}) == tmp_path / "x.toml"
    assert RW.registry_path({"HOME": str(tmp_path)}) == tmp_path / ".klode" / "registry.toml"
    assert RW.registry_path({"KLODE_REGISTRY": "", "HOME": str(tmp_path)}) == tmp_path / ".klode" / "registry.toml"


def test_registry_upsert_refuses_a_file_that_is_not_toml_and_leaves_it(tmp_path):
    reg = tmp_path / "r.toml"
    reg.write_text("this is = not = toml", encoding="utf-8")
    with pytest.raises(RW.ActionError, match="TOML"):
        RW.upsert(reg, "demo", "/p", A)
    assert reg.read_text(encoding="utf-8") == "this is = not = toml"
    reg.write_bytes(b"\xff\xfe")
    with pytest.raises(RW.ActionError):
        RW.marker(reg, "demo")


def test_registry_upsert_keeps_neighbours_and_foreign_tables(tmp_path):
    reg = tmp_path / "r.toml"
    reg.write_text('[other]\nkey = 1\n\n[[kb]]\nid = "first"\npath = "/a"\n\n[[kb]]\nid = "demo"\npath = "old"\n\n'
                   '[[kb]]\nid = "last"\npath = "/z"', encoding="utf-8")
    RW.upsert(reg, "demo", "/new", A)
    doc = tomllib.loads(reg.read_text(encoding="utf-8"))
    assert doc["other"] == {"key": 1}
    assert [e["id"] for e in doc["kb"]] == ["first", "demo", "last"]
    assert doc["kb"][1] == {"id": "demo", "path": "/new", "build_id": A}
    assert doc["kb"][0]["path"] == "/a" and doc["kb"][2]["path"] == "/z"


def test_registry_entries(tmp_path):
    reg = tmp_path / "r.toml"
    assert RW.entries(reg) == []
    RW.upsert(reg, "a", "/a", A)
    RW.upsert(reg, "b", "/b", B)
    assert [(e["id"], e["build_id"]) for e in RW.entries(reg)] == [("a", A), ("b", B)]


# --------------------------------------------------------------------------- 11b-06


def test_expert_written_atomic(tmp_path):
    """11b-06 (L2, S1): ``.claude/agents/<kb>-klode-expert.md`` is the build's agent file, byte for byte; its build marker
    is on the first line after the frontmatter; no temporary file is left."""
    proj, kb = project(tmp_path)
    assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    dest = proj / ".claude" / "agents" / "demo-klode-expert.md"
    lines = dest.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "---" and lines[3] == f"<!-- klode-kit build {A} -->"
    assert dest.read_bytes() == (kb / "builds" / A / "agent" / "demo-klode-expert.md").read_bytes()
    assert sorted(p.name for p in dest.parent.iterdir()) == ["demo-klode-expert.md"]


def test_expert_failed_write_keeps_the_old_agent_and_leaves_no_temp(tmp_path, monkeypatch):
    proj, kb = project(tmp_path)
    assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    dest = proj / ".claude" / "agents" / "demo-klode-expert.md"
    old = dest.read_bytes()
    project(tmp_path, B)
    real = os.replace

    def broken(src, dst):
        if str(dst).endswith("demo-klode-expert.md"):
            raise OSError("disk full")
        return real(src, dst)

    monkeypatch.setattr(os, "replace", broken)
    code, body = R.run(kb, B, project=proj, env=env_for(tmp_path))
    monkeypatch.setattr(os, "replace", real)
    assert code == 40 and {a["action"]: a["status"] for a in body["actions"]}["agent"] == "failed"
    assert dest.read_bytes() == old and sorted(p.name for p in dest.parent.iterdir()) == ["demo-klode-expert.md"]


def test_agent_without_a_build_agent_file_fails_only_that_action(tmp_path):
    proj, kb = project(tmp_path)
    for f in (kb / "builds" / A / "agent").glob("*.md"):
        f.rename(tmp_path / f.name)  # moved away, not deleted
    code, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert code == 40
    assert {a["action"]: a["status"] for a in body["actions"]} == {
        "mcp_json": "done", "registry": "done", "agent": "failed", "approve": "done"}


# --------------------------------------------------------------------------- 11b-10: never the real paths


COVERAGE_VARS = ("COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG", "COV_CORE_SOURCE", "COV_CORE_CONFIG",
                 "COV_CORE_DATAFILE", "COV_CORE_BRANCH", "COV_CORE_CONTEXT")


def run_register_cli(tmp: Path, log: Path, *, with_registry: bool) -> subprocess.CompletedProcess:
    proj, kb = project(tmp)
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    # the coverage variables are dropped: under the coverage lanes the child would write its coverage data file (outside tmp)
    env = audit_env(log, {"HOME": str(home)}, drop=(*COVERAGE_VARS, *(() if with_registry else ("KLODE_REGISTRY",))))
    return subprocess.run([sys.executable, "-m", "klode_kit", "register", "--kb", str(kb), "--build", A,
                           "--project", str(proj)], env=env, capture_output=True, timeout=120)


def test_never_touches_real_paths(tmp_path):
    """11b-10 (L2, S3): with ``HOME=tmp`` and no ``KLODE_REGISTRY`` a whole registration changes the file system only
    below tmp (audit hook over every mutating event); the default registry is ``tmp/.klode/registry.toml``."""
    real_registry = Path.home() / ".klode" / "registry.toml"
    snapshot = real_registry.read_bytes() if real_registry.exists() else None
    log = tmp_path / "audit.jsonl"
    proc = run_register_cli(tmp_path, log, with_registry=False)
    assert proc.returncode == 0, proc.stderr.decode()
    events = [e for e in audit.read_log(log) if e.get("mut")]
    assert events, "the hook saw no write at all"
    root = os.path.realpath(tmp_path)
    outside = [e["path"] for e in events if e["path"] != os.devnull and not (
        e["path"] is not None and (e["path"] == root or e["path"].startswith(root + os.sep)))]
    assert outside == []
    assert (tmp_path / "home" / ".klode" / "registry.toml").is_file()
    assert (real_registry.read_bytes() if real_registry.exists() else None) == snapshot
    assert sorted(p.name for p in (tmp_path / "home").iterdir()) == [".klode"]  # no .claude, .claude.json, .codex


# --------------------------------------------------------------------------- 11b-11: concurrent upserts


WORKER = """
import sys
from klode_kit import registry_write as RW
reg, tag, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
for i in range(n):
    RW.upsert(reg, f"{tag}-{i}", f"/data/{tag}-{i}/current/library.toml", "b-20261022T010203Z-0123abcd")
"""


def test_concurrent_registry_upsert(tmp_path):
    """11b-11 (L2, S5): two processes upserting different ids into one registry lose nothing and leave valid TOML."""
    reg = tmp_path / "reg" / "registry.toml"
    env = base_env()
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(reg), tag, "25"], env=env, stderr=subprocess.PIPE)
             for tag in ("p", "q")]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
    got = [e["id"] for e in tomllib.loads(reg.read_text(encoding="utf-8"))["kb"]]
    assert sorted(got) == sorted([f"p-{i}" for i in range(25)] + [f"q-{i}" for i in range(25)])
    assert len(got) == len(set(got))


# --------------------------------------------------------------------------- the CLI


def test_main_usage_errors(capsys):
    assert R.main([]) == 2
    assert R.main(["--kb", "x"]) == 2
    assert R.main(["--kb", "x", "--build", "latest"]) == 2  # not a build id


def test_main_reports_one_json_line(tmp_path, capsys, monkeypatch):
    proj, kb = project(tmp_path)
    for k, v in env_for(tmp_path).items():
        monkeypatch.setenv(k, v)
    code = R.main(["--kb", str(kb), "--build", A, "--project", str(proj)])
    out = capsys.readouterr().out
    body = json.loads(out)
    assert code == 0 and out.count("\n") == 1
    assert body["schema"] == "klode-kit.register/1" and body["kb_id"] == "demo" and body["build_id"] == A
    assert [a["action"] for a in body["actions"]] == ["mcp_json", "registry", "agent", "approve"]


def test_main_project_defaults_to_the_one_above_kb(tmp_path, capsys, monkeypatch):
    proj, kb = project(tmp_path)
    for k, v in env_for(tmp_path).items():
        monkeypatch.setenv(k, v)
    assert R.main(["--kb", str(kb), "--build", A]) == 0
    assert (proj / ".mcp.json").is_file()
    capsys.readouterr()
    assert R.main(["--kb", str(tmp_path / "elsewhere" / "demo"), "--build", A]) == 2  # no project can be derived
