"""WI-00c2c: Codex route on a permission profile + deterministic host isolation check
(user decision 2026-10-23, plan A; .claude/tdd-guardian/impl-deviations.md, WI-00c2b rows).

Public surface under test:

``klode_kit.ocr.cloud_argv.codex(...)`` - argv carries no ``-s`` / ``--sandbox`` and carries, each
exactly once, the ``-c`` values listed in ``PROFILE_CONFIG`` below (the spike's measured spelling).

``klode_kit.ocr.isolation_check.check(inv, project=...)`` for codex:
- ``required:-c <exact>`` for every entry of ``PROFILE_CONFIG`` (and ``mcp_servers={}``);
- ``forbidden:-s``, ``forbidden:--sandbox``, ``forbidden:--dangerously-bypass-approvals-and-sandbox``,
  ``forbidden:danger-full-access`` (that text in any argv token);
- ``codex_home:agents_md``: ``$HOME/.codex/AGENTS.md`` and ``AGENTS.override.md`` (HOME of the
  invocation env) are each absent or a 0-byte regular file; anything else - non-empty, a directory, a
  dangling or non-empty symlink, no HOME - fails closed. Read-only: the files are never changed.

``klode_kit.ocr.isolation_probe``:
- ``HostExec(returncode, stdout, stderr, timed_out=False)``;
- ``host_check_argv(inv, target)`` -> ``("codex", "sandbox", <every -c pair of inv, same order>,
  "-P", <profile named by default_permissions>, "-C", <inv -C>, "--", "/bin/cat", "--", target)``;
  ``ValueError`` when ``inv`` names no ``default_permissions`` profile;
- ``judge_host(canary_runs, control, *, control_text, nonce) -> HostVerdict`` (``canaries``: path ->
  ``denied | read | enoent | sandbox_error | error``; ``control``: ``ok | sandbox_error | failed``;
  ``verdict``: ``PASS`` iff every canary ``denied`` and control ``ok``). A nested-sandbox failure
  (``sandbox_apply``) is never ``denied``: it says nothing about the profile (spike WI-00c2b);
- ``run_host_check(*, project, canaries, nonce, png, model, effort, source_env, trash_dir, out_dir=None,
  timeout_s=60) -> HostCheckRun`` (``receipt``, ``verdict``, ``argv`` = production argv, ``cleaned``,
  ``trashed``): prepare canaries -> layer (a) review of the production invocation -> only if it
  passed, one ``codex sandbox`` per canary plus one positive control (``<send>/prompt.txt``) ->
  receipt (schema ``isolation_receipt``, ``argv_sha`` of the production argv, ``host_check``
  section, no nonce anywhere) -> canaries and send dir moved to ``trash_dir`` (always). No model call.

Boundary: the ``codex`` child process is a fake script placed first on the invocation PATH (it answers
``--version`` and executes or refuses the ``cat``). Paired real path:
``tests/model/test_codex_host_check.py::test_codex_host_check_real``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit.contract.schema import issues
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import isolation_probe as IP

pytestmark = pytest.mark.unit

FIXTURE_PNG = REPO / "tests" / "fixtures" / "fx-approx-only" / "images" / "p001.png"
SYNTH_PROJECT = REPO / "tests" / "fixtures" / "synth_project"
PROFILE_CONFIG = (
    'default_permissions="klode-kit-ocr"',
    'permissions.klode-kit-ocr.filesystem={":minimal"="read",":workspace_roots"={"."="read"}}',
    "permissions.klode-kit-ocr.network.enabled=false",
    "skills.include_instructions=false",
    "features.apps=false",
    "agents.enabled=false",
    "features.view_image=false",
    "features.image_generation=false",
    'web_search="disabled"',
    "allow_login_shell=false",
)
WIDENED = {
    'default_permissions="klode-kit-ocr"': ['default_permissions=":danger-full-access"', 'default_permissions=":workspace"'],
    'permissions.klode-kit-ocr.filesystem={":minimal"="read",":workspace_roots"={"."="read"}}': [
        'permissions.klode-kit-ocr.filesystem={":root"="read"}',
        'permissions.klode-kit-ocr.filesystem={":minimal"="read",":workspace_roots"={"."="write"}}',
    ],
    "permissions.klode-kit-ocr.network.enabled=false": ["permissions.klode-kit-ocr.network.enabled=true"],
    "skills.include_instructions=false": ["skills.include_instructions=true"],
    "features.apps=false": ["features.apps=true"],
    "agents.enabled=false": ["agents.enabled=true"],
    "features.view_image=false": ["features.view_image=true"],
    "features.image_generation=false": ["features.image_generation=true"],
    'web_search="disabled"': ['web_search="live"', 'web_search="cached"'],
    "allow_login_shell=false": ["allow_login_shell=true"],
}
SANDBOX_INSERTS = [
    (["-s", "read-only"], "forbidden:-s"),
    (["-s", "danger-full-access"], "forbidden:-s"),
    (["--sandbox", "read-only"], "forbidden:--sandbox"),
    (["--sandbox=workspace-write"], "forbidden:--sandbox"),
    (["--dangerously-bypass-approvals-and-sandbox"], "forbidden:--dangerously-bypass-approvals-and-sandbox"),
    (["-c", 'sandbox_mode="danger-full-access"'], "forbidden:danger-full-access"),
]
HYP = settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])


# --------------------------------------------------------------------------- helpers


@pytest.fixture
def systmp(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def home(tmp_path) -> Path:
    h = tmp_path / "home"
    h.mkdir()
    return h


@pytest.fixture
def project(tmp_path) -> Path:
    p = tmp_path / "synth_project"
    shutil.copytree(SYNTH_PROJECT, p)
    return p


FAKE_CODEX = r'''#!{python}
"""Fake codex for WI-00c2c unit tests: --version, and `codex sandbox ... -- /bin/cat -- <path>`."""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
with open(os.path.join(HERE, "calls.jsonl"), "a") as fh:
    fh.write(json.dumps(args) + "\n")
if args == ["--version"]:
    print("codex-cli 0.163.0")
    sys.exit(0)
if not args or args[0] != "sandbox" or "--" not in args or "-C" not in args:
    sys.stderr.write("fake codex: unexpected argv\n")
    sys.exit(97)
mode = open(os.path.join(HERE, "mode")).read().strip()
cmd = args[args.index("--") + 1:]
cd = os.path.realpath(args[args.index("-C") + 1])
target = cmd[-1]
inside = os.path.realpath(target).startswith(cd + os.sep)
if mode == "nested":
    sys.stderr.write("sandbox-exec: sandbox_apply: Operation not permitted\n")
    sys.exit(71)
if mode == "deny" and not inside:
    sys.stderr.write("cat: " + target + ": Operation not permitted\n")
    sys.exit(1)
if mode == "enoent" and not inside:
    sys.stderr.write("cat: " + target + ": No such file or directory\n")
    sys.exit(1)
os.execv(cmd[0], cmd)
'''


@pytest.fixture
def fake_codex(tmp_path) -> Path:
    """A directory holding a fake `codex`; write the behaviour into `<dir>/mode`; calls land in calls.jsonl."""
    d = tmp_path / "fakebin"
    d.mkdir()
    exe = d / "codex"
    exe.write_text(FAKE_CODEX.replace("{python}", sys.executable), encoding="utf-8")
    exe.chmod(0o755)
    (d / "mode").write_text("deny\n", encoding="utf-8")
    return d


def calls(fake: Path) -> list[list[str]]:
    log = fake / "calls.jsonl"
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []


def source_env(home: Path, systmp: Path, fake: Path | None = None) -> dict[str, str]:
    path = os.pathsep.join([*([str(fake)] if fake else []), "/usr/bin", "/bin"])
    return {"PATH": path, "HOME": str(home), "TMPDIR": str(systmp), "LANG": "C.UTF-8", "USER": "synthetic"}


def build_codex(home: Path, systmp: Path, fake: Path | None = None, png: bytes | None = None, **kw) -> CA.Invocation:
    png = FIXTURE_PNG.read_bytes() if png is None else png
    send = CA.prepare_send_dir(png, hashlib.sha256(png).hexdigest())
    kw.setdefault("model", "gpt-6-sol")
    kw.setdefault("effort", "low")
    return CA.codex(send, source_env=source_env(home, systmp, fake), **kw)


def configs(argv) -> list[str]:
    argv = list(argv)
    return [argv[i + 1] for i, t in enumerate(argv[:-1]) if t == "-c"]


def results(res) -> dict[str, bool]:
    return {c.name: c.ok for c in res.checks}


def failed(res) -> set[str]:
    return {c.name for c in res.checks if not c.ok}


def drop_config(argv: list[str], exact: str) -> list[str]:
    i = next(i for i in range(len(argv) - 1) if argv[i] == "-c" and argv[i + 1] == exact)
    return argv[:i] + argv[i + 2:]


# --------------------------------------------------------------------------- argv (cloud_argv + layer a)


def test_codex_argv_carries_profile_and_switches(systmp, home, project, fake_codex):
    """S1: each profile / switch -c value exactly once, no legacy sandbox flag, layer (a) PASS naming each."""
    inv = build_codex(home, systmp, fake_codex)
    argv = list(inv.argv)

    got = configs(argv)
    for exact in ("mcp_servers={}", *PROFILE_CONFIG):
        assert got.count(exact) == 1, exact
    assert not {"-s", "--sandbox"} & set(argv)
    for kept in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "--json"):
        assert argv.count(kept) == 1, kept
    assert argv[argv.index("--output-schema") + 1] == "schema.json"
    assert argv[argv.index("-i") + 1] == f"{hashlib.sha256(FIXTURE_PNG.read_bytes()).hexdigest()}.png"
    assert argv[-1] == (Path(inv.cwd) / "prompt.txt").read_text(encoding="utf-8")

    res = IC.check(inv, project=project)
    assert res.ok, failed(res)
    names = results(res)
    for exact in PROFILE_CONFIG:
        assert names[f"required:-c {exact}"] is True
    for name in ("forbidden:-s", "forbidden:--sandbox", "forbidden:danger-full-access",
                 "forbidden:--dangerously-bypass-approvals-and-sandbox", "codex_home:agents_md"):
        assert names[name] is True, name
    assert "required:-s" not in names


@HYP
@given(data=st.data())
def test_dropping_profile_config_fails(systmp, home, project, fake_codex, data):
    """S4: removing any non-empty subset of the profile / switch -c pairs FAILs, each named False."""
    removed = data.draw(st.sets(st.sampled_from(PROFILE_CONFIG), min_size=1))
    inv = build_codex(home, systmp, fake_codex)
    argv = list(inv.argv)
    for exact in removed:
        argv = drop_config(argv, exact)

    res = IC.check(dataclasses.replace(inv, argv=tuple(argv)), project=project)

    assert not res.ok
    for exact in removed:
        assert results(res)[f"required:-c {exact}"] is False, exact
    assert res.receipt()["cloud_isolation"] == "FAIL"


@HYP
@given(data=st.data(), mode=st.sampled_from(["replace", "duplicate"]))
def test_widening_profile_value_fails(systmp, home, project, fake_codex, data, mode):
    """S4: a widened value in place of, or after, a required -c pair FAILs naming that pair."""
    exact = data.draw(st.sampled_from(sorted(WIDENED)))
    bad = data.draw(st.sampled_from(WIDENED[exact]))
    inv = build_codex(home, systmp, fake_codex)
    argv = list(inv.argv)
    i = argv.index(exact)
    if mode == "replace":
        argv[i] = bad
    else:
        argv[i + 1:i + 1] = ["-c", bad]

    res = IC.check(dataclasses.replace(inv, argv=tuple(argv)), project=project)

    assert not res.ok
    assert results(res)[f"required:-c {exact}"] is False, (exact, bad)


@HYP
@given(data=st.data())
def test_legacy_sandbox_flags_forbidden(systmp, home, project, fake_codex, data):
    """S4: -s / --sandbox (any value, any spelling), the bypass flag or danger-full-access anywhere -> FAIL, named."""
    tokens, name = data.draw(st.sampled_from(SANDBOX_INSERTS))
    inv = build_codex(home, systmp, fake_codex)
    argv = list(inv.argv)
    pos = data.draw(st.integers(2, len(argv) - 1))
    argv[pos:pos] = tokens

    res = IC.check(dataclasses.replace(inv, argv=tuple(argv)), project=project)

    assert not res.ok
    assert results(res)[name] is False, (tokens, pos)


@pytest.mark.parametrize("extra", [
    'permissions.klode-kit-ocr.filesystem.":root"="read"',
    'permissions.klode-kit-ocr.extends=":workspace"',
    'permissions.klode-kit-ocr.workspace_roots."/Users"=true',
    "features.shell_snapshot=true",
    "include_environment_context=true",
])
def test_unlisted_config_key_fails_closed(systmp, home, project, fake_codex, extra):
    """S3: a -c key outside the closed list (e.g. extending the profile) is an argv:closed FAIL."""
    inv = build_codex(home, systmp, fake_codex)
    argv = list(inv.argv)
    argv[-1:-1] = ["-c", extra]

    res = IC.check(dataclasses.replace(inv, argv=tuple(argv)), project=project)

    assert not res.ok
    assert results(res)["argv:closed"] is False


# --------------------------------------------------------------------------- codex_home:agents_md


def _agents_setup(home: Path, case: str, tmp_path: Path) -> None:
    codex = home / ".codex"
    if case == "absent":
        return
    codex.mkdir()
    if case == "empty":
        (codex / "AGENTS.md").write_bytes(b"")
        (codex / "AGENTS.override.md").write_bytes(b"")
    elif case == "nonempty":
        (codex / "AGENTS.md").write_text("reply in Chinese\n", encoding="utf-8")
    elif case == "override_nonempty":
        (codex / "AGENTS.md").write_bytes(b"")
        (codex / "AGENTS.override.md").write_text("x", encoding="utf-8")
    elif case == "directory":
        (codex / "AGENTS.md").mkdir()
    elif case == "dangling_symlink":
        (codex / "AGENTS.md").symlink_to(tmp_path / "missing-target.md")
    elif case == "symlink_nonempty":
        target = tmp_path / "elsewhere.md"
        target.write_text("instructions", encoding="utf-8")
        (codex / "AGENTS.override.md").symlink_to(target)
    else:
        raise AssertionError(case)


@pytest.mark.parametrize("case,ok", [
    ("absent", True), ("empty", True), ("nonempty", False), ("override_nonempty", False),
    ("directory", False), ("dangling_symlink", False), ("symlink_nonempty", False),
])
def test_agents_md_absent_or_empty(systmp, home, project, fake_codex, tmp_path, case, ok):
    """S2/S3: user-level AGENTS.md / AGENTS.override.md must be absent or 0 bytes; the check only reads."""
    _agents_setup(home, case, tmp_path)
    before = {p: (p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
              for p in (home / ".codex").glob("*")} if (home / ".codex").exists() else {}
    inv = build_codex(home, systmp, fake_codex)

    res = IC.check(inv, project=project)

    assert results(res)["codex_home:agents_md"] is ok
    if ok:
        assert res.ok, failed(res)
    else:
        assert failed(res) == {"codex_home:agents_md"}
    after = {p: (p.lstat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
             for p in (home / ".codex").glob("*")} if (home / ".codex").exists() else {}
    assert after == before


def test_agents_md_without_home_fails_closed(systmp, home, project, fake_codex):
    inv = build_codex(home, systmp, fake_codex)
    env = {k: v for k, v in inv.env.items() if k != "HOME"}
    res = IC.check(dataclasses.replace(inv, env=env), project=project)
    assert results(res)["codex_home:agents_md"] is False
    assert not res.ok


# --------------------------------------------------------------------------- host_check_argv


@HYP
@given(model=st.from_regex(r"[A-Za-z0-9][A-Za-z0-9._-]{0,16}", fullmatch=True),
       effort=st.sampled_from(["minimal", "low", "medium", "high", "xhigh"]),
       target=st.from_regex(r"/[a-z]{1,8}(/[a-z0-9._-]{1,8}){0,3}", fullmatch=True))
def test_host_check_argv_reuses_production_config(systmp, home, fake_codex, model, effort, target):
    """S4: for any production invocation the sandbox argv carries exactly its -c list (same order), the
    profile it names, its -C, and runs only `/bin/cat -- <target>`; never exec, never a legacy sandbox flag."""
    inv = build_codex(home, systmp, fake_codex, model=model, effort=effort)

    argv = list(IP.host_check_argv(inv, target))

    assert argv[:2] == ["codex", "sandbox"]
    sep = argv.index("--")
    head, cmd = argv[:sep], argv[sep + 1:]
    assert cmd == ["/bin/cat", "--", target]
    assert configs(head) == configs(inv.argv)
    assert head[head.index("-P") + 1] == "klode-kit-ocr"
    assert head[head.index("-C") + 1] == inv.argv[list(inv.argv).index("-C") + 1]
    assert not {"exec", "-s", "--sandbox", "-p", "--profile"} & set(head)
    assert len(head) == 2 + 2 * len(configs(inv.argv)) + 4


def test_host_check_argv_needs_profile(systmp, home, fake_codex):
    inv = build_codex(home, systmp, fake_codex)
    argv = drop_config(list(inv.argv), 'default_permissions="klode-kit-ocr"')
    with pytest.raises(ValueError):
        IP.host_check_argv(dataclasses.replace(inv, argv=tuple(argv)), "/etc/hosts")


# --------------------------------------------------------------------------- judge_host

NONCE = "nonce-0123456789ab"
CONTROL_TEXT = "Transcribe the image file x.png in the current directory as instructed. Reply with JSON only."
# (returncode, stdout, stderr, timed_out) -> expected outcome; turned into IP.HostExec inside the tests
CANARY_SHAPES = {
    "denied": ((1, "", "cat: /p/canary: Operation not permitted\n", False), "denied"),
    "read": ((0, NONCE + "\n", "", False), "read"),
    "leak_nonzero": ((1, NONCE + "\n", "cat: Operation not permitted\n", False), "read"),
    "enoent": ((1, "", "cat: /p/canary: No such file or directory\n", False), "enoent"),
    "nested": ((71, "", "sandbox-exec: sandbox_apply: Operation not permitted\n", False), "sandbox_error"),
    "timeout": ((None, "", "", True), "error"),
    "silent": ((1, "", "", False), "error"),
}
CONTROL_SHAPES = {
    "ok": ((0, CONTROL_TEXT, "", False), "ok"),
    "nested": ((71, "", "sandbox-exec: sandbox_apply: Operation not permitted\n", False), "sandbox_error"),
    "wrong_text": ((0, "something else", "", False), "failed"),
    "denied": ((1, "", "cat: prompt.txt: Operation not permitted\n", False), "failed"),
    "timeout": ((None, "", "", True), "failed"),
}


def hx(shape) -> "IP.HostExec":
    return IP.HostExec(*shape[0])


@settings(max_examples=150, deadline=None)
@given(shapes=st.lists(st.sampled_from(sorted(CANARY_SHAPES)), min_size=1, max_size=3),
       control=st.sampled_from(sorted(CONTROL_SHAPES)))
def test_judge_host_pass_iff_denied_and_control_ok(shapes, control):
    """S4: PASS exactly when every canary is a sandbox denial and the positive control read the send-dir file."""
    runs = {f"/p{i}/canary-{NONCE}.txt": hx(CANARY_SHAPES[s]) for i, s in enumerate(shapes)}

    v = IP.judge_host(runs, hx(CONTROL_SHAPES[control]), control_text=CONTROL_TEXT, nonce=NONCE)

    assert v.canaries == {p: CANARY_SHAPES[s][1] for p, s in zip(runs, shapes)}
    assert v.control == CONTROL_SHAPES[control][1]
    expect_pass = all(s == "denied" for s in shapes) and control == "ok"
    assert v.verdict == ("PASS" if expect_pass else "FAIL")


def test_judge_host_nested_sandbox_is_not_a_denial():
    """S3 (spike WI-00c2b defect): sandbox_apply failures look like 'Operation not permitted' on every read;
    without a working positive control that must never read as 'host denied'."""
    nested = hx(CANARY_SHAPES["nested"])
    v = IP.judge_host({"/a/c": nested, "/b/c": nested}, nested, control_text=CONTROL_TEXT, nonce=NONCE)
    assert v.verdict == "FAIL"
    assert set(v.canaries.values()) == {"sandbox_error"}
    assert v.control == "sandbox_error"

    denied = hx(CANARY_SHAPES["denied"])
    v2 = IP.judge_host({"/a/c": denied, "/b/c": denied}, nested, control_text=CONTROL_TEXT, nonce=NONCE)
    assert v2.verdict == "FAIL" and v2.control == "sandbox_error"


def test_judge_host_requires_a_canary():
    with pytest.raises(ValueError):
        IP.judge_host({}, hx(CONTROL_SHAPES["ok"]), control_text=CONTROL_TEXT, nonce=NONCE)


# --------------------------------------------------------------------------- run_host_check (fake codex)


def _run(project, home, systmp, fake, tmp_path, *, out_dir=None):
    nonce = IP.new_nonce()
    canaries = [IP.plant_canary(project, nonce), IP.plant_canary(home, nonce)]
    run = IP.run_host_check(project=project, canaries=canaries, nonce=nonce, png=FIXTURE_PNG, model="gpt-6-sol",
                            effort="low", source_env=source_env(home, systmp, fake), trash_dir=tmp_path / "trash",
                            out_dir=out_dir, timeout_s=60)
    return run, canaries, nonce


def test_run_host_check_pass(systmp, home, project, fake_codex, tmp_path):
    """L2·S1: an enforcing sandbox -> both canaries denied, control ok, receipt PASS bound to the production
    argv; only `codex --version` and `codex sandbox` ran (no model); canaries and send dir moved to trash."""
    out = tmp_path / "out"
    run, canaries, nonce = _run(project, home, systmp, fake_codex, tmp_path, out_dir=out)

    r = run.receipt
    assert run.verdict == "PASS"
    assert r["cloud_isolation"] == "PASS", [c for c in r["checks"] if not c["ok"]]
    assert issues("isolation_receipt", r) == []
    assert r["route"] == "codex" and r["cli_version"] == "0.163.0"
    assert list(run.argv[:2]) == ["codex", "exec"] and not {"-s", "--sandbox"} & set(run.argv)
    assert r["argv_sha"] == IC.argv_sha(list(run.argv))
    hc = r["host_check"]
    assert hc["verdict"] == "PASS" and hc["profile"] == "klode-kit-ocr"
    roles = sorted((t["role"], t["outcome"]) for t in hc["targets"])
    assert roles == [("canary", "denied"), ("canary", "denied"), ("control", "ok")]
    assert nonce not in json.dumps(r)

    sandbox_calls = [c for c in calls(fake_codex) if c[:1] == ["sandbox"]]
    assert len(sandbox_calls) == 3
    assert not [c for c in calls(fake_codex) if c[:1] == ["exec"]]
    send = run.argv[list(run.argv).index("-C") + 1]
    assert sorted(c[-1] for c in sandbox_calls) == sorted([str(canaries[0]), str(canaries[1]),
                                                           str(Path(send) / "prompt.txt")])
    for c in sandbox_calls:
        assert configs(c[:c.index("--")]) == configs(run.argv)

    assert not any(c.exists() for c in canaries) and len(run.cleaned) == 2
    assert not Path(send).exists()
    assert run.trashed and all((tmp_path / "trash") in p.parents for p in run.trashed)
    saved = json.loads((out / "isolation_receipt.json").read_text(encoding="utf-8"))
    assert saved == r


@pytest.mark.parametrize("mode,canary_outcome,control", [
    ("nested", "sandbox_error", "sandbox_error"),
    ("open", "read", "ok"),
    ("enoent", "enoent", "ok"),
])
def test_run_host_check_fail_modes(systmp, home, project, fake_codex, tmp_path, mode, canary_outcome, control):
    """L2·S3: a nested-sandbox failure, a sandbox that lets reads through, or a missing canary -> FAIL; the nonce
    a leaking read printed never reaches the receipt."""
    (fake_codex / "mode").write_text(mode, encoding="utf-8")
    run, canaries, nonce = _run(project, home, systmp, fake_codex, tmp_path)

    r = run.receipt
    assert run.verdict == "FAIL"
    assert r["cloud_isolation"] == "FAIL"
    assert issues("isolation_receipt", r) == []
    outcomes = {t["role"]: [] for t in r["host_check"]["targets"]}
    for t in r["host_check"]["targets"]:
        outcomes[t["role"]].append(t["outcome"])
    assert outcomes == {"canary": [canary_outcome, canary_outcome], "control": [control]}
    assert nonce not in json.dumps(r)
    assert not any(c.exists() for c in canaries)


def test_run_host_check_review_failure_runs_nothing(systmp, home, project, fake_codex, tmp_path):
    """S3: layer (a) FAIL (non-empty user AGENTS.md) -> no sandbox process at all, receipt FAIL, cleanup still done."""
    (home / ".codex").mkdir()
    (home / ".codex" / "AGENTS.md").write_text("always answer in Chinese\n", encoding="utf-8")
    run, canaries, _ = _run(project, home, systmp, fake_codex, tmp_path)

    assert run.verdict == "FAIL"
    assert run.receipt["cloud_isolation"] == "FAIL"
    assert {c["name"] for c in run.receipt["checks"] if not c["ok"]} >= {"codex_home:agents_md"}
    assert [c for c in calls(fake_codex) if c[:1] == ["sandbox"]] == []
    assert issues("isolation_receipt", run.receipt) == []
    assert not any(c.exists() for c in canaries)
    assert (home / ".codex" / "AGENTS.md").read_text(encoding="utf-8") == "always answer in Chinese\n"


def test_run_host_check_rejects_bad_canaries_before_anything(systmp, home, project, fake_codex, tmp_path):
    nonce = IP.new_nonce()
    with pytest.raises(IP.CanaryError):
        IP.run_host_check(project=project, canaries=[project / IP.canary_name(nonce)], nonce=nonce, png=FIXTURE_PNG,
                          model="gpt-6-sol", effort="low", source_env=source_env(home, systmp, fake_codex),
                          trash_dir=tmp_path / "trash")
    assert calls(fake_codex) == []
    assert list(systmp.iterdir()) == []


# --------------------------------------------------------------------------- added while implementing (fail-closed edges)


def test_agents_md_unreadable_codex_dir_fails_closed(systmp, home, project, fake_codex):
    """S3: ~/.codex that cannot be inspected (no search permission) is a FAIL, not 'absent'."""
    codex = home / ".codex"
    codex.mkdir()
    (codex / "AGENTS.md").write_bytes(b"")
    inv = build_codex(home, systmp, fake_codex)
    codex.chmod(0)
    try:
        res = IC.check(inv, project=project)
    finally:
        codex.chmod(0o755)
    assert results(res)["codex_home:agents_md"] is False
    assert not res.ok


def test_host_check_argv_config_spellings(systmp, home, fake_codex):
    """--config=<v> counts as a -c pair; an invocation without -C cannot be host-checked."""
    inv = build_codex(home, systmp, fake_codex)
    argv = list(inv.argv)
    i = argv.index('default_permissions="klode-kit-ocr"')
    argv[i - 1:i + 1] = ['--config=default_permissions="klode-kit-ocr"']
    out = IP.host_check_argv(dataclasses.replace(inv, argv=tuple(argv)), "/x")
    assert out[out.index("-P") + 1] == "klode-kit-ocr"
    assert 'default_permissions="klode-kit-ocr"' in configs(out[:out.index("--")])

    j = argv.index("-C")
    with pytest.raises(ValueError):
        IP.host_check_argv(dataclasses.replace(inv, argv=tuple(argv[:j] + argv[j + 2:])), "/x")


def test_run_host_check_timeout_is_fail(systmp, home, project, fake_codex, tmp_path):
    """S3: a sandbox run that does not finish is neither a denial nor a control success."""
    hang = fake_codex / "codex"
    text = hang.read_text(encoding="utf-8").replace(
        'mode = open(os.path.join(HERE, "mode")).read().strip()',
        'mode = open(os.path.join(HERE, "mode")).read().strip()\nimport time\ntime.sleep(30) if mode == "hang" else None')
    hang.write_text(text, encoding="utf-8")
    (fake_codex / "mode").write_text("hang", encoding="utf-8")
    nonce = IP.new_nonce()
    canaries = [IP.plant_canary(project, nonce), IP.plant_canary(home, nonce)]

    run = IP.run_host_check(project=project, canaries=canaries, nonce=nonce, png=FIXTURE_PNG, model="gpt-6-sol",
                            effort="low", source_env=source_env(home, systmp, fake_codex),
                            trash_dir=tmp_path / "trash", timeout_s=1)

    assert run.verdict == "FAIL" and run.receipt["cloud_isolation"] == "FAIL"
    assert sorted(t["outcome"] for t in run.receipt["host_check"]["targets"]) == ["error", "error", "failed"]
    assert all(t["timed_out"] for t in run.receipt["host_check"]["targets"])
    assert issues("isolation_receipt", run.receipt) == []
