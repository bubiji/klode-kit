"""WI-09d: the publish protocol on real, checked builds (matrix 09d-01, 04, 05, 06, 07, 12, 13, 16, 17, 18;
design 02 r3.2 "单一生效指针" / "切换前提" / "外部登记", r4.2 "发布日志" / "replay 规则").

``fx-publish-ab`` is two real builds of ``fx-labs-3x`` (the real ``klode-kit build`` with the real klode, then the real
``kb-check``): the commands under test are the real ``klode-kit kb-publish`` as a subprocess. Laws: after any command
``current`` is sound (K5 - the independent check ``F.sound``), ``PUBLISH.log`` only ever grows, and a refused command
changes nothing (pointer, log, registrations).
"""

from __future__ import annotations

import json
import os
import time
import tomllib
from pathlib import Path

import pytest

from _support import publish_fx as F
from klode_kit import publish as P

pytestmark = pytest.mark.contract_replay


@pytest.fixture()
def ab(tmp_path):
    return F.ab(tmp_path)


def publish(ab, build, *extra, env=None):
    return F.cli(ab.kb, ab.project, ab.tmp, "--build", build, *extra, extra_env=env)


def artifacts(ab) -> dict[str, bytes]:
    """The external registrations as bytes: ``.mcp.json``, the registry, the agent, ``settings.local.json``."""
    out = {}
    for name, path in (("mcp", ab.project / ".mcp.json"), ("registry", F.registry_file(ab.tmp)),
                       ("agent", ab.project / ".claude" / "agents" / f"{ab.kb.name}-klode-expert.md"),
                       ("settings", ab.project / ".claude" / "settings.local.json")):
        if path.is_file():
            out[name] = path.read_bytes()
    return out


def mtimes(ab) -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in (ab.project / ".mcp.json", F.registry_file(ab.tmp),
                                                   ab.project / ".claude" / "agents" /
                                                   f"{ab.kb.name}-klode-expert.md",
                                                   ab.project / ".claude" / "settings.local.json") if p.is_file()}


def lock_registry(ab) -> Path:
    """Make the registry's directory unwritable: the registry action must fail, the others must not."""
    reg = F.registry_file(ab.tmp).parent
    reg.mkdir(parents=True, exist_ok=True)
    reg.chmod(0o555)
    return reg


def unlock_registry(ab) -> None:
    F.registry_file(ab.tmp).parent.chmod(0o755)


def registry_entries(ab) -> list[dict]:
    return tomllib.loads(F.registry_file(ab.tmp).read_text(encoding="utf-8")).get("kb", [])


# --------------------------------------------------------------------------- 09d-01


def test_publish_switches_pointer(ab):
    """09d-01 (L2, S1): publishing A then B moves ``current`` to each; the log gets a pointer row per switch with a
    ``seq`` and ``target_build_id``, B's naming A as the previous build."""
    first = publish(ab, ab.a)
    assert first.returncode == 0, first.stderr.decode()
    assert F.current(ab.kb) == ab.a and os.readlink(ab.kb / "current") == f"builds/{ab.a}"
    switches = [r for r in F.rows(ab.kb) if r["action"] == "switch"]
    assert [(r["seq"], r["target_build_id"], r["prev_build_id"], r["status"]) for r in switches] == [
        (1, ab.a, None, "done")]
    assert F.sound(ab.kb) == []

    second = publish(ab, ab.b)
    assert second.returncode == 0, second.stderr.decode()
    assert F.current(ab.kb) == ab.b
    switches = [r for r in F.rows(ab.kb) if r["action"] == "switch"]
    assert [(r["target_build_id"], r["prev_build_id"]) for r in switches] == [(ab.a, None), (ab.b, ab.a)]
    assert switches[1]["seq"] > switches[0]["seq"]
    seqs = [r["seq"] for r in F.rows(ab.kb)]
    assert seqs == list(range(1, len(seqs) + 1))  # gap-free, increasing
    body = F.summary(second)
    assert (body["exit_code"], body["reason"], body["build_id"], body["prev_build_id"]) == (0, "ok", ab.b, ab.a)
    assert F.sound(ab.kb) == []


def test_publish_writes_the_four_registrations_with_their_markers(ab):
    proc = publish(ab, ab.a)
    assert proc.returncode == 0, proc.stderr.decode()
    server = f"{ab.kb.name}-klode"
    mcp = json.loads((ab.project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][server]
    assert mcp["args"] == ["mcp", "--kb", str(ab.kb)] and mcp["env"] == {"KLODE_KIT_BUILD": ab.a}
    assert Path(mcp["command"]).name == "klode-kit"
    (entry,) = registry_entries(ab)
    assert (entry["id"], entry["build_id"]) == (ab.kb.name, ab.a)
    assert entry["path"] == str(ab.kb / "current" / "library.toml")
    agent = ab.project / ".claude" / "agents" / f"{ab.kb.name}-klode-expert.md"
    assert agent.read_bytes() == (ab.kb / "builds" / ab.a / "agent" / agent.name).read_bytes()
    assert f"<!-- klode-kit build {ab.a} -->" in agent.read_text(encoding="utf-8")
    settings = json.loads((ab.project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert settings["enabledMcpjsonServers"] == [server]
    done = {r["action"]: r["status"] for r in F.rows(ab.kb) if r["target_build_id"] == ab.a}
    assert done == {"switch": "done", "mcp_json": "done", "registry": "done", "agent": "done", "approve": "done"}
    assert [r["action"] for r in F.rows(ab.kb)] == ["switch", "mcp_json", "registry", "agent", "approve"]


def test_publish_keeps_what_was_there(ab):
    """The registrations are read-modify-write: other servers, other registry entries (and their comments), other
    settings keys stay; publishing B updates our entry in place, it does not add a second one."""
    (ab.project / ".mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "extra": 1}),
                                          encoding="utf-8")
    (ab.project / ".claude").mkdir()
    (ab.project / ".claude" / "settings.local.json").write_text(
        json.dumps({"permissions": {"allow": ["Read"]}, "enabledMcpjsonServers": ["other"]}), encoding="utf-8")
    reg = F.registry_file(ab.tmp)
    reg.parent.mkdir(parents=True)
    reg.write_text('# my libraries\n[[kb]]\nid = "other-kb"\npath = "/somewhere/else"\n', encoding="utf-8")
    assert publish(ab, ab.a).returncode == 0
    assert publish(ab, ab.b).returncode == 0
    mcp = json.loads((ab.project / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["other"] == {"command": "x"} and mcp["extra"] == 1
    assert mcp["mcpServers"][f"{ab.kb.name}-klode"]["env"] == {"KLODE_KIT_BUILD": ab.b}
    settings = json.loads((ab.project / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert settings["permissions"] == {"allow": ["Read"]}
    assert settings["enabledMcpjsonServers"] == ["other", f"{ab.kb.name}-klode"]  # appended once, not twice
    text = reg.read_text(encoding="utf-8")
    assert text.startswith("# my libraries\n")
    entries = {e["id"]: e for e in registry_entries(ab)}
    assert set(entries) == {"other-kb", ab.kb.name}
    assert entries["other-kb"]["path"] == "/somewhere/else"
    assert entries[ab.kb.name]["build_id"] == ab.b


def test_publish_is_idempotent(ab):
    assert publish(ab, ab.a).returncode == 0
    before_log, before_art, before_times = F.log_bytes(ab.kb), artifacts(ab), mtimes(ab)
    again = publish(ab, ab.a)
    assert again.returncode == 0, again.stderr.decode()
    assert F.log_bytes(ab.kb) == before_log  # nothing new to say
    assert artifacts(ab) == before_art and mtimes(ab) == before_times
    assert F.summary(again)["reason"] == "ok"


def test_a_broken_registration_file_fails_its_action_and_stays_as_it_was(ab):
    (ab.project / ".mcp.json").write_text("{ not json", encoding="utf-8")
    proc = publish(ab, ab.a)
    assert proc.returncode == 21, proc.stderr.decode()
    assert (ab.project / ".mcp.json").read_text(encoding="utf-8") == "{ not json"
    status = {r["action"]: r["status"] for r in F.rows(ab.kb)}
    assert status["mcp_json"] == "failed" and status["registry"] == "done" and status["approve"] == "done"
    failed = [r for r in F.rows(ab.kb) if r["status"] == "failed"]
    assert failed and failed[0].get("detail")
    body = F.summary(proc)
    assert body["reason"] == "post_publish_failed"
    assert [a["status"] for a in body["actions"] if a["action"] == "mcp_json"] == ["failed"]


def test_an_unwritable_agents_directory_fails_the_agent_and_approve_actions_only(ab):
    (ab.project / ".claude").write_text("not a directory", encoding="utf-8")  # .claude/agents cannot be made
    proc = publish(ab, ab.a)
    assert proc.returncode == 21, proc.stderr.decode()
    status = {r["action"]: r["status"] for r in F.rows(ab.kb)}
    assert status == {"switch": "done", "mcp_json": "done", "registry": "done", "agent": "failed",
                      "approve": "failed"}
    assert F.current(ab.kb) == ab.a and F.sound(ab.kb) == []
    assert (ab.project / ".claude").read_text(encoding="utf-8") == "not a directory"


def test_publish_receipt_file(ab):
    out = ab.tmp / "receipt" / "publish_receipt.json"
    proc = publish(ab, ab.a, "--receipt", str(out))
    assert proc.returncode == 0
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert (doc["build_id"], doc["prev_build_id"], doc["exit_code"], doc["reason"]) == (ab.a, None, 0, "ok")
    assert [a["action"] for a in doc["actions"]] == ["switch", *P.ACTIONS]
    assert F.summary(proc) == doc


# --------------------------------------------------------------------------- 09d-04 / 09d-05 / 09d-06 / 09d-07


def test_stale_replay_exit22(ab):
    """09d-04 (L2, S3): ``current`` is B, replay of A is refused with exit 22 and nothing is written."""
    assert publish(ab, ab.a).returncode == 0 and publish(ab, ab.b).returncode == 0
    art, log = artifacts(ab), F.log_bytes(ab.kb)
    proc = F.cli(ab.kb, ab.project, ab.tmp, "--replay", ab.a)
    assert proc.returncode == 22, proc.stderr.decode()
    assert F.summary(proc)["reason"] == "stale_replay"
    assert artifacts(ab) == art and F.log_bytes(ab.kb) == log
    assert F.current(ab.kb) == ab.b


def test_replay_of_a_build_that_was_never_current_is_stale_too(ab):
    proc = F.cli(ab.kb, ab.project, ab.tmp, "--replay", ab.a)  # no current at all
    assert proc.returncode == 22
    assert F.rows(ab.kb) == [] and not (ab.project / ".mcp.json").exists()


def test_replay_skips_newer_marker(ab):
    """09d-05 (L2, S3): an artifact whose marker names a build published *later* (by ``seq``) is left alone and the
    action is recorded ``skipped_newer``; the other, failed-before action of the same replay still runs."""
    lock_registry(ab)
    failed = publish(ab, ab.a)
    assert failed.returncode == 21  # the registry action failed: A has an action to replay
    unlock_registry(ab)
    reg = F.registry_file(ab.tmp)
    reg.parent.mkdir(exist_ok=True)
    reg.write_text(f'[[kb]]\nid = "{ab.kb.name}"\npath = "elsewhere"\nbuild_id = "{ab.b}"\n', encoding="utf-8")
    top = max(r["seq"] for r in F.rows(ab.kb))
    F.write_rows(ab.kb, F.published_rows(top + 1, ab.b, prev=ab.a))  # B was published later - elsewhere, in full
    kept = reg.read_bytes()
    proc = F.cli(ab.kb, ab.project, ab.tmp, "--replay", ab.a)
    assert proc.returncode == 0, proc.stderr.decode()
    assert reg.read_bytes() == kept
    last = F.by_action(ab.kb, ab.a, "registry")[-1]
    assert last["status"] == "skipped_newer" and last["seq"] > top
    assert [a for a in F.summary(proc)["actions"] if a["action"] == "registry"][0]["status"] == "skipped_newer"
    assert P.pending_builds(F.rows(ab.kb)) == set()  # skipped is settled


def test_replay_only_failed_actions(ab):
    """09d-06 (L2, S3): ``mcp_json`` is done and ``registry`` failed - the replay rewrites only the registry; the
    other artifacts keep their bytes *and* their modification time."""
    lock_registry(ab)
    assert publish(ab, ab.a).returncode == 21
    unlock_registry(ab)
    art, times = artifacts(ab), mtimes(ab)
    n = len(F.rows(ab.kb))
    proc = F.cli(ab.kb, ab.project, ab.tmp, "--replay", ab.a)
    assert proc.returncode == 0, proc.stderr.decode()
    after = artifacts(ab)
    assert after["mcp"] == art["mcp"] and after["agent"] == art["agent"] and after["settings"] == art["settings"]
    assert {k: v for k, v in mtimes(ab).items() if k != "registry.toml"} == {
        k: v for k, v in times.items() if k != "registry.toml"}
    (entry,) = registry_entries(ab)
    assert entry["build_id"] == ab.a
    new = F.rows(ab.kb)[n:]
    assert [(r["action"], r["status"]) for r in new] == [("registry", "done")]
    assert P.pending_builds(F.rows(ab.kb)) == set()
    again = F.cli(ab.kb, ab.project, ab.tmp, "--replay", ab.a)  # nothing left: a no-op
    assert again.returncode == 0 and len(F.rows(ab.kb)) == n + 1


def test_replay_runs_the_actions_that_never_got_a_row(ab):
    """A publication that died after the pointer row: the actions without a row are open and replay does them."""
    F.write_rows(ab.kb, [F.row(1, ab.a, "switch")])
    os.symlink(f"builds/{ab.a}", ab.kb / "current")
    proc = F.cli(ab.kb, ab.project, ab.tmp, "--replay", ab.a)
    assert proc.returncode == 0, proc.stderr.decode()
    assert [r["action"] for r in F.rows(ab.kb)[1:]] == list(P.ACTIONS)
    assert F.sound(ab.kb) == []


def test_post_publish_failure_exit21_pointer_kept(ab):
    """09d-07 (L2, S3): a registry path that cannot be written - exit 21, the pointer already points to the new build
    (the library is self-contained and can be used directly), and it stays sound."""
    assert publish(ab, ab.a).returncode == 0
    lock_registry(ab)
    proc = publish(ab, ab.b)
    unlock_registry(ab)
    assert proc.returncode == 21, proc.stderr.decode()
    assert F.current(ab.kb) == ab.b and F.sound(ab.kb) == []
    body = F.summary(proc)
    assert (body["reason"], body["exit_code"]) == ("post_publish_failed", 21)
    assert [(r["action"], r["status"]) for r in F.rows(ab.kb) if r["target_build_id"] == ab.b] == [
        ("switch", "done"), ("mcp_json", "done"), ("registry", "failed"), ("agent", "done"), ("approve", "done")]
    assert P.pending_builds(F.rows(ab.kb)) == {ab.b}


# --------------------------------------------------------------------------- 09d-12 / 09d-13 / 09d-18


def test_rollback(ab):
    """09d-12 (L2, S1): B is published, A is the previous build; ``--rollback-to A`` points back and replays *all*
    registrations to A; the log says ``rollback``."""
    assert publish(ab, ab.a).returncode == 0 and publish(ab, ab.b).returncode == 0
    n = len(F.rows(ab.kb))
    proc = F.cli(ab.kb, ab.project, ab.tmp, "--rollback-to", ab.a)
    assert proc.returncode == 0, proc.stderr.decode()
    assert F.current(ab.kb) == ab.a and F.sound(ab.kb) == []
    new = F.rows(ab.kb)[n:]
    assert [(r["action"], r["target_build_id"], r["status"]) for r in new] == [
        ("rollback", ab.a, "done"), ("mcp_json", ab.a, "done"), ("registry", ab.a, "done"), ("agent", ab.a, "done"),
        ("approve", ab.a, "done")]
    assert new[0]["prev_build_id"] == ab.b
    mcp = json.loads((ab.project / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"][f"{ab.kb.name}-klode"]
    assert mcp["env"] == {"KLODE_KIT_BUILD": ab.a}
    assert registry_entries(ab)[0]["build_id"] == ab.a
    agent = ab.project / ".claude" / "agents" / f"{ab.kb.name}-klode-expert.md"
    assert f"<!-- klode-kit build {ab.a} -->" in agent.read_text(encoding="utf-8")
    assert F.summary(proc)["reason"] == "ok"


def test_rollback_refuses_what_publish_refuses(ab):
    assert publish(ab, ab.a).returncode == 0
    F.set_check_receipt(ab.kb / "builds" / ab.b, status="FAIL")
    log, art = F.log_bytes(ab.kb), artifacts(ab)
    proc = F.cli(ab.kb, ab.project, ab.tmp, "--rollback-to", ab.b)
    assert proc.returncode == 20
    assert F.current(ab.kb) == ab.a and F.log_bytes(ab.kb) == log and artifacts(ab) == art
    gone = F.cli(ab.kb, ab.project, ab.tmp, "--rollback-to", F.build_id(7))
    assert gone.returncode == 30 and F.current(ab.kb) == ab.a


def test_replay_rollback_share_lock(ab):
    """09d-13 (L2, S3): a replay and a rollback at the same time - one of them gets exit 40 - and the end state is the
    one a serial run gives."""
    assert publish(ab, ab.a).returncode == 0 and publish(ab, ab.b).returncode == 0
    control = F.ab(ab.tmp / "control")
    assert publish(control, control.a).returncode == 0 and publish(control, control.b).returncode == 0
    serial = F.cli(control.kb, control.project, control.tmp, "--rollback-to", control.a)
    assert serial.returncode == 0

    pause = ab.tmp / "pause"
    pause.mkdir()
    held = F.popen(ab.kb, ab.project, ab.tmp, "--rollback-to", ab.a,
                   extra_env={"KLODE_KIT_PAUSE_AT": "publish.lock", "KLODE_KIT_PAUSE_DIR": str(pause)})
    try:
        reached = pause / "publish.lock.reached"
        for _ in range(600):
            if reached.exists():
                break
            if held.poll() is not None:
                raise AssertionError(held.communicate())
            time.sleep(0.05)
        assert reached.exists()
        busy = F.cli(ab.kb, ab.project, ab.tmp, "--replay", ab.b)
        assert busy.returncode == 40, busy.stderr.decode()
        assert F.summary(busy)["reason"] == "lock_busy"
        assert F.current(ab.kb) == ab.b  # the holder has not moved anything yet
        (pause / "publish.lock.go").write_text("", encoding="utf-8")
        out, err = held.communicate(timeout=120)
        assert held.returncode == 0, err.decode()
    finally:
        if held.poll() is None:
            held.kill()
    assert F.current(ab.kb) == F.current(control.kb) == ab.a

    def strip(kb):
        return [{k: v for k, v in r.items() if k != "at"} for r in F.rows(kb)]

    assert strip(ab.kb) == strip(control.kb)
    assert F.sound(ab.kb) == []


def test_skipped_newer_uses_publish_seq(ab):
    """09d-18 (L2, S3): A, B (its id sorts after A's), then a rollback to A whose ``.mcp.json`` action fails; the file
    still carries B's marker, but B was published *before* the rollback, so the replay of A rewrites it - nothing is
    ``skipped_newer`` (judging by build id would have skipped it)."""
    assert ab.b > ab.a
    assert publish(ab, ab.a).returncode == 0 and publish(ab, ab.b).returncode == 0
    mcp = ab.project / ".mcp.json"
    stale = mcp.read_text(encoding="utf-8")  # B's marker
    mcp.write_text("{ broken", encoding="utf-8")
    proc = F.cli(ab.kb, ab.project, ab.tmp, "--rollback-to", ab.a)
    assert proc.returncode == 21, proc.stderr.decode()
    assert F.current(ab.kb) == ab.a
    mcp.write_text(stale, encoding="utf-8")  # fixed by hand - back to the marker of B
    assert f'"KLODE_KIT_BUILD": "{ab.b}"' in stale or ab.b in stale
    n = len(F.rows(ab.kb))
    replay = F.cli(ab.kb, ab.project, ab.tmp, "--replay", ab.a)
    assert replay.returncode == 0, replay.stderr.decode()
    new = F.rows(ab.kb)[n:]
    assert [(r["action"], r["status"]) for r in new] == [("mcp_json", "done")]
    assert not [r for r in F.rows(ab.kb) if r["status"] == "skipped_newer"]
    server = json.loads(mcp.read_text(encoding="utf-8"))["mcpServers"][f"{ab.kb.name}-klode"]
    assert server["env"] == {"KLODE_KIT_BUILD": ab.a}


# --------------------------------------------------------------------------- 09d-16 / 09d-17


def refuse_unchanged(ab, build, code, fragment):
    """A publish of ``build`` is refused: exit ``code``, the message names ``fragment``; ``current``, the log and every
    registration are as they were."""
    before = (F.current(ab.kb), F.log_bytes(ab.kb), artifacts(ab))
    proc = publish(ab, build)
    assert proc.returncode == code, proc.stderr.decode()
    body = F.summary(proc)
    assert body["exit_code"] == code and any(fragment in p for p in body["problems"]), body
    assert fragment in proc.stderr.decode()
    assert (F.current(ab.kb), F.log_bytes(ab.kb), artifacts(ab)) == before
    assert not [r for r in F.rows(ab.kb) if r["target_build_id"] == build]  # no row of any kind for the refused build
    assert not os.path.lexists(ab.kb / "current.tmp")


def test_publish_requires_check_pass(ab):
    """09d-16 (L2, S3): a ``check_receipt`` with ``status`` FAIL is not published (exit 20)."""
    assert publish(ab, ab.a).returncode == 0
    F.set_check_receipt(ab.kb / "builds" / ab.b, status="FAIL")
    refuse_unchanged(ab, ab.b, 20, "FAIL")


def test_a_warn_receipt_is_not_a_pass_either(ab):
    F.set_check_receipt(ab.kb / "builds" / ab.a, status="WARN")
    refuse_unchanged(ab, ab.a, 30, "WARN")


def _unlink_check(b):
    (b / "check_receipt.json").unlink()


def _other_id(b):
    path = b / "check_receipt.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["build_id"] = F.build_id(5)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _tamper(b):
    card = sorted((b / "library").rglob("*.txt"))[0]
    card.write_text(card.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")


def _writable_top(b):
    os.chmod(b, 0o755)


def _writable_inner(b):
    os.chmod(sorted((b / "library").rglob("*.txt"))[0], 0o644)


def _no_manifest(b):
    (b / "MANIFEST.sha256").unlink()


def _no_build_receipt(b):
    (b / "build_receipt.json").unlink()


def _failed_build_receipt(b):
    path = b / "build_receipt.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["status"] = "FAIL"
    path.write_text(json.dumps(doc), encoding="utf-8")


def _invalid_check_receipt(b):
    (b / "check_receipt.json").write_text("{}", encoding="utf-8")


# (id, tamper, the tamper is a permission change on the sealed tree (no unlock needed), the message names ...)
CASES = [
    ("1-no-check-receipt", _unlink_check, False, "check_receipt.json"),
    ("2-check-names-another-build", _other_id, False, "names"),
    ("3-manifest-mismatch", _tamper, False, "MANIFEST"),
    ("4-top-dir-writable", _writable_top, True, "writable"),
    ("5-inner-file-writable", _writable_inner, True, "writable"),
    ("6-no-manifest", _no_manifest, False, "MANIFEST.sha256"),
    ("7-no-build-receipt", _no_build_receipt, False, "build_receipt.json"),
    ("8-build-receipt-fail", _failed_build_receipt, False, "build_receipt"),
    ("9-check-receipt-invalid", _invalid_check_receipt, False, "check_receipt.json"),
]


@pytest.mark.parametrize("make,on_sealed,fragment", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_publish_rejects_unverified_target(ab, make, on_sealed, fragment):
    """09d-17 (L2, S3): the six ways of the matrix a build can fail the verification before it is pointed to, and
    three more (no build receipt - which ``kb-check`` only WARNs about - a failed one, an unreadable check receipt):
    each is refused, ``current`` stays, no row is written."""
    assert publish(ab, ab.a).returncode == 0
    build = ab.kb / "builds" / ab.b
    if on_sealed:
        make(build)
    else:
        F.edit(build, make)
    refuse_unchanged(ab, ab.b, 30, fragment)


def test_publish_unknown_or_trashed_build(ab):
    assert publish(ab, ab.a).returncode == 0
    unknown = publish(ab, F.build_id(7))
    assert unknown.returncode == 30 and "no such build" in unknown.stderr.decode()
    assert F.cli(ab.kb, ab.project, ab.tmp, "--prune", "--keep", "0").returncode == 0
    assert F.trash(ab.kb) == [ab.b]  # B is no build any more
    gone = publish(ab, ab.b)
    assert gone.returncode == 30 and F.current(ab.kb) == ab.a


def test_usage_errors(ab):
    none = F.cli(ab.kb, ab.project, ab.tmp)
    assert none.returncode == 2
    two = F.cli(ab.kb, ab.project, ab.tmp, "--build", ab.a, "--replay", ab.a)
    assert two.returncode == 2
    bad = F.cli(ab.kb, ab.project, ab.tmp, "--build", "../../etc")
    assert bad.returncode == 2
    nokb = F.cli(ab.tmp / "no-such-kb", ab.project, ab.tmp, "--build", ab.a)
    assert nokb.returncode == 2
    keep = F.cli(ab.kb, ab.project, ab.tmp, "--prune", "--keep", "-1")
    assert keep.returncode == 2
    assert F.rows(ab.kb) == [] and F.current(ab.kb) is None
