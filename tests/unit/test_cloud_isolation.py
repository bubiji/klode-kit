"""WI-00c1: cloud argv builder + deterministic isolation review, layer (a) of design 02 r4.3
(matrix 00c1-01 .. 00c1-12, 00c1-18 .. 00c1-20).

Public surface under test:

``klode_kit.ocr.cloud_argv``
- ``prepare_send_dir(png: bytes, der_sha256) -> Path``: a fresh ``klode-kit-send-*`` directory
  directly under ``tempfile.gettempdir()`` (mode 0700) holding exactly ``<der_sha256>.png``,
  ``prompt.txt`` and ``schema.json``; a png whose sha256 is not ``der_sha256`` is a
  ``ValueError`` and creates nothing;
- ``claude(send_dir, *, model, effort, max_budget_usd, source_env=None)`` /
  ``codex(send_dir, *, model, effort, source_env=None)`` -> ``Invocation(route, argv, cwd, env)``;
- ``CLOUD_SETTINGS``: the packaged pinned settings file.

``klode_kit.ocr.isolation_check``
- ``check(invocation, *, project, approved_shas=None) -> Result`` with ``checks`` (``name``,
  ``ok``, ``detail``), ``ok``, ``cli_version``, ``receipt()``;
- check names: ``required:<flag>``, ``forbidden:<flag>``, ``settings:pinned``,
  ``cwd:outside_project``, ``tmpdir:contents``, ``tmpdir:png_approved``, ``env:allowlist``,
  ``cli:version`` (others may exist; the builder output passes all of them);
- ``binding(invocation) -> {"cli_version", "effective_config_sha", "argv_sha"}`` and
  ``receipt_still_valid(receipt, env)``;
- ``CLAUDE_MIN_VERSION == "2.1.285"`` (WI-00c1b: raised from 2.1.259, the cli-reference
  ``--permission-prompts`` floor, to the only version the WI-00c2 host evidence was taken on),
  ``CODEX_MIN_VERSION`` = the locked codex floor.
- ``CLOUD_ENV_ALLOWLIST`` gains ``USER`` (WI-00c1b, user decision 2026-10-23: macOS claude.ai OAuth
  reads the Keychain by it); the pinned settings also carry ``autoMemoryEnabled: false``.

The CLI version is read by running ``<argv[0]> --version`` as resolved on the invocation's own
PATH: the replay fakes in ``tests/fakes/bin`` answer it (paired real path: 00c2-06/07).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from _support.replay import write_scenario
from klode_kit.contract.schema import issues
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC

pytestmark = pytest.mark.unit

FIXTURE_PNG = REPO / "tests" / "fixtures" / "fx-approx-only" / "images" / "p001.png"
FIXTURE_PNG_SHA = "9540907a834d2dd136b20f1364b037871342ba418fca540b98aa88830140a682"
ALLOWLIST = {"PATH", "HOME", "TMPDIR", "LANG", "ANTHROPIC_API_KEY", "USER"}

# r4.3 (a), verbatim: Claude route must-have flags -> required value (None = flag without value,
# ... = any value; checked elsewhere). "-p" is print mode, without which nothing is unattended.
CLAUDE_REQUIRED: dict[str, object] = {
    "-p": None,
    "--restricted": None,
    "--tools": "Read",
    "--strict-mcp-config": None,
    "--permission-mode": "dontAsk",
    "--permission-prompts": "none",
    "--setting-sources": "",
    "--settings": ...,
    "--no-session-persistence": None,
    "--system-prompt": ...,
    "--disable-slash-commands": None,
}
CLAUDE_FORBIDDEN = [
    ("--add-dir", "/"),
    ("--agents", '{"x": {"description": "d", "prompt": "p"}}'),
    ("--plugin-dir", "/tmp/plugin"),
    ("--mcp-config", "mcp.json"),
    ("--dangerously-skip-permissions",),
    ("--bare",),
]
# Codex route: -C <tmp> --ephemeral --ignore-user-config --ignore-rules -c mcp_servers={}
# WI-00c2c (user decision 2026-10-23, plan A): no -s / --sandbox at all - the permission profile
# replaces the legacy sandbox (`-s read-only` read the whole disk, WI-00c2 F3); see test_codex_profile_00c2c.py.
CODEX_REQUIRED: dict[str, object] = {
    "-C": ...,
    "--ephemeral": None,
    "--ignore-user-config": None,
    "--ignore-rules": None,
    "-c mcp_servers={}": None,
}
CODEX_FORBIDDEN = [("--full-auto",), ("--dangerously-bypass-approvals-and-sandbox",)]

HYP = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


# --------------------------------------------------------------------------- fixtures / helpers


@pytest.fixture
def systmp(tmp_path, monkeypatch) -> Path:
    """The 'system TMPDIR' for this test: tempfile.gettempdir() answers this directory."""
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "project"
    p.mkdir()
    return p


@pytest.fixture
def versions(tmp_path, monkeypatch):
    """Replay answers for `claude --version` / `codex --version` (fake CLIs on PATH)."""
    replay = tmp_path / "replay"
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(replay))

    def set_version(tool: str, stdout: str, exit_code: int = 0) -> None:
        write_scenario(replay, tool, ["--version"], stdout, exit_code=exit_code)

    set_version("claude", "2.1.285 (Claude Code)\n")
    set_version("codex", "codex-cli 0.163.0\n")
    return set_version


def build(route: str, png: bytes | None = None, **kw) -> CA.Invocation:
    png = FIXTURE_PNG.read_bytes() if png is None else png
    send = CA.prepare_send_dir(png, hashlib.sha256(png).hexdigest())
    if route == "claude":
        kw.setdefault("model", "opus")
        kw.setdefault("effort", "max")
        kw.setdefault("max_budget_usd", 5.0)
        return CA.claude(send, **kw)
    kw.setdefault("model", "gpt-6-sol")
    kw.setdefault("effort", "xhigh")
    return CA.codex(send, **kw)


def with_argv(inv: CA.Invocation, argv) -> CA.Invocation:
    return dataclasses.replace(inv, argv=tuple(argv))


def results(res) -> dict[str, bool]:
    return {c.name: c.ok for c in res.checks}


def failed(res) -> set[str]:
    return {c.name for c in res.checks if not c.ok}


def flag_index(argv, flag: str) -> int:
    if flag == "-c mcp_servers={}":
        return next(i for i in range(len(argv) - 1) if argv[i] == "-c" and argv[i + 1] == "mcp_servers={}")
    return argv.index(flag)


def drop_group(argv: list[str], flag: str, spec: dict) -> list[str]:
    i = flag_index(argv, flag)
    width = 2 if (spec[flag] is not None or flag == "-c mcp_servers={}") else 1
    return argv[:i] + argv[i + width:]


def sha_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- 00c1-01 / 00c1-02


def test_claude_argv_contract(systmp, project, versions):
    """00c1-01: every r4.3 must-have is present with its exact value; no forbidden flag in any spelling."""
    inv = build("claude")
    argv = list(inv.argv)

    assert inv.route == "claude"
    assert argv[0] == "claude"
    for flag, value in CLAUDE_REQUIRED.items():
        assert argv.count(flag) == 1, flag
        if value is not None and value is not ...:
            assert argv[argv.index(flag) + 1] == value, flag
    settings_path = Path(argv[argv.index("--settings") + 1])
    assert settings_path.resolve() == Path(CA.CLOUD_SETTINGS).resolve()
    for item in CLAUDE_FORBIDDEN:
        assert not any(tok == item[0] or tok.startswith(item[0] + "=") for tok in argv), item[0]

    res = IC.check(inv, project=project)
    assert res.ok, failed(res)
    names = results(res)
    for flag in CLAUDE_REQUIRED:
        assert names[f"required:{flag}"] is True
    for item in CLAUDE_FORBIDDEN:
        assert names[f"forbidden:{item[0]}"] is True


def test_cloud_settings_file_content():
    """00c1-01 (config half): the pinned settings turn the read fence on, deny the rest, wire the guard."""
    data = json.loads(Path(CA.CLOUD_SETTINGS).read_text(encoding="utf-8"))
    assert data["permissions"]["blockReadsOutsideWorkingDirectories"] is True
    assert data["permissions"]["deny"] == ["Bash", "WebFetch", "WebSearch", "Agent", "Edit", "Write", "NotebookEdit"]
    groups = data["hooks"]["PreToolUse"]
    hooks = [h for g in groups if g.get("matcher", "*") in ("*", "") for h in g["hooks"]]
    assert {"type": "command", "command": "klode-kit", "args": ["hook", "isolation-guard"]} in [
        {k: h.get(k) for k in ("type", "command", "args")} for h in hooks
    ]
    assert sha_file(Path(CA.CLOUD_SETTINGS)) == IC.CLOUD_SETTINGS_SHA256
    # WI-00c1b: auto memory is not governed by --setting-sources (docs: agent-sdk/claude-code-features,
    # "What settingSources does not control"); the key is "Any file" scope (docs: settings-reference
    # #automemoryenabled), so the pinned --settings file turns it off.
    assert data.get("autoMemoryEnabled") is False


def test_codex_argv_contract(systmp, project, versions):
    """00c1-02 (changed in WI-00c2c, user decision 2026-10-23): -C <tmp> --ephemeral --ignore-user-config
    --ignore-rules -c mcp_servers={} plus the klode-kit-ocr permission profile and the feature switches;
    no -s / --sandbox in any spelling (a legacy sandbox flag would override the profile)."""
    inv = build("codex")
    argv = list(inv.argv)

    assert inv.route == "codex"
    assert argv[:2] == ["codex", "exec"]
    assert Path(argv[argv.index("-C") + 1]).resolve() == Path(inv.cwd).resolve()
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules"):
        assert argv.count(flag) == 1, flag
    configs = [argv[i + 1] for i, t in enumerate(argv[:-1]) if t == "-c"]
    for exact in ("mcp_servers={}",
                  'default_permissions="klode-kit-ocr"',
                  'permissions.klode-kit-ocr.filesystem={":minimal"="read",":workspace_roots"={"."="read"}}',
                  "permissions.klode-kit-ocr.network.enabled=false",
                  "skills.include_instructions=false", "features.apps=false", "agents.enabled=false",
                  "features.view_image=false", "features.image_generation=false", 'web_search="disabled"',
                  "allow_login_shell=false"):
        assert configs.count(exact) == 1, exact
    assert "--full-auto" not in argv
    assert not any(t in ("-s", "--sandbox") for t in argv)
    assert not any(t.startswith(("--sandbox=", "-s=")) for t in argv)
    assert not any("danger-full-access" in t for t in argv)

    res = IC.check(inv, project=project)
    assert res.ok, failed(res)
    names = results(res)
    for flag in CODEX_REQUIRED:
        assert names[f"required:{flag}"] is True
    assert names["forbidden:--full-auto"] is True


def test_send_dir_shape(systmp):
    """r3.5 / r4.3: fresh dir directly under system TMPDIR, 0700, exactly png + prompt.txt + schema.json."""
    png = FIXTURE_PNG.read_bytes()
    d = CA.prepare_send_dir(png, FIXTURE_PNG_SHA)

    assert d.parent.resolve() == systmp.resolve()
    assert d.name.startswith("klode-kit-send-")
    assert stat.S_IMODE(os.stat(d).st_mode) == 0o700
    assert sorted(p.name for p in d.iterdir()) == sorted([f"{FIXTURE_PNG_SHA}.png", "prompt.txt", "schema.json"])
    assert (d / f"{FIXTURE_PNG_SHA}.png").read_bytes() == png
    assert "/" not in (d / "prompt.txt").read_text(encoding="utf-8")
    json.loads((d / "schema.json").read_text(encoding="utf-8"))


def test_send_dir_rejects_wrong_sha(systmp):
    """A png that does not hash to the claimed der_sha256 is refused before anything is created."""
    with pytest.raises(ValueError):
        CA.prepare_send_dir(b"\x89PNG not the fixture", FIXTURE_PNG_SHA)
    assert list(systmp.iterdir()) == []


@pytest.mark.parametrize("bad", ["-x", "--bare", "", "op us", "a\x00b"])
def test_builder_rejects_flag_like_model(systmp, versions, bad):
    """A model name that could be parsed as a flag (or is empty / has spaces / NUL) is refused."""
    with pytest.raises(ValueError):
        build("claude", model=bad)
    with pytest.raises(ValueError):
        build("codex", model=bad)


@pytest.mark.parametrize("route,bad", [("claude", "ultracode"), ("claude", "minimal"), ("codex", "max"), ("codex", "x")])
def test_builder_rejects_unknown_effort(systmp, versions, route, bad):
    with pytest.raises(ValueError):
        build(route, effort=bad)


# --------------------------------------------------------------------------- 00c1-03


@HYP
@given(
    route=st.sampled_from(["claude", "codex"]),
    model=st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,24}", fullmatch=True),
    effort_ix=st.integers(0, 3),
    png=st.binary(min_size=1, max_size=64),
)
def test_builder_output_always_passes_checker(systmp, project, versions, route, model, effort_ix, png):
    """00c1-03 (S4): whatever the model/effort/tmp name/der_sha, builder output passes every check."""
    effort = (["low", "medium", "high", "max"] if route == "claude" else ["low", "medium", "high", "xhigh"])[effort_ix]
    inv = build(route, png=png, model=model, effort=effort)
    sha = hashlib.sha256(png).hexdigest()

    res = IC.check(inv, project=project, approved_shas=frozenset({sha}))

    assert res.ok, failed(res)
    assert res.checks and all(c.ok for c in res.checks)
    assert res.receipt()["cloud_isolation"] == "PASS"


# --------------------------------------------------------------------------- 00c1-04


@HYP
@given(data=st.data(), route=st.sampled_from(["claude", "codex"]))
def test_required_flag_removal_fails(systmp, project, versions, data, route):
    """00c1-04 (S4): deleting any non-empty subset of must-haves fails, each deleted one named False."""
    spec = CLAUDE_REQUIRED if route == "claude" else CODEX_REQUIRED
    removed = data.draw(st.sets(st.sampled_from(sorted(spec)), min_size=1))
    inv = build(route)
    argv = list(inv.argv)
    for flag in removed:
        argv = drop_group(argv, flag, spec)

    res = IC.check(with_argv(inv, argv), project=project)

    assert not res.ok
    names = results(res)
    for flag in removed:
        assert names[f"required:{flag}"] is False, flag
    assert res.receipt()["cloud_isolation"] == "FAIL"


# --------------------------------------------------------------------------- 00c1-05


@HYP
@given(data=st.data(), route=st.sampled_from(["claude", "codex"]))
def test_forbidden_flag_added_fails(systmp, project, versions, data, route):
    """00c1-05 (S4): one forbidden flag inserted anywhere after the program name -> FAIL naming it."""
    items = CLAUDE_FORBIDDEN if route == "claude" else CODEX_FORBIDDEN
    item = data.draw(st.sampled_from(items))
    inv = build(route)
    argv = list(inv.argv)
    pos = data.draw(st.integers(1, len(argv)))
    argv[pos:pos] = list(item)

    res = IC.check(with_argv(inv, argv), project=project)

    assert not res.ok
    assert results(res)[f"forbidden:{item[0]}"] is False


# --------------------------------------------------------------------------- 00c1-18

CLAUDE_BAD_VALUES = {
    "--tools": ["Read,Bash", "Bash", "", "default", "Read,Glob"],
    "--permission-mode": ["bypassPermissions", "acceptEdits", "auto", "default", "plan"],
    "--permission-prompts": ["host"],
    "--setting-sources": ["user", "project", "user,project,local", "local"],
}
# WI-00c2c: "-s" is no longer a codex flag (now forbidden, see test_codex_profile_00c2c.py), so the codex
# value-mutation case moves to the other valued must-have: -C pointing anywhere but the send dir.
CODEX_BAD_VALUES = {
    "-C": ["/", "/tmp"],
}
CLAUDE_EQ_FORBIDDEN = ["--mcp-config=x.json", "--add-dir=/", "--agents={}", "--plugin-dir=/tmp/p"]


@HYP
@given(data=st.data(), route=st.sampled_from(["claude", "codex"]), kind=st.sampled_from(["value", "duplicate", "eq"]))
def test_required_flag_value_mutation_or_duplicate_fails(systmp, project, versions, data, route, kind):
    """00c1-18 (S4): changed value / trailing same-name duplicate / forbidden as --x=v -> FAIL, named."""
    inv = build(route)
    argv = list(inv.argv)
    bad = CLAUDE_BAD_VALUES if route == "claude" else CODEX_BAD_VALUES
    if kind == "eq" and route == "codex":
        kind = "value"
    if kind == "eq":
        token = data.draw(st.sampled_from(CLAUDE_EQ_FORBIDDEN))
        pos = data.draw(st.integers(1, len(argv)))
        argv[pos:pos] = [token]
        expected = f"forbidden:{token.split('=', 1)[0]}"
    else:
        flag = data.draw(st.sampled_from(sorted(bad)))
        value = data.draw(st.sampled_from(bad[flag]))
        if kind == "value":
            argv[argv.index(flag) + 1] = value
        else:
            argv += [flag, value]
        expected = f"required:{flag}"

    res = IC.check(with_argv(inv, argv), project=project)

    assert not res.ok
    assert results(res)[expected] is False, (kind, argv)


def test_codex_mcp_servers_value_or_extra_config_fails(systmp, project, versions):
    """00c1-18 (codex): -c mcp_servers with a server, or an extra -c sandbox override, fails."""
    inv = build("codex")
    argv = list(inv.argv)
    i = flag_index(argv, "-c mcp_servers={}")
    changed = argv[:i + 1] + ['mcp_servers={x={command="sh"}}'] + argv[i + 2:]
    res = IC.check(with_argv(inv, changed), project=project)
    assert not res.ok and results(res)["required:-c mcp_servers={}"] is False

    extra = argv + ["-c", 'sandbox_mode="danger-full-access"']
    res2 = IC.check(with_argv(inv, extra), project=project)
    assert not res2.ok


def test_unknown_flag_fails(systmp, project, versions):
    """Closed world: a flag the reviewer does not know is a FAIL, not a pass-through."""
    for route, flag in (("claude", "--verbose-everything"), ("codex", "--oss")):
        inv = build(route)
        res = IC.check(with_argv(inv, [*inv.argv[:1], flag, *inv.argv[1:]]), project=project)
        assert not res.ok, route


# --------------------------------------------------------------------------- 00c1-06 / 00c1-07


@pytest.mark.parametrize("route", ["claude", "codex"])
def test_cwd_inside_project_fails(systmp, project, versions, route):
    """00c1-06: cwd = project/sub, or a symlink outside the project pointing inside: both FAIL by realpath."""
    inv = build(route)
    sub = project / "sub"
    sub.mkdir()
    for f in Path(inv.cwd).iterdir():
        (sub / f.name).write_bytes(f.read_bytes())
    res = IC.check(dataclasses.replace(inv, cwd=sub), project=project)
    assert not res.ok
    assert results(res)["cwd:outside_project"] is False

    link = systmp / "klode-kit-send-link"
    link.symlink_to(sub, target_is_directory=True)
    res2 = IC.check(dataclasses.replace(inv, cwd=link), project=project)
    assert not res2.ok
    assert results(res2)["cwd:outside_project"] is False


def test_cwd_containing_project_fails(systmp, project, versions):
    """A cwd that is an ancestor of the project exposes the project as well -> FAIL."""
    inv = build("claude")
    res = IC.check(dataclasses.replace(inv, cwd=project.parent), project=project)
    assert results(res)["cwd:outside_project"] is False
    assert not res.ok


def test_cwd_sibling_prefix_not_inside(tmp_path, systmp, versions):
    """00c1-07: project /x/p, cwd /x/p2 -> not inside (path components, not startswith)."""
    proj = tmp_path / "x" / "p"
    proj.mkdir(parents=True)
    sibling = tmp_path / "x" / "p2"
    sibling.mkdir()
    inv = build("claude")

    res = IC.check(dataclasses.replace(inv, cwd=sibling), project=proj)

    assert results(res)["cwd:outside_project"] is True


# --------------------------------------------------------------------------- 00c1-08


@pytest.mark.parametrize("route", ["claude", "codex"])
@pytest.mark.parametrize("key", ["CLAUDE_CONFIG_DIR", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL", "FOO", "CLAUDE_CODE_SIMPLE",
                                 "LOGNAME"])
def test_env_whitelist(systmp, project, versions, route, key):
    """00c1-08: any key outside PATH HOME TMPDIR LANG ANTHROPIC_API_KEY USER fails; no ANTHROPIC_* wildcard."""
    inv = build(route)
    assert set(inv.env) <= ALLOWLIST
    bad = dataclasses.replace(inv, env={**inv.env, key: "x"})

    res = IC.check(bad, project=project)

    assert not res.ok
    assert failed(res) == {"env:allowlist"}


@pytest.mark.parametrize("route", ["claude", "codex"])
def test_env_builder_keeps_only_whitelist(systmp, project, versions, route):
    """00c1-08 PASS side: builder output carries only explicit whitelist keys and passes."""
    source = {
        "PATH": os.environ["PATH"],
        "HOME": "/home/synthetic",
        "TMPDIR": str(systmp),
        "LANG": "C.UTF-8",
        "ANTHROPIC_API_KEY": "sk-synthetic",
        "ANTHROPIC_BASE_URL": "https://evil.invalid",
        "CLAUDE_CONFIG_DIR": "/tmp/cfg",
        "CODEX_HOME": "/tmp/codex",
        "KLODE_KIT_REPLAY_DIR": "/tmp/r",
    }
    inv = build(route, source_env=source)

    assert set(inv.env) <= ALLOWLIST
    assert inv.env["ANTHROPIC_API_KEY"] == "sk-synthetic"
    assert inv.env["LANG"] == "C.UTF-8"
    res = IC.check(inv, project=project)
    assert res.ok, failed(res)


@pytest.mark.parametrize("route", ["claude", "codex"])
def test_env_user_allowed(systmp, project, versions, route):
    """00c1-08 (WI-00c1b): USER is an allowlisted key - the builder carries it over from the source env,
    LOGNAME next to it is dropped, and an invocation carrying USER passes env:allowlist."""
    source = {"PATH": os.environ["PATH"], "HOME": "/home/synthetic", "TMPDIR": str(systmp), "LANG": "C.UTF-8",
              "USER": "synthetic-user", "LOGNAME": "synthetic-user"}
    inv = build(route, source_env=source)

    assert inv.env.get("USER") == "synthetic-user"
    assert "LOGNAME" not in inv.env
    res = IC.check(inv, project=project)
    assert res.ok, failed(res)
    assert results(res)["env:allowlist"] is True

    changed = dataclasses.replace(inv, env={**inv.env, "USER": "someone-else"})
    assert results(IC.check(changed, project=project))["env:allowlist"] is True


# --------------------------------------------------------------------------- 00c1-09


@pytest.mark.parametrize("route", ["claude", "codex"])
def test_tmpdir_extra_file_or_unlisted_png_fails(systmp, project, versions, route):
    """00c1-09: an extra file in the send dir, or a png whose sha is in no MANIFEST, fails."""
    inv = build(route)
    (Path(inv.cwd) / "CLAUDE.md").write_text("extra context\n", encoding="utf-8")
    res = IC.check(inv, project=project)
    assert not res.ok
    assert results(res)["tmpdir:contents"] is False

    unlisted = build(route, png=b"synthetic bytes listed nowhere")
    res2 = IC.check(unlisted, project=project)
    assert not res2.ok
    assert results(res2)["tmpdir:png_approved"] is False


def test_masked_manifest_approves_png(systmp, project, versions):
    """The data project's _masked/MANIFEST.jsonl der_sha256 list approves a png (r2.2-B)."""
    png = b"synthetic masked derivative"
    sha = hashlib.sha256(png).hexdigest()
    (project / "_masked").mkdir()
    (project / "_masked" / "MANIFEST.jsonl").write_text(
        json.dumps({"der_sha256": sha, "page_id": "d-000000000000/p001"}) + "\n" + "{broken\n", encoding="utf-8"
    )
    res = IC.check(build("claude", png=png), project=project)
    assert res.ok, failed(res)


def test_tmpdir_png_renamed_sha_mismatch_fails(systmp, project, versions):
    """The png's content must hash to its own <sha>.png name."""
    inv = build("claude")
    png = Path(inv.cwd) / f"{FIXTURE_PNG_SHA}.png"
    png.write_bytes(png.read_bytes() + b"\x00")
    res = IC.check(inv, project=project)
    assert not res.ok
    assert results(res)["tmpdir:png_approved"] is False


def test_tmpdir_mode_and_location(systmp, project, versions, tmp_path):
    """A send dir that is group/world readable, or not directly in system TMPDIR, fails."""
    inv = build("claude")
    os.chmod(inv.cwd, 0o755)
    res = IC.check(inv, project=project)
    assert not res.ok
    os.chmod(inv.cwd, 0o700)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(mode=0o700)
    for f in Path(inv.cwd).iterdir():
        (elsewhere / f.name).write_bytes(f.read_bytes())
    res2 = IC.check(dataclasses.replace(inv, cwd=elsewhere), project=project)
    assert not res2.ok


# --------------------------------------------------------------------------- 00c1-10 / 00c1-19


def test_cloud_settings_sha_mismatch_fails(systmp, project, versions, tmp_path):
    """00c1-10: --settings pointing at a copy with one byte changed -> FAIL; packaged file untouched."""
    packaged = Path(CA.CLOUD_SETTINGS)
    before = sha_file(packaged)
    copy = tmp_path / "cloud_settings.json"
    raw = bytearray(packaged.read_bytes())
    i = raw.index(b"true")
    raw[i:i + 4] = b"True"
    copy.write_bytes(bytes(raw))
    inv = build("claude")
    argv = list(inv.argv)
    argv[argv.index("--settings") + 1] = str(copy)

    res = IC.check(with_argv(inv, argv), project=project)

    assert not res.ok
    assert results(res)["settings:pinned"] is False
    assert sha_file(packaged) == before


def test_settings_arg_must_be_pinned_file(systmp, project, versions, tmp_path):
    """00c1-19: the file argv actually points at is judged, not the plugin's pinned path."""
    permissive = tmp_path / "permissive.json"
    permissive.write_text(json.dumps({"permissions": {"allow": ["Bash", "Read(//**)"]}}), encoding="utf-8")
    inv = build("claude")
    argv = list(inv.argv)
    argv[argv.index("--settings") + 1] = str(permissive)

    res = IC.check(with_argv(inv, argv), project=project)

    assert not res.ok
    assert results(res)["settings:pinned"] is False


def test_settings_symlink_to_pinned_file_passes(systmp, project, versions, tmp_path):
    """Judged by realpath: a symlink to the pinned file is the pinned file."""
    link = tmp_path / "s.json"
    link.symlink_to(Path(CA.CLOUD_SETTINGS))
    inv = build("claude")
    argv = list(inv.argv)
    argv[argv.index("--settings") + 1] = str(link)
    res = IC.check(with_argv(inv, argv), project=project)
    assert res.ok, failed(res)


# --------------------------------------------------------------------------- 00c1-11 / 00c1-20


def test_cli_version_boundary(systmp, project, versions):
    """00c1-11 (floor raised to 2.1.285 in WI-00c1b, user decision 2026-10-23): claude 2.1.284 FAIL,
    2.1.285 PASS, 2.1.1000 PASS (numeric, not lexicographic); the old floor 2.1.259 now FAILs."""
    assert IC.CLAUDE_MIN_VERSION == "2.1.285"
    inv = build("claude")
    outcome = {}
    for v in ("2.1.259", "2.1.284", "2.1.285", "2.1.1000", "2.10.0", "10.0.0"):
        versions("claude", f"{v} (Claude Code)\n")
        res = IC.check(inv, project=project)
        outcome[v] = (results(res)["cli:version"], res.ok, res.cli_version)

    assert outcome["2.1.259"] == (False, False, "2.1.259")
    assert outcome["2.1.284"] == (False, False, "2.1.284")
    assert outcome["2.1.285"] == (True, True, "2.1.285")
    assert outcome["2.1.1000"] == (True, True, "2.1.1000")
    assert outcome["2.10.0"][0] is True
    assert outcome["10.0.0"][0] is True


def test_codex_version_boundary(systmp, project, versions):
    """00c1-20: locked floor - 1 FAIL, floor PASS; across digit counts numeric (0.99 < 0.163 < 0.1000)."""
    assert IC.CODEX_MIN_VERSION == "0.163.0"
    inv = build("codex")
    outcome = {}
    for v in ("0.162.0", "0.162.99", "0.163.0", "0.99.0", "0.1000.0", "1.0.0"):
        versions("codex", f"codex-cli {v}\n")
        outcome[v] = results(IC.check(inv, project=project))["cli:version"]

    assert outcome == {"0.162.0": False, "0.162.99": False, "0.163.0": True, "0.99.0": False,
                       "0.1000.0": True, "1.0.0": True}


@pytest.mark.parametrize("stdout,exit_code", [("codex-cli dev\n", 0), ("", 0), ("codex-cli 0.200.0\n", 1)])
def test_version_unreadable_fails(systmp, project, versions, stdout, exit_code):
    """No parseable version, or a failing --version, is a FAIL (fail closed), never a pass."""
    versions("codex", stdout, exit_code)
    res = IC.check(build("codex"), project=project)
    assert results(res)["cli:version"] is False
    assert not res.ok
    assert issues("isolation_receipt", res.receipt()) == []


def test_version_binary_missing_fails(systmp, project, versions):
    """argv[0] not resolvable on the invocation's PATH -> cli:version False."""
    inv = build("claude")
    res = IC.check(dataclasses.replace(inv, env={**inv.env, "PATH": "/nonexistent-dir"}), project=project)
    assert results(res)["cli:version"] is False
    assert not res.ok


# --------------------------------------------------------------------------- 00c1-12


def test_receipt_invalid_on_binding_change(systmp, project, versions):
    """00c1-12: a PASS receipt stays valid only while cli_version, effective config and argv are unchanged."""
    inv = build("claude")
    res = IC.check(inv, project=project)
    receipt = res.receipt()
    assert issues("isolation_receipt", receipt) == []
    assert receipt["cloud_isolation"] == "PASS"
    assert receipt["route"] == "claude"
    assert receipt["cloud_settings_sha"] == IC.CLOUD_SETTINGS_SHA256

    env = IC.binding(inv)
    assert set(env) == {"cli_version", "effective_config_sha", "argv_sha"}
    assert {k: receipt[k] for k in env} == env
    assert IC.receipt_still_valid(receipt, env) is True

    for key, other in (("cli_version", "2.1.286"), ("effective_config_sha", "0" * 64), ("argv_sha", "f" * 64)):
        assert IC.receipt_still_valid(receipt, {**env, key: other}) is False, key

    # the same three changes, produced for real
    versions("claude", "2.1.286 (Claude Code)\n")
    assert IC.receipt_still_valid(receipt, IC.binding(inv)) is False
    versions("claude", "2.1.285 (Claude Code)\n")
    assert IC.receipt_still_valid(receipt, IC.binding(inv)) is True
    assert IC.receipt_still_valid(receipt, IC.binding(dataclasses.replace(inv, env={**inv.env, "LANG": "xx"}))) is False
    argv = list(inv.argv)
    argv[argv.index("--model") + 1] = "sonnet"
    assert IC.receipt_still_valid(receipt, IC.binding(with_argv(inv, argv))) is False


def test_receipt_failed_check_never_valid(systmp, project, versions):
    """A FAIL receipt is never 'still valid', even with an identical binding; its schema forbids PASS."""
    inv = build("claude")
    (Path(inv.cwd) / "extra.txt").write_text("x", encoding="utf-8")
    res = IC.check(inv, project=project)
    receipt = res.receipt()
    assert receipt["cloud_isolation"] == "FAIL"
    assert issues("isolation_receipt", receipt) == []
    assert IC.receipt_still_valid(receipt, IC.binding(inv)) is False
    assert IC.receipt_still_valid({"schema": "nope"}, IC.binding(inv)) is False


def test_receipt_codex_route(systmp, project, versions):
    inv = build("codex")
    receipt = IC.check(inv, project=project).receipt()
    assert issues("isolation_receipt", receipt) == []
    assert receipt["route"] == "codex" and receipt["cli_version"] == "0.163.0"
    assert receipt["cloud_settings_sha"] is None
    assert IC.receipt_still_valid(receipt, IC.binding(inv)) is True
