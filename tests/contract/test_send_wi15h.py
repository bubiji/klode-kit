"""WI-15h: ``ocr/send`` pinned exactly - the cases that start a process (companion of ``tests/unit/test_send_wi15h.py``).

Covered here, with the fake cloud CLIs of ``_support.send_fake`` (0 real model calls) or a plain Python child:
what a sent receipt records from the review (CLI version, argv sha, check names), the explicit ``config`` argument,
the review refusal's reason / detail and the fields it leaves in its receipt, the whitelist handed to the review
(fixture-registered image through a project without a MANIFEST), ``_execute`` (stdin is /dev/null, output decoded
as UTF-8 with replacement also when the child timed out and had printed nothing or half a line), and the stored
isolation receipt read as UTF-8 when the process locale is ASCII.

Boundary: ``FakeCloud`` / a Python child process; paired real path: model lane 06-11/12 (unchanged).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from _support.send_fake import (
    FIXTURE_PNG,
    VERSIONS,
    FakeCloud,
    isolation_receipt,
    masked_project,
    routes_state,
    sha256,
    source_env,
)
from klode_kit import config as config_mod
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import send as S

pytestmark = pytest.mark.contract_replay

ROUTES = ("claude", "codex")


@pytest.fixture
def systmp(tmp_path, monkeypatch) -> Path:
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def rig(tmp_path, systmp):
    home = tmp_path / "home"
    home.mkdir()
    fake = FakeCloud(tmp_path / "fake")
    project, row = masked_project(tmp_path / "project")
    state = routes_state(tmp_path / "routes.json")
    env = source_env(fake, home, systmp)
    iso = {}

    def isolation(route):
        if route not in iso:
            other = FakeCloud(tmp_path / "fake-iso")
            iso[route] = isolation_receipt(route, other, home, tmp_path / "iso", tmp_path / f"iso-{route}.json")
        return iso[route]

    class Rig:
        pass

    r = Rig()
    r.tmp, r.home, r.fake, r.project, r.row, r.state, r.env = tmp_path, home, fake, project, row, state, env
    r.systmp, r.receipts, r.iso = systmp, tmp_path / "receipts", isolation
    r.masked = project / "_masked" / row["path"]

    def send(route="claude", path=None, sha=None, **kw):
        kw.setdefault("project", project)
        kw.setdefault("receipt_dir", r.receipts)
        kw.setdefault("isolation_receipt", isolation(route))
        kw.setdefault("routes_state", state)
        kw.setdefault("source_env", env)
        return S.send(path or r.masked, sha or row["der_sha256"], route=route, **kw)

    r.send = send
    return r


# --------------------------------------------------------------------------- what a sent receipt records


@pytest.mark.parametrize("route", ROUTES)
def test_sent_receipt_records_review_results_and_versions(rig, route):
    res = rig.send(route)
    [call] = rig.fake.model_calls()
    rec = res.receipt
    assert rec["status"] == "sent" and rec["exit_code"] == 0 and rec["reason"] is None
    assert rec["cli_version"] == VERSIONS[route]
    assert rec["argv_sha"] == IC.argv_sha((route, *call["argv"]))
    assert len(rec["argv_sha"]) == 64
    assert rec["isolation_receipt_sha"] == sha256(rig.iso(route).read_bytes())
    assert rec["review"] and all(set(c) == {"name", "ok"} and c["ok"] is True for c in rec["review"])
    assert {"cli:version", "env:allowlist", "effort:configured"} <= {c["name"] for c in rec["review"]}
    assert (rec["model"], rec["effort"]) == (
        config_mod.load(rig.project)[f"{route}.model"], config_mod.load(rig.project)[f"{route}.effort"])
    assert rec["der_sha256"] == rig.row["der_sha256"] == res.der_sha256
    assert rec["expect_sha256"] == rig.row["der_sha256"]
    assert rec["timed_out"] is False
    assert json.loads(res.receipt_path.read_text(encoding="utf-8")) == rec
    assert res.receipt_path.name == f"{route}-{rig.row['der_sha256']}.1.json"
    assert res.returncode == 0 and res.timed_out is False
    assert res.stdout == rig.fake.state["stdout"] and res.stderr == ""


@pytest.mark.parametrize("route", ROUTES)
def test_explicit_config_is_the_one_used(rig, route):
    """``config=`` wins over the project's file: model, effort and timeout come from it."""
    cfg = config_mod.resolve({f"{route}.model": "model-from-argument", f"{route}.effort": "high"})
    assert cfg[f"{route}.model"] != config_mod.load(rig.project)[f"{route}.model"]
    res = rig.send(route, config=cfg)
    [call] = rig.fake.model_calls()
    assert (res.receipt["model"], res.receipt["effort"]) == ("model-from-argument", "high")
    assert "model-from-argument" in call["argv"]
    assert res.receipt["status"] == "sent"


def test_stdout_and_stderr_of_the_child_are_returned_decoded(rig):
    rig.fake.set(stdout='{"type": "result", "result": "héllo 患者"}\n', exit_code=4)
    res = rig.send("codex")
    assert res.returncode == 4 and res.receipt["exit_code"] == 4 and res.receipt["status"] == "sent"
    assert res.stdout == '{"type": "result", "result": "héllo 患者"}\n'


def test_budget_argument_beats_the_configured_batch_cap(rig):
    rig.send("claude", max_budget_usd=0.25)
    [call] = rig.fake.model_calls()
    i = call["argv"].index("--max-budget-usd")
    assert float(call["argv"][i + 1]) == 0.25


# --------------------------------------------------------------------------- the review refuses


def test_review_failure_reason_detail_and_receipt_fields(rig, tmp_path):
    """RR-7: a data project whose real path is under /tmp (a symlink: nothing is created there)."""
    link = tmp_path / "tmp-link"
    link.symlink_to("/tmp")
    proj = link / f"klode-kit-wi15h-absent-{os.getpid()}"
    data = FIXTURE_PNG.read_bytes()
    with pytest.raises(S.SendRefused) as info:
        rig.send("claude", path=FIXTURE_PNG, sha=sha256(data), project=proj)
    exc = info.value
    assert exc.exit_code == 61 and exc.reason == "isolation_review_failed"
    [rp] = list(rig.receipts.glob("*.json"))
    rec = json.loads(rp.read_text(encoding="utf-8"))
    failed = [c["name"] for c in rec["review"] if not c["ok"]]
    assert failed == ["project:outside_shared_tmp"]
    assert exc.detail == ", ".join(failed) == "project:outside_shared_tmp"
    assert str(exc) == "isolation_review_failed: project:outside_shared_tmp"
    assert (rec["status"], rec["exit_code"], rec["reason"]) == ("refused", 61, "isolation_review_failed")
    assert rec["cli_version"] == VERSIONS["claude"]
    assert len(rec["argv_sha"]) == 64
    assert rec["isolation_receipt_sha"] == sha256(rig.iso("claude").read_bytes())
    assert (rec["model"], rec["effort"]) == (config_mod.load()["claude.model"], config_mod.load()["claude.effort"])
    assert rec["der_sha256"] == sha256(data) and rec["bytes"] == len(data)
    assert rig.fake.model_calls() == []
    assert list(rig.systmp.iterdir()) == []


def test_review_failure_with_several_failed_checks_lists_them_joined_by_comma_space(rig, tmp_path, monkeypatch):
    link = tmp_path / "tmp-link"
    link.symlink_to("/tmp")
    proj = link / f"klode-kit-wi15h-absent2-{os.getpid()}"
    rig.iso("claude")  # built with the real review, before it is made to fail
    real = IC.check

    def worse(inv, **kw):
        res = real(inv, **kw)
        bad = IC.Check("extra:synthetic", False, "")
        return IC.Result(res.route, (*res.checks, bad), res.cli_version, res.argv_sha, res.effective_config_sha,
                         res.cloud_settings_sha, res.cwd)

    monkeypatch.setattr(IC, "check", worse)
    data = FIXTURE_PNG.read_bytes()
    with pytest.raises(S.SendRefused) as info:
        rig.send("claude", path=FIXTURE_PNG, sha=sha256(data), project=proj)
    assert info.value.detail == "project:outside_shared_tmp, extra:synthetic"


def test_stale_stored_receipt_refuses_before_any_model_call(rig):
    iso = rig.iso("codex")
    data = json.loads(iso.read_text(encoding="utf-8"))
    data["cli_version"] = "0.0.1"
    iso.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(S.SendRefused) as info:
        rig.send("codex")
    assert info.value.reason == "isolation_receipt_stale"
    assert info.value.detail == f"receipt cli 0.0.1, now {VERSIONS['codex']}"
    assert rig.fake.model_calls() == []
    [rp] = list(rig.receipts.glob("*.json"))
    rec = json.loads(rp.read_text(encoding="utf-8"))
    assert (rec["status"], rec["exit_code"], rec["reason"]) == ("refused", 61, "isolation_receipt_stale")


# --------------------------------------------------------------------------- the whitelist handed to the review


def test_review_gets_the_same_whitelist_the_sender_checked(rig, monkeypatch):
    seen = {}
    real = IC.check

    def spy(inv, **kw):
        seen.update(kw)
        return real(inv, **kw)

    monkeypatch.setattr(IC, "check", spy)
    rig.send("claude")
    assert seen["approved_shas"] == S.approved_shas(rig.project)
    assert rig.row["der_sha256"] in seen["approved_shas"]
    assert seen["expected_effort"] == config_mod.load(rig.project)["claude.effort"]
    assert seen["refuse_shared_tmp"] is True
    assert seen["project"] == rig.project


@pytest.mark.parametrize("route", ROUTES)
def test_fixture_registered_image_is_sent_from_a_project_without_a_manifest(rig, tmp_path, route):
    """The synthetic fixtures MANIFEST is on the whitelist; the review must be given it too (the project's own
    MANIFEST does not list this image), or ``tmpdir:png_approved`` would fail."""
    proj = tmp_path / "bare-project"
    proj.mkdir()
    data = FIXTURE_PNG.read_bytes()
    res = rig.send(route, path=FIXTURE_PNG, sha=sha256(data), project=proj)
    assert res.receipt["status"] == "sent"
    assert all(c["ok"] for c in res.receipt["review"])
    assert len(rig.fake.model_calls()) == 1


# --------------------------------------------------------------------------- _execute


def py(code: str, **env) -> CA.Invocation:
    return CA.Invocation("claude", (sys.executable, "-c", textwrap.dedent(code)), Path.cwd(),
                         {"PATH": "/usr/bin:/bin", **env})


def test_execute_child_gets_dev_null_on_stdin(tmp_path):
    r, w = os.pipe()
    os.write(w, b"LEAKED FROM THE SENDER'S STDIN\n")
    os.close(w)
    saved = os.dup(0)
    try:
        os.dup2(r, 0)
        out = S._execute(py("import sys; print(repr(sys.stdin.read()))"), 30)
    finally:
        os.dup2(saved, 0)
        os.close(saved)
        os.close(r)
    assert out == ("''\n", "", 0, False)


def test_execute_returns_decoded_output_and_the_exit_code():
    inv = py("""
        import sys
        sys.stdout.buffer.write("héllo 患者\\n".encode("utf-8"))
        sys.stderr.buffer.write("fehler ü\\n".encode("utf-8"))
        sys.exit(3)
    """)
    assert S._execute(inv, 30) == ("héllo 患者\n", "fehler ü\n", 3, False)


def test_execute_replaces_undecodable_bytes():
    inv = py("""
        import sys
        sys.stdout.buffer.write(b"a\\xffb")
        sys.stderr.buffer.write(b"c\\xfed")
    """)
    assert S._execute(inv, 30) == ("a�b", "c�d", 0, False)


def test_execute_timeout_without_output_gives_empty_strings_and_no_code():
    out = S._execute(py("import time; time.sleep(60)"), 0.5)
    assert out == ("", "", None, True)


def test_execute_timeout_keeps_what_was_printed_and_decodes_it_with_replacement():
    inv = py("""
        import sys, time
        sys.stdout.buffer.write(b"half a line \\xff")
        sys.stdout.flush()
        sys.stderr.buffer.write(b"warn \\xfe")
        sys.stderr.flush()
        time.sleep(60)
    """)
    out, err, code, timed_out = S._execute(inv, 1.5)
    assert (out, err, code, timed_out) == ("half a line �", "warn �", None, True)


def test_execute_runs_in_the_given_cwd_with_only_the_given_env(tmp_path, monkeypatch):
    monkeypatch.setenv("WI15H_PARENT_ONLY", "leak")
    inv = CA.Invocation("claude", (sys.executable, "-c",
                                   "import os, json; print(json.dumps([os.getcwd(), os.environ.get('WI15H_GIVEN'),"
                                   " os.environ.get('WI15H_PARENT_ONLY')]))"),
                        tmp_path, {"PATH": "/usr/bin:/bin", "WI15H_GIVEN": "yes"})
    out, _, code, _ = S._execute(inv, 30)
    assert code == 0
    assert json.loads(out) == [os.path.realpath(tmp_path), "yes", None]


# --------------------------------------------------------------------------- stored receipt under an ASCII locale


def test_stored_receipt_is_read_as_utf8_whatever_the_process_locale(tmp_path):
    data = {"schema": "klode-kit.isolation_receipt/1", "route": "codex", "cli_version": "2.5.1",
            "effective_config_sha": "c" * 64, "argv_sha": "d" * 64,
            "checks": [{"name": "effort:configured", "ok": True, "detail": "患者 é"}],
            "cloud_isolation": "PASS"}
    path = tmp_path / "iso.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    code = ("import sys; from klode_kit.ocr import send as S\n"
            "S.check_stored_receipt(sys.argv[1], 'codex', '2.5.1')\n"
            "print('accepted')\n")
    import klode_kit

    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0",
           "PYTHONIOENCODING": "ascii", "PYTHONPATH": str(Path(klode_kit.__file__).resolve().parent.parent)}
    probe = subprocess.run([sys.executable, "-c", "import locale; print(locale.getpreferredencoding(False))"],
                           env=env, capture_output=True, timeout=60)
    assert probe.stdout.decode().strip().lower() in ("ascii", "ansi_x3.4-1968", "us-ascii"), probe
    res = subprocess.run([sys.executable, "-c", code, str(path)], env=env, capture_output=True, timeout=60)
    assert res.returncode == 0, res.stderr.decode(errors="replace")
    assert res.stdout.decode().strip() == "accepted"
