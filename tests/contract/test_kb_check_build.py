"""WI-10a: ``kb-check`` over a build directory - receipt, exit code, which build is read (matrix 10a-03, 10a-07,
10a-08; design 02 r3.2 "check 读 build 目录而非 current", R2-11, K5).

The builds are real (``klode-kit build`` of ``fx-labs-3x`` with the real klode); the faults are injected at the
double boundary by ``tests/_support/build_fx.Fault``. Laws: a check never writes outside the build it judges, and
never changes what ``current`` points to (K5: no build is pointed to before its check).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from _support import build_fx as B
from _support import kb_check_fx as KX
from _support.flows import REPO, base_env
from klode_kit import pipeline as P
from klode_kit.build import run_build as RB
from klode_kit.check import kb_check as K
from klode_kit.contract import schema as contract

pytestmark = pytest.mark.contract_replay

OTHER_ID = "b-20301231T000000Z-deadbeef"


def test_forced_receipt_fails_check(tmp_path):
    """10a-03 (L2, S3): a build whose write receipt says ``forced`` is a FAIL with exit 20 - both the build the
    pipeline refused (its step FAILed) and a sound build whose write receipt was forced afterwards."""
    project = B.project(tmp_path)
    fault = B.Fault(tmp_path)
    res = B.run_with(project, tmp_path, fault, "launcher", ["write-card"], "--kb", "kb/main", mutate="forced")
    assert res.returncode == 20, res.stderr.decode()
    (failed,) = B.builds(project)
    proc, receipt = KX.check(B.kb_dir(project))
    assert proc.returncode == 20, proc.stderr.decode()
    checks = KX.by_name(receipt)
    assert receipt["status"] == "FAIL" and checks["build_receipt"]["status"] == "FAIL"
    assert checks["write_receipt"]["status"] == "FAIL" and "forced" in checks["write_receipt"]["detail"]
    assert KX.written(failed) == receipt

    kb, build = KX.good_kb(tmp_path / "second")
    path = build / "write_receipt.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["forced"] = True
    path.write_text(json.dumps(doc), encoding="utf-8")
    KX.reseal(build)
    proc, receipt = KX.check(kb)
    assert proc.returncode == 20, proc.stderr.decode()
    checks = KX.by_name(receipt)
    assert checks["write_receipt"]["status"] == "FAIL" and "forced" in checks["write_receipt"]["detail"]
    assert [n for n, c in checks.items() if c["status"] == "FAIL"] == ["write_receipt"]


def test_check_reads_build_dir_not_current(tmp_path):
    """10a-07 (L2, S3): ``current`` points to the sound build A; B (later, defective) is the one that is checked and
    its defect is reported; the receipt lands in B, A and the pointer are untouched."""
    kb, a = KX.good_kb(tmp_path)
    current = kb / "current"
    os.symlink(Path("builds") / a.name, current)
    b = kb / "builds" / OTHER_ID
    import shutil

    shutil.copytree(a, b, symlinks=True)
    B.unlock(b)
    rec = json.loads((b / "build_receipt.json").read_text(encoding="utf-8"))
    rec["build_id"] = OTHER_ID
    (b / "build_receipt.json").write_text(json.dumps(rec), encoding="utf-8")
    card = b / "library" / "cards" / "wbc.md"
    card.write_text(card.read_text(encoding="utf-8").replace("zoom: full", "zoom: stub"), encoding="utf-8")
    KX.reseal(b)
    RB.make_readonly(a)
    before = (os.readlink(current), (a / "build_receipt.json").read_bytes())

    proc, receipt = KX.check(kb, "--build", OTHER_ID)
    assert proc.returncode == 20, proc.stderr.decode()
    assert receipt["build_id"] == OTHER_ID
    zoom = KX.by_name(receipt)["zoom"]
    assert zoom["status"] == "FAIL" and "wbc" in zoom["detail"]
    assert KX.written(b) == receipt and not (a / "check_receipt.json").exists()
    assert (os.readlink(current), (a / "build_receipt.json").read_bytes()) == before

    # no --build: the newest build under builds/ is the one (not the one current points to)
    proc, default = KX.check(kb)
    assert proc.returncode == 20 and default["build_id"] == OTHER_ID
    # A, asked for by id, is sound
    proc, sound = KX.check(kb, "--build", a.name)
    assert proc.returncode == 0, proc.stderr.decode()
    assert sound["build_id"] == a.name and sound["status"] == "PASS"
    assert os.readlink(current) == str(Path("builds") / a.name)


def test_plugin_field_in_receipt(tmp_path):
    """10a-08 (L2, S1): ``plugin.{name,version,entry,root_sha}`` in the receipt, on a passing and on a failing build."""
    kb, build = KX.good_kb(tmp_path)
    expected = K.plugin_block(REPO, KX.ENTRY)
    proc, receipt = KX.check(kb)
    assert proc.returncode == 0, proc.stderr.decode()
    assert receipt["plugin"] == expected
    assert set(receipt["plugin"]) == {"name", "version", "entry", "root_sha"}
    assert receipt["plugin"]["name"] == "klode-kit" and len(receipt["plugin"]["root_sha"]) == 64
    assert contract.issues("check_receipt", KX.written(build)) == []

    bad_kb, bad = KX.good_kb(tmp_path / "bad")
    card = bad / "library" / "cards" / "wbc.md"
    card.write_text(card.read_text(encoding="utf-8").replace("zoom: full", "zoom: stub"), encoding="utf-8")
    KX.reseal(bad)
    proc, failing = KX.check(bad_kb, entry="pipeline:check")
    assert proc.returncode == 20
    assert failing["plugin"] == K.plugin_block(REPO, "pipeline:check") and failing["plugin"]["entry"] == "pipeline:check"


def test_tampered_tree_is_exit_30(tmp_path):
    """A build whose files no longer match its MANIFEST was changed after it was finished: exit 30, said in the
    manifest check (the tree is not a build any more)."""
    kb, build = KX.good_kb(tmp_path)
    card = build / "cards_content" / "wbc.md"
    card.write_text(card.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    RB.make_readonly(build)
    proc, receipt = KX.check(kb)
    assert proc.returncode == 30, proc.stderr.decode()
    manifest = KX.by_name(receipt)["manifest"]
    assert manifest["status"] == "FAIL" and "changed: cards_content/wbc.md" in manifest["detail"]


def test_repeated_failure_is_exit_50_no_progress(tmp_path):
    """The same problems as the previous build's check (nothing improved): exit 50 instead of 20."""
    kb, a = KX.good_kb(tmp_path)

    def defective(build: Path, build_id: str | None) -> None:
        B.unlock(build)
        if build_id:
            rec = json.loads((build / "build_receipt.json").read_text(encoding="utf-8"))
            rec["build_id"] = build_id
            (build / "build_receipt.json").write_text(json.dumps(rec), encoding="utf-8")
        card = build / "library" / "cards" / "wbc.md"
        card.write_text(card.read_text(encoding="utf-8").replace("zoom: full", "zoom: stub"), encoding="utf-8")
        KX.reseal(build)

    import shutil

    b = kb / "builds" / "b-20280101T000000Z-aaaaaaaa"
    c = kb / "builds" / OTHER_ID
    shutil.copytree(a, b, symlinks=True)
    shutil.copytree(a, c, symlinks=True)
    defective(b, b.name)
    defective(c, c.name)
    first, _ = KX.check(kb, "--build", b.name)
    second, receipt = KX.check(kb, "--build", c.name)
    assert first.returncode == 20 and second.returncode == 50, second.stderr.decode()
    assert receipt["status"] == "FAIL"


def test_pipeline_check_stage_runs_kb_check_on_the_build(tmp_path):
    """The pipeline's check stage (``default_registry``) runs kb-check on the build the build stage made and
    reports the check receipt as its product; publish is still an interface (WI-09d)."""
    reg = P.default_registry()
    assert reg["check"].run is not None and reg["publish"].run is None
    kb, build = KX.good_kb(tmp_path)
    project = tmp_path
    rel = (build / "build_receipt.json").relative_to(project).as_posix()
    env = KX.env()

    def child(*args):
        proc = subprocess.run([str(REPO / "bin" / "klode-kit"), *args], env=env, cwd=str(project), capture_output=True)
        return proc.returncode, proc.stdout.decode()

    ctx = SimpleNamespace(project=project, upstream=lambda stage: [{"path": rel, "sha256": "x"}] if stage == "build" else [],
                          rel=lambda p: Path(p).relative_to(project).as_posix(), child=child, env=env)
    outcome = reg["check"].run(ctx)
    assert outcome.reason is P.Reason.ok, outcome.detail
    assert outcome.outputs == [(build / "check_receipt.json").relative_to(project).as_posix()]
    assert KX.written(build)["status"] == "PASS"
