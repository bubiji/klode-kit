"""WI-05b: ``privacy_guard`` as the plugin's PreToolUse hook (matrix 05b-12, 05b-13; design 02 r2.2-B "第二道闸").

The hook process is started exactly as ``hooks/hooks.json`` declares it (exec form: ``command`` + ``args``,
``${CLAUDE_PLUGIN_ROOT}`` replaced by the repository root, https://code.claude.com/docs/en/hooks "Exec form and
shell form"), with the PreToolUse event on stdin and ``CLAUDE_PROJECT_DIR`` in the environment
(https://code.claude.com/docs/en/hooks, "Reference scripts by path").

Decision: for every ``claude`` / ``codex`` simple command in ``tool_input.command``, every image it names
(``-i``/``--image`` values, image-looking tokens or file names inside tokens, resolved against ``-C``/``--cd`` or
the event ``cwd``) must exist, lie outside any ``_mask_review`` directory and hash to a registered sha
(``mask.read_manifest(<project>)`` or the fixtures MANIFEST) -> exit 0; otherwise exit 2 with the reason on stderr.
Commands that are not cloud calls pass (exit 0). Unparseable input fails closed (exit 2).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from _support.flows import REPO, base_env
from _support.send_fake import FIXTURE_PNG, masked_project

pytestmark = pytest.mark.contract_replay

HOOKS = REPO / "hooks" / "hooks.json"


def hook_argv() -> list[str]:
    data = json.loads(HOOKS.read_text(encoding="utf-8"))
    handlers = [h for group in data["hooks"]["PreToolUse"] for h in group["hooks"]]
    argvs = {(h["command"], tuple(h["args"])) for h in handlers}
    assert len(argvs) == 1, argvs
    command, args = argvs.pop()
    sub = lambda s: s.replace("${CLAUDE_PLUGIN_ROOT}", str(REPO))  # noqa: E731
    return [sub(command), *(sub(a) for a in args)]


def run_hook(project: Path, command, *, cwd: Path | None = None, raw: bytes | None = None):
    event = {"session_id": "s", "hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": command}, "cwd": str(cwd or project)}
    stdin = raw if raw is not None else json.dumps(event).encode("utf-8")
    env = base_env({"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(REPO)})
    return subprocess.run(hook_argv(), input=stdin, env=env, capture_output=True, timeout=60, cwd=str(cwd or project))


@pytest.fixture
def project(tmp_path):
    return masked_project(tmp_path / "project")


def test_hook_subprocess_unlisted_exit2(project, tmp_path):
    """05b-12 (S3): ``codex exec -i <unregistered image>`` -> exit 2, the reason names the problem, not the path."""
    proj, _ = project
    stray = proj / "_inbox" / "stray.png"
    stray.write_bytes(b"\x89PNG unregistered")

    res = run_hook(proj, f"codex exec -i {shlex.quote(str(stray))} 'read it'")

    assert res.returncode == 2
    assert b"not registered" in res.stderr


def test_hook_listed_allows(project):
    """05b-13 (S1): the same command with a registered masked image -> exit 0."""
    proj, row = project
    img = proj / "_masked" / row["path"]
    res = run_hook(proj, f"codex exec -i {shlex.quote(str(img))} 'read it'")
    assert res.returncode == 0, res.stderr.decode()


@pytest.mark.parametrize("command", [
    "codex exec -C {d} -i {name} --json 'x'",
    "cd {d} && codex exec --image={name} 'x'",
    "claude -p 'Transcribe the image file {name} in the current directory'",
    "FOO=1 codex exec -i {d}/{name} 'x'",
    "echo start; codex exec -i {d}/{name},{d}/{name} 'x'",
])
def test_hook_resolves_names_against_cwd_and_C(project, tmp_path, command):
    """Boundary: relative names are resolved against ``-C`` / a preceding ``cd`` / the event cwd; a registered
    image passes in each spelling, the unregistered twin next to it is refused in the same spelling."""
    proj, row = project
    d = tmp_path / "send"
    d.mkdir()
    good = (proj / "_masked" / row["path"]).read_bytes()
    (d / "good.png").write_bytes(good)
    (d / "bad.png").write_bytes(good + b"\0")

    ok = run_hook(proj, command.format(d=shlex.quote(str(d)), name="good.png"), cwd=d)
    bad = run_hook(proj, command.format(d=shlex.quote(str(d)), name="bad.png"), cwd=d)

    assert ok.returncode == 0, ok.stderr.decode()
    assert bad.returncode == 2


def test_hook_review_dir_rejected(project):
    """A registered sha inside ``_mask_review/`` is still refused (the directory is never sendable)."""
    proj, row = project
    copy = proj / "_mask_review" / "copy.png"
    copy.write_bytes((proj / "_masked" / row["path"]).read_bytes())
    res = run_hook(proj, f"codex exec -i {shlex.quote(str(copy))} x")
    assert res.returncode == 2
    assert b"_mask_review" in res.stderr


def test_hook_fixture_image_allowed(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    res = run_hook(empty, f"codex exec -i {shlex.quote(str(FIXTURE_PNG))} x")
    assert res.returncode == 0, res.stderr.decode()


@pytest.mark.parametrize("command", ["ls -la", "git status && echo claude", "python -c 'print(1)'",
                                     "echo codex exec -i x.png"])
def test_hook_non_cloud_commands_pass(project, command):
    proj, _ = project
    assert run_hook(proj, command).returncode == 0


@pytest.mark.parametrize("raw", [b"{", b"", b"[]", json.dumps({"hook_event_name": "PreToolUse"}).encode(),
                                 json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                             "tool_input": {"command": 5}, "cwd": "/"}).encode()])
def test_hook_malformed_fail_closed(project, raw):
    proj, _ = project
    assert run_hook(proj, "", raw=raw).returncode == 2


@pytest.mark.parametrize("command", ["codex exec -i 'unterminated", "codex exec -i missing.png x",
                                     "claude -p 'read gone.png'"])
def test_hook_unverifiable_fails_closed(project, command):
    """Unbalanced quotes, or a named image that does not exist -> exit 2 (nothing to verify)."""
    proj, _ = project
    assert run_hook(proj, command).returncode == 2


def test_hook_corrupt_manifest_fails_closed(project):
    proj, row = project
    manifest = proj / "_masked" / "MANIFEST.jsonl"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "{broken\n{}\n", encoding="utf-8")
    res = run_hook(proj, f"codex exec -i {shlex.quote(str(proj / '_masked' / row['path']))} x")
    assert res.returncode == 2


def test_hook_project_from_event_cwd_without_env(project):
    """Without ``CLAUDE_PROJECT_DIR`` the event ``cwd`` is the project."""
    proj, row = project
    event = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(proj),
             "tool_input": {"command": f"codex exec -i {shlex.quote(str(proj / '_masked' / row['path']))} x"}}
    env = base_env({}, drop=("CLAUDE_PROJECT_DIR",))
    res = subprocess.run(hook_argv(), input=json.dumps(event).encode(), env=env, capture_output=True, timeout=60)
    assert res.returncode == 0, res.stderr.decode()
