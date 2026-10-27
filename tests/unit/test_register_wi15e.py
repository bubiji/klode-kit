"""WI-15e: ``klode_kit.register`` pinned exactly (mutation-gate survivors of WI-15c, score 0.75 -> >= 0.80).

``test_register.py`` checks that the four registrations happen and that foreign keys survive. It does not pin what the
user (and the files in a data project) actually see: the exact bytes of ``.mcp.json`` / ``settings.local.json``, the
``detail`` of each action and of each refusal, the names of the entries created (``.claude/agents``, ``.mcp.json``),
the body keys, the argparse surface (``--help``, which options are required), ``main()`` reading ``sys.argv``, the
explicit ``--project``, non-ASCII output, and the 40-line head the agent marker is read from.

Law: none new - the merge/idempotence laws are held by ``test_register.py``. These are S1/S2 pins; the byte-for-byte
file contents are the S1 form of "a registration writes exactly its own keys".

Mutants that cannot be killed on the case-insensitive macOS file system (the only supported platform) and are left
alone, with the reason: upper-casing a path component that is only *read* (``.MCP.JSON`` in ``_mcp_marker``,
``.CLAUDE`` / ``AGENT`` in ``_agent_marker`` / ``_agent_files``) resolves to the same file; ``"UTF-8"`` is the same codec
as ``"utf-8"``; ``type=Path`` / ``default=None`` removed from ``--kb`` / ``--project`` (``run`` wraps both in ``Path``
anyway); ``mcpServers`` default ``{}`` vs ``None`` in ``_mcp_marker`` (both end in "no entry"); ``build_id or ""`` vs
``or "XXXX"`` (neither is a build id); ``... or "XXXX"`` on an applier that always returns a non-empty string.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _support import publish_fx as F
from _support.flows import base_env
from klode_kit import register as R
from klode_kit.pipeline import Reason

pytestmark = pytest.mark.unit

A = F.build_id(0)
B = F.build_id(1)
AGENT_TEXT = "---\nname: x\n---\n<!-- klode-kit build {b} -->\nanswer\n"
COVERAGE_VARS = ("COVERAGE_PROCESS_START", "COVERAGE_PROCESS_CONFIG", "COV_CORE_SOURCE", "COV_CORE_CONFIG",
                 "COV_CORE_DATAFILE", "COV_CORE_BRANCH", "COV_CORE_CONTEXT")


def project(tmp: Path, build_id: str = A, kb_name: str = "demo", proj_name: str = "proj") -> tuple[Path, Path]:
    proj = tmp / proj_name
    kb = proj / "kb" / kb_name
    agent = kb / "builds" / build_id / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    (agent / f"{kb_name}-klode-expert.md").write_text(AGENT_TEXT.format(b=build_id), encoding="utf-8")
    return proj, kb


def env_for(tmp: Path, **extra: str) -> dict[str, str]:
    return {"HOME": str(tmp / "home"), "KLODE_REGISTRY": str(tmp / "reg" / "registry.toml"), **extra}


def set_env(monkeypatch, tmp: Path) -> None:
    for k, v in env_for(tmp).items():
        monkeypatch.setenv(k, v)


def statuses(body: dict) -> dict[str, str]:
    return {a["action"]: a["status"] for a in body["actions"]}


def details(body: dict) -> dict[str, str]:
    return {a["action"]: a["detail"] for a in body["actions"]}


# --------------------------------------------------------------------------- exact bytes written


def test_mcp_json_exact_text_keeps_foreign_entry_and_non_ascii(tmp_path):
    proj, kb = project(tmp_path)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"其他": {"command": "é"}}}), encoding="utf-8")
    code, _ = R.run(kb, A, project=proj, env=env_for(tmp_path, KLODE_KIT_BIN="/opt/k/bin/klode-kit"))
    assert code == 0
    expected = (
        '{\n'
        '  "mcpServers": {\n'
        '    "其他": {\n'
        '      "command": "é"\n'
        '    },\n'
        '    "demo-klode": {\n'
        '      "command": "/opt/k/bin/klode-kit",\n'
        '      "args": [\n'
        '        "mcp",\n'
        '        "--kb",\n'
        f'        "{os.path.abspath(kb)}"\n'
        '      ],\n'
        '      "env": {\n'
        f'        "KLODE_KIT_BUILD": "{A}"\n'
        '      }\n'
        '    }\n'
        '  }\n'
        '}\n')
    assert (proj / ".mcp.json").read_bytes() == expected.encode("utf-8")


def test_mcp_json_created_from_nothing_exact_text(tmp_path):
    proj, kb = project(tmp_path)
    assert R.run(kb, A, project=proj, env=env_for(tmp_path, KLODE_KIT_BIN="/b/k"))[0] == 0
    text = (proj / ".mcp.json").read_text(encoding="utf-8")
    assert text.startswith('{\n  "mcpServers": {\n    "demo-klode": {\n      "command": "/b/k",\n')
    assert text.endswith('\n    }\n  }\n}\n')


def test_settings_local_exact_text(tmp_path):
    proj, kb = project(tmp_path)
    assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    raw = (proj / ".claude" / "settings.local.json").read_bytes()
    assert raw == '{\n  "enabledMcpjsonServers": [\n    "demo-klode"\n  ]\n}\n'.encode("utf-8")


def test_settings_local_keeps_non_ascii_unescaped(tmp_path):
    proj, kb = project(tmp_path)
    path = proj / ".claude" / "settings.local.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"note": "说明"}, ensure_ascii=False), encoding="utf-8")
    assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    assert path.read_bytes() == ('{\n  "note": "说明",\n  "enabledMcpjsonServers": [\n    "demo-klode"\n  ]\n}\n'
                                 .encode("utf-8"))


def test_created_entries_have_the_exact_names(tmp_path):
    """On a case-insensitive file system only the *listing* shows the case a component was created with."""
    proj, kb = project(tmp_path)
    assert R.run(kb, A, project=proj, env=env_for(tmp_path))[0] == 0
    assert sorted(os.listdir(proj)) == [".claude", ".mcp.json", "kb"]
    assert sorted(os.listdir(proj / ".claude")) == ["agents", "settings.local.json"]
    assert os.listdir(proj / ".claude" / "agents") == ["demo-klode-expert.md"]


def test_settings_entry_names_when_the_agent_action_fails_first(tmp_path):
    proj, kb = project(tmp_path)
    for f in (kb / "builds" / A / "agent").glob("*.md"):
        f.rename(tmp_path / f.name)
    code, _ = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert code == 40
    assert os.listdir(proj / ".claude") == ["settings.local.json"]


# --------------------------------------------------------------------------- details and body


def test_details_of_the_four_actions_and_the_body_keys(tmp_path):
    proj, kb = project(tmp_path)
    code, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert code == 0
    assert body == {
        "schema": "klode-kit.register/1", "kb_id": "demo", "build_id": A, "exit_code": 0, "reason": "ok",
        "project": str(proj),
        "actions": [
            {"action": "mcp_json", "status": "done", "detail": f"demo-klode -> build {A}"},
            {"action": "registry", "status": "done", "detail": f"demo -> build {A}"},
            {"action": "agent", "status": "done", "detail": "demo-klode-expert.md"},
            {"action": "approve", "status": "done", "detail": "demo-klode enabled"},
        ]}
    code, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert details(body)["approve"] == "demo-klode already enabled"


def test_agent_detail_lists_every_file_comma_separated(tmp_path):
    proj, kb = project(tmp_path)
    (kb / "builds" / A / "agent" / "zz-second.md").write_text(AGENT_TEXT.format(b=A), encoding="utf-8")
    _, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert details(body)["agent"] == "demo-klode-expert.md, zz-second.md"


def test_failed_body_carries_the_config_reason(tmp_path):
    proj, kb = project(tmp_path)
    (proj / ".mcp.json").write_text("{ broken", encoding="utf-8")
    code, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert code == body["exit_code"] == 40 and body["reason"] == Reason.config.value
    assert body["project"] == str(proj)


@pytest.mark.parametrize("content,detail", [
    (b"[1]", ".mcp.json is not a JSON object; left as it is"),
    (b'{"mcpServers": [1]}', ".mcp.json: mcpServers is not an object; left as it is"),
], ids=["not-object", "servers-not-object"])
def test_mcp_refusal_details(tmp_path, content, detail):
    proj, kb = project(tmp_path)
    (proj / ".mcp.json").write_bytes(content)
    _, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert statuses(body)["mcp_json"] == "failed" and details(body)["mcp_json"] == detail
    assert (proj / ".mcp.json").read_bytes() == content


@pytest.mark.parametrize("content", [b"{ broken", b"\xff\xfe"], ids=["syntax", "not-utf8"])
def test_mcp_unreadable_detail_names_the_file_and_the_cause(tmp_path, content):
    proj, kb = project(tmp_path)
    (proj / ".mcp.json").write_bytes(content)
    _, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    d = details(body)["mcp_json"]
    assert d.startswith(".mcp.json cannot be read as JSON (") and d.endswith("); left as it is")
    assert len(d) > len(".mcp.json cannot be read as JSON (); left as it is")


def test_settings_refusal_details(tmp_path):
    proj, kb = project(tmp_path)
    path = proj / ".claude" / "settings.local.json"
    path.parent.mkdir()
    path.write_text('{"enabledMcpjsonServers": "demo-klode"}', encoding="utf-8")
    _, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert details(body)["approve"] == "settings.local.json: enabledMcpjsonServers is not a list; left as it is"
    path.write_text("[]", encoding="utf-8")
    _, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert details(body)["approve"] == "settings.local.json is not a JSON object; left as it is"


def test_agent_missing_detail(tmp_path):
    proj, kb = project(tmp_path)
    for f in (kb / "builds" / A / "agent").glob("*.md"):
        f.rename(tmp_path / f.name)
    _, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert details(body)["agent"] == f"builds/{A}/agent holds no agent file"


def test_oserror_in_an_action_is_reported_with_its_text(tmp_path, monkeypatch):
    proj, kb = project(tmp_path)

    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setitem(R.ACTION_IMPL, "approve", (None, lambda t: boom(None, None)))
    _, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert statuses(body)["approve"] == "failed" and details(body)["approve"] == "disk full"


def test_applier_returning_nothing_reports_an_empty_detail(tmp_path, monkeypatch):
    proj, kb = project(tmp_path)
    monkeypatch.setitem(R.ACTION_IMPL, "approve", (None, lambda t: None))
    _, body = R.run(kb, A, project=proj, env=env_for(tmp_path))
    assert details(body)["approve"] == ""


# --------------------------------------------------------------------------- usage bodies


def test_bad_build_id_body(tmp_path):
    code, body = R.run(tmp_path / "kb" / "demo", "latest")
    assert code == 2
    assert body == {"schema": "klode-kit.register/1", "kb_id": "demo", "build_id": "latest", "exit_code": 2,
                    "reason": "usage", "detail": "'latest' is not a build id"}


def test_missing_build_id_body(tmp_path):
    code, body = R.run(tmp_path / "kb" / "demo", None)
    assert code == 2 and body["build_id"] is None and body["detail"] == "None is not a build id"


def test_no_project_body(tmp_path):
    code, body = R.run(tmp_path / "elsewhere" / "demo", A, env=env_for(tmp_path))
    assert code == 2
    assert body == {"schema": "klode-kit.register/1", "kb_id": "demo", "build_id": A, "exit_code": 2,
                    "reason": "usage", "detail": "no --project and the kb is not below <project>/kb/"}


def test_explicit_project_wins_over_the_default(tmp_path):
    default_proj, kb = project(tmp_path)
    other = tmp_path / "other-project"
    other.mkdir()
    code, body = R.run(kb, A, project=other, env=env_for(tmp_path))
    assert code == 0 and body["project"] == str(other)
    assert (other / ".mcp.json").is_file() and (other / ".claude" / "agents" / "demo-klode-expert.md").is_file()
    assert not (default_proj / ".mcp.json").exists() and not (default_proj / ".claude").exists()


def test_run_reads_the_process_environment_when_none_is_given(tmp_path, monkeypatch):
    proj, kb = project(tmp_path)
    set_env(monkeypatch, tmp_path)
    monkeypatch.setenv("KLODE_KIT_BIN", "/from/process/env")
    code, _ = R.run(kb, A, project=proj)
    assert code == 0
    entry = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["demo-klode"]
    assert entry["command"] == "/from/process/env"
    assert (tmp_path / "reg" / "registry.toml").is_file()


# --------------------------------------------------------------------------- the agent marker (head of 40 lines)


def agent_marker(tmp: Path, content: bytes | None) -> str | None:
    proj, kb = project(tmp)
    dest = proj / ".claude" / "agents" / "demo-klode-expert.md"
    if content is not None:
        dest.parent.mkdir(parents=True)
        dest.write_bytes(content)
    return R.ACTION_IMPL["agent"][0](R.Target(kb, proj, A, env_for(tmp)))


def test_agent_marker_absent_file(tmp_path):
    assert agent_marker(tmp_path, None) is None


def test_agent_marker_found_after_frontmatter(tmp_path):
    assert agent_marker(tmp_path, AGENT_TEXT.format(b=B).encode()) == B


def test_agent_marker_on_line_40_is_read_and_on_line_41_is_not(tmp_path):
    marker = f"<!-- klode-kit build {B} -->\n"
    assert agent_marker(tmp_path / "a", ("x\n" * 39 + marker).encode()) == B
    assert agent_marker(tmp_path / "b", ("x\n" * 40 + marker).encode()) is None


def test_agent_marker_survives_undecodable_bytes_in_the_head(tmp_path):
    assert agent_marker(tmp_path, b"---\nname: \xff\xfe\n---\n" + f"<!-- klode-kit build {B} -->\n".encode()) == B


def test_agent_marker_is_not_assembled_across_a_line_break(tmp_path):
    split = f"---\n<!-- klode-kit build\n {B} -->\n".encode()
    assert agent_marker(tmp_path, split) is None


def test_agent_marker_comment_may_span_lines_where_whitespace_is_allowed(tmp_path):
    spanning = f"---\n<!--\n  klode-kit build {B}\n-->\n".encode()
    assert agent_marker(tmp_path, spanning) == B


def test_agent_marker_without_marker(tmp_path):
    assert agent_marker(tmp_path, b"---\nname: x\n---\nno marker here\n") is None


# --------------------------------------------------------------------------- main(): argparse surface


def test_main_help_exits_zero_and_describes_the_command(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "300")
    assert R.main(["--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: klode-kit register ")
    flat = " ".join(out.split())
    assert flat.endswith(
        "[--project PROJECT] register a published build options: -h, --help show this help message and exit "
        "--kb KB kb root (kb/<id>) --build BUILD build id "
        "--project PROJECT data project (default: the directory above kb/)")


def test_main_missing_build_is_an_argparse_error_not_a_run(capsys):
    assert R.main(["--kb", "x"]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "the following arguments are required: --build" in cap.err
    assert cap.err.startswith("usage: klode-kit register ")


def test_main_missing_kb_is_an_argparse_error_not_a_crash(capsys):
    assert R.main(["--build", A]) == 2
    cap = capsys.readouterr()
    assert cap.out == "" and "the following arguments are required: --kb" in cap.err


def test_main_unknown_option_is_two(capsys):
    assert R.main(["--kb", "x", "--build", A, "--nope"]) == 2
    assert "unrecognized arguments: --nope" in capsys.readouterr().err


def test_main_reads_sys_argv_when_not_given_argv(tmp_path, capsys, monkeypatch):
    proj, kb = project(tmp_path)
    set_env(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["klode-kit-register", "--kb", str(kb), "--build", A, "--project", str(proj)])
    assert R.main() == 0
    assert json.loads(capsys.readouterr().out)["project"] == str(proj)


def test_main_passes_an_explicit_project_through(tmp_path, capsys, monkeypatch):
    _, kb = project(tmp_path)
    other = tmp_path / "chosen"
    other.mkdir()
    set_env(monkeypatch, tmp_path)
    assert R.main(["--kb", str(kb), "--build", A, "--project", str(other)]) == 0
    assert json.loads(capsys.readouterr().out)["project"] == str(other)
    assert (other / ".mcp.json").is_file()


def test_main_stdout_keeps_non_ascii_unescaped(tmp_path, capsys, monkeypatch):
    proj, kb = project(tmp_path, kb_name="资料库")
    set_env(monkeypatch, tmp_path)
    assert R.main(["--kb", str(kb), "--build", A]) == 0
    out = capsys.readouterr().out
    assert "资料库-klode-expert.md" in out and "\\u" not in out
    assert json.loads(out)["kb_id"] == "资料库"
    doc = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    assert list(doc["mcpServers"]) == ["资料库-klode"]


# --------------------------------------------------------------------------- the file is read as UTF-8, whatever the locale


def test_mcp_json_with_non_ascii_is_read_as_utf8_under_the_c_locale(tmp_path):
    """The explicit ``encoding="utf-8"`` of the read: a process with an ASCII locale and no UTF-8 mode still reads it."""
    proj, kb = project(tmp_path)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"其他": {"command": "é"}}}, ensure_ascii=False),
                                    encoding="utf-8")
    assert max((proj / ".mcp.json").read_bytes()) > 127  # really non-ASCII bytes on disk
    (tmp_path / "home").mkdir()
    env = base_env({"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
                    **env_for(tmp_path)}, drop=COVERAGE_VARS)
    proc = subprocess.run([sys.executable, "-m", "klode_kit", "register", "--kb", str(kb), "--build", A,
                           "--project", str(proj)], env=env, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stderr.decode(errors="replace")
    doc = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    assert doc["mcpServers"]["其他"] == {"command": "é"} and "demo-klode" in doc["mcpServers"]
