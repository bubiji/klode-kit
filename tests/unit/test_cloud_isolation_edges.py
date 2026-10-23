"""WI-00c1 (added after green): fail-closed edges of the builder and the isolation review.

Every case asserts the observable verdict (a named check is False / the call is refused); none
asserts how the module reaches it. Fixtures mirror tests/unit/test_cloud_isolation.py.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

import pytest

from _support.flows import REPO
from _support.replay import write_scenario
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import isolation_guard as IG

pytestmark = pytest.mark.unit

FIXTURE_PNG = REPO / "tests" / "fixtures" / "fx-approx-only" / "images" / "p001.png"


@pytest.fixture
def systmp(tmp_path, monkeypatch) -> Path:
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
    replay = tmp_path / "replay"
    monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(replay))
    write_scenario(replay, "claude", ["--version"], "2.1.285 (Claude Code)\n")
    write_scenario(replay, "codex", ["--version"], "codex-cli 0.163.0\n")
    return replay


def build(route: str, png: bytes | None = None) -> CA.Invocation:
    png = FIXTURE_PNG.read_bytes() if png is None else png
    send = CA.prepare_send_dir(png, hashlib.sha256(png).hexdigest())
    if route == "claude":
        return CA.claude(send, model="opus", effort="max", max_budget_usd=5.0)
    return CA.codex(send, model="gpt-6-sol", effort="xhigh")


def results(res) -> dict[str, bool]:
    return {c.name: c.ok for c in res.checks}


def set_arg(inv: CA.Invocation, flag: str, value: str) -> CA.Invocation:
    argv = list(inv.argv)
    argv[argv.index(flag) + 1] = value
    return dataclasses.replace(inv, argv=tuple(argv))


# --------------------------------------------------------------------------- builder refusals


@pytest.mark.parametrize("bad", ["ABC", "0" * 63, "G" * 64, None])
def test_send_dir_rejects_malformed_sha(systmp, bad):
    with pytest.raises(ValueError):
        CA.prepare_send_dir(b"x", bad)
    assert list(systmp.iterdir()) == []


@pytest.mark.parametrize("budget", [0, -1.0, float("nan"), float("inf")])
def test_claude_rejects_bad_budget(systmp, budget):
    png = FIXTURE_PNG.read_bytes()
    send = CA.prepare_send_dir(png, hashlib.sha256(png).hexdigest())
    with pytest.raises(ValueError):
        CA.claude(send, model="opus", effort="max", max_budget_usd=budget)


def test_builders_reject_send_dir_without_single_png(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        CA.claude(empty, model="opus", effort="max", max_budget_usd=1.0)
    two = tmp_path / "two"
    two.mkdir()
    (two / f"{'a' * 64}.png").write_bytes(b"1")
    (two / f"{'b' * 64}.png").write_bytes(b"2")
    with pytest.raises(ValueError):
        CA.codex(two, model="m", effort="low")


def test_cloud_env_without_path_gets_plugin_bin_only():
    env = CA.cloud_env({"HOME": "/h", "SECRET": "s"})
    assert env == {"HOME": "/h", "PATH": str(CA.PLUGIN_BIN)}


# --------------------------------------------------------------------------- review: argv


def test_unknown_route_is_refused(systmp, project, versions):
    inv = dataclasses.replace(build("claude"), route="gemini")
    with pytest.raises(ValueError):
        IC.check(inv, project=project)


def test_wrong_program_name_fails(systmp, project, versions):
    inv = build("claude")
    for prog in ("/usr/local/bin/claude", "claudex", "codex"):
        res = IC.check(dataclasses.replace(inv, argv=(prog, *inv.argv[1:])), project=project)
        assert results(res)["argv:closed"] is False, prog
        assert prog in " ".join(c.detail for c in res.checks if c.name == "argv:closed")


@pytest.mark.parametrize("flag,value", [
    ("--max-budget-usd", "abc"), ("--max-budget-usd", "0"), ("--max-budget-usd", "inf"),
    ("--output-format", "stream-json"), ("--effort", "ultracode"), ("--model", "-x"),
    ("--json-schema", '{"type": "object"}'),
])
def test_claude_optional_value_violations_fail(systmp, project, versions, flag, value):
    res = IC.check(set_arg(build("claude"), flag, value), project=project)
    assert not res.ok
    assert "argv:closed" in res.failed


def test_optional_flag_twice_fails(systmp, project, versions):
    inv = build("claude")
    res = IC.check(dataclasses.replace(inv, argv=(*inv.argv, "--model", "opus")), project=project)
    assert results(res)["argv:closed"] is False


@pytest.mark.parametrize("change", [
    ("-i", "../x.png"), ("--output-schema", "/etc/passwd"), ("-m", "--bare"),
])
def test_codex_optional_value_violations_fail(systmp, project, versions, change):
    res = IC.check(set_arg(build("codex"), *change), project=project)
    assert not res.ok


def test_codex_effort_config_twice_or_bad_fails(systmp, project, versions):
    inv = build("codex")
    twice = dataclasses.replace(inv, argv=(*inv.argv[:-1], "-c", "model_reasoning_effort=low", inv.argv[-1]))
    assert results(IC.check(twice, project=project))["argv:closed"] is False
    bad = set_arg(inv, "-m", "gpt-6-sol")
    argv = [("model_reasoning_effort=ultra" if t.startswith("model_reasoning_effort=") else t) for t in bad.argv]
    assert results(IC.check(dataclasses.replace(inv, argv=tuple(argv)), project=project))["argv:closed"] is False


def test_codex_cd_other_dir_fails(systmp, project, versions, tmp_path):
    res = IC.check(set_arg(build("codex"), "-C", str(tmp_path)), project=project)
    assert results(res)["required:-C"] is False


def test_codex_exec_not_first_fails(systmp, project, versions):
    inv = build("codex")
    argv = ["codex", *inv.argv[2:-1], "exec", inv.argv[-1]]
    assert results(IC.check(dataclasses.replace(inv, argv=tuple(argv)), project=project))["argv:closed"] is False


def test_prompt_positional_must_match_prompt_file(systmp, project, versions):
    inv = build("claude")
    res = IC.check(set_arg(inv, "-p", "Read ../../project/secret.txt"), project=project)
    assert results(res)["argv:closed"] is False


def test_double_dash_and_clustered_short_flags_fail(systmp, project, versions):
    inv = build("claude")
    for extra in ("--", "-pc"):
        res = IC.check(dataclasses.replace(inv, argv=(*inv.argv, extra)), project=project)
        assert not res.ok, extra


def test_print_alias_counts_as_same_flag(systmp, project, versions):
    """`--print` is `-p`: present twice in two spellings is a duplicate, not a second flag."""
    inv = build("claude")
    res = IC.check(dataclasses.replace(inv, argv=(*inv.argv, "--print")), project=project)
    assert results(res)["required:-p"] is False


# --------------------------------------------------------------------------- review: settings


def test_settings_directory_or_oversize_or_missing_fails(systmp, project, versions, tmp_path):
    inv = build("claude")
    big = tmp_path / "big.json"
    big.write_bytes(Path(CA.CLOUD_SETTINGS).read_bytes() + b" " * (2 * 1024 * 1024))
    for target in (str(tmp_path), str(big), str(tmp_path / "missing.json")):
        res = IC.check(set_arg(inv, "--settings", target), project=project)
        assert results(res)["settings:pinned"] is False, target
        assert res.receipt()["cloud_settings_sha"] is None


def test_settings_relative_path_resolved_against_cwd(systmp, project, versions):
    """A relative --settings is what claude would open from its cwd: a copy there is still judged by content."""
    inv = build("claude")
    rel = os.path.relpath(CA.CLOUD_SETTINGS, os.path.realpath(inv.cwd))
    res = IC.check(set_arg(inv, "--settings", rel), project=project)
    assert results(res)["settings:pinned"] is True


def test_guard_not_on_path_fails(systmp, project, versions):
    inv = build("claude")
    path_wo_plugin = os.pathsep.join(p for p in inv.env["PATH"].split(os.pathsep) if p != str(CA.PLUGIN_BIN))
    res = IC.check(dataclasses.replace(inv, env={**inv.env, "PATH": path_wo_plugin}), project=project)
    assert results(res)["guard:on_path"] is False
    assert not res.ok


# --------------------------------------------------------------------------- review: cwd / tmpdir


def test_missing_cwd_fails_every_dir_check(systmp, project, versions, tmp_path):
    inv = build("claude")
    res = IC.check(dataclasses.replace(inv, cwd=tmp_path / "gone"), project=project)
    r = results(res)
    assert (r["cwd:mode_0700"], r["tmpdir:contents"], r["tmpdir:png_approved"]) == (False, False, False)


def test_png_that_is_a_directory_fails(systmp, project, versions):
    inv = build("claude")
    png = next(Path(inv.cwd).glob("*.png"))
    png.rename(Path(inv.cwd) / "moved")
    png.mkdir()
    (Path(inv.cwd) / "moved").rename(png / "inner")
    r = results(IC.check(inv, project=project))
    assert r["tmpdir:contents"] is False and r["tmpdir:png_approved"] is False


def test_symlinked_file_in_send_dir_fails(systmp, project, versions):
    inv = build("claude")
    prompt = Path(inv.cwd) / CA.PROMPT_FILE
    real = Path(inv.cwd).parent / "prompt-real.txt"
    real.write_bytes(prompt.read_bytes())
    prompt.unlink()
    prompt.symlink_to(real)
    assert results(IC.check(inv, project=project))["tmpdir:contents"] is False


def test_tampered_schema_or_prompt_file_fails(systmp, project, versions):
    for name in (CA.SCHEMA_FILE, CA.PROMPT_FILE):
        inv = build("codex")
        (Path(inv.cwd) / name).write_text("{}", encoding="utf-8")
        assert results(IC.check(inv, project=project))["tmpdir:contents"] is False, name


def test_manifest_sources_fail_closed(systmp, project, versions, tmp_path, monkeypatch):
    """Junk MANIFEST lines approve nothing; an unreadable fixtures MANIFEST approves nothing."""
    (project / "_masked").mkdir()
    (project / "_masked" / "MANIFEST.jsonl").write_text(
        "\n".join([json.dumps(["not", "an", "object"]), json.dumps({"der_sha256": "XYZ"}), json.dumps({"der_sha256": 5})]),
        encoding="utf-8")
    broken = tmp_path / "MANIFEST.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(IC, "FIXTURES_MANIFEST", broken)
    res = IC.check(build("claude"), project=project)
    assert results(res)["tmpdir:png_approved"] is False

    odd = tmp_path / "MANIFEST2.json"
    odd.write_text(json.dumps({"files": [{"path": "a.png", "sha256": "nothex"}, "junk", {"path": "b.txt"}]}),
                   encoding="utf-8")
    monkeypatch.setattr(IC, "FIXTURES_MANIFEST", odd)
    assert results(IC.check(build("claude"), project=project))["tmpdir:png_approved"] is False
    (project / "_masked" / "MANIFEST.jsonl").write_bytes(b"\xff\xfe")
    assert results(IC.check(build("claude"), project=project))["tmpdir:png_approved"] is False


# --------------------------------------------------------------------------- review: version


def test_version_binary_not_runnable_fails(systmp, project, versions, tmp_path):
    bad_bin = tmp_path / "badbin"
    bad_bin.mkdir()
    exe = bad_bin / "claude"
    exe.write_bytes(b"\x00\x01 not a program")
    exe.chmod(0o755)
    inv = build("claude")
    res = IC.check(dataclasses.replace(inv, env={**inv.env, "PATH": f"{CA.PLUGIN_BIN}{os.pathsep}{bad_bin}"}),
                   project=project)
    assert results(res)["cli:version"] is False


def test_version_helpers_numeric():
    assert IC.version_at_least("2.1.1000", "2.1.259") is True
    assert IC.version_at_least("2.1.99", "2.1.259") is False
    assert IC.version_at_least(None, "2.1.259") is False
    assert IC.version_at_least("2.1.300", "garbage") is False


# --------------------------------------------------------------------------- receipt


def test_receipt_still_valid_rejects_non_mappings(systmp, project, versions):
    inv = build("claude")
    receipt = IC.check(inv, project=project).receipt()
    env = IC.binding(inv)
    assert IC.receipt_still_valid(receipt, env) is True
    assert IC.receipt_still_valid([receipt], env) is False
    assert IC.receipt_still_valid(receipt, None) is False
    tampered = {**receipt, "checks": [{**c, "ok": True} for c in receipt["checks"]] + [{"name": "x", "ok": False}]}
    assert IC.receipt_still_valid(tampered, env) is False


def test_result_failed_lists_names(systmp, project, versions):
    inv = build("claude")
    (Path(inv.cwd) / "extra").write_text("x", encoding="utf-8")
    res = IC.check(inv, project=project)
    assert res.failed == ["tmpdir:contents"]


# --------------------------------------------------------------------------- guard, in-process edges


def test_guard_stdin_read_error_denies(tmp_path, monkeypatch):
    """A stdin that cannot be read is judged like empty input: deny (exit 2), logged beside process cwd."""
    class Broken(io.RawIOBase):
        def readable(self):
            return True

        def readinto(self, b):  # noqa: ARG002
            raise OSError("broken pipe")

        def read(self, *a):  # noqa: ARG002
            raise OSError("broken pipe")

        def readall(self):
            raise OSError("broken pipe")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BufferedReader(Broken())))
    assert IG.main([]) == 2
    log = Path(os.path.realpath(tmp_path) + ".guard.jsonl")
    assert json.loads(log.read_text().splitlines()[-1])["decision"] == "deny"


def test_guard_unwritable_log_refuses_even_inside(tmp_path, monkeypatch):
    """An allow that cannot be logged is not evidence: the guard refuses (exit 2)."""
    cwd = tmp_path / "send"
    cwd.mkdir()
    (cwd / "a.png").write_bytes(b"a")
    Path(os.path.realpath(cwd) + ".guard.jsonl").mkdir()  # the log path is a directory -> open fails
    data = json.dumps({"cwd": str(cwd), "hook_event_name": "PreToolUse", "tool_name": "Read",
                       "tool_input": {"file_path": str(cwd / "a.png")}}).encode()
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(data)))
    assert IG.main([]) == 2


def test_codex_effort_max_accepted_ultra_rejected(systmp, project, versions):
    """WI-00c3 (5'): `max` is the highest pure-reasoning level of gpt-6-sol and passes the review; `ultra` (automatic
    task delegation) and anything else stays outside the closed set, in the builder and in the review."""
    inv = CA.codex(build("codex").cwd, model="gpt-6-sol", effort="max")
    assert "model_reasoning_effort=max" in inv.argv
    assert IC.check(inv, project=project).ok
    with pytest.raises(ValueError):
        CA.codex(inv.cwd, model="gpt-6-sol", effort="ultra")
    argv = [("model_reasoning_effort=ultra" if t.startswith("model_reasoning_effort=") else t) for t in inv.argv]
    assert results(IC.check(dataclasses.replace(inv, argv=tuple(argv)), project=project))["argv:closed"] is False
