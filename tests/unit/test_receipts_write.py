"""WI-00b1: atomic, validated receipt writes (test matrix 00b1-10 .. 00b1-13).

Law (matrix WI-00b1):
- round-trip: ``read(write(x)) == x`` for every legal receipt (S5);
- atomicity: after a crash the target holds either the complete old or the complete new
  content, never a partial file (S5).

Public surface:
- ``klode_kit.receipts.write(path, data, schema)`` -> Path; ``read(path, schema)`` -> dict;
  ``ReceiptInvalid`` (carries ``issues``);
- ``python -m klode_kit.receipts write --schema <name> <path>`` reads the receipt JSON from
  stdin (exit 0 written; exit 30 contract violation, nothing written).

Crash points (matrix section 0.3, enumeration list paired with 00s-16 ``receipts``):
``receipts.mkdir`` (after each missing parent directory is created) and
``receipts.before_replace`` (temporary file complete, before ``os.replace``).
"""

from __future__ import annotations

import copy
import json
import math
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import base_env
from klode_kit import receipts
from klode_kit.contract import schema as S

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "tests" / "contract" / "schema_examples"
RECEIPT_CRASHPOINTS = ("receipts.mkdir", "receipts.before_replace")


def _valid(name: str) -> dict:
    files = sorted((EXAMPLES / name / "valid").glob("*.json"))
    assert files, name
    return json.loads(files[0].read_text(encoding="utf-8"))


def _tree(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def _cli(target: Path, data, schema: str = "receipt", extra_env: dict | None = None, timeout: float = 60):
    payload = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
    return subprocess.run(
        [sys.executable, "-m", "klode_kit.receipts", "write", "--schema", schema, str(target)],
        input=payload, env=base_env(extra_env), capture_output=True, timeout=timeout,
    )


# --------------------------------------------------------------------------- 00b1-10


@pytest.mark.parametrize("nested", [False, True], ids=["existing-dir", "missing-dirs"])
def test_receipt_write_invalid_no_file(tmp_path, nested):
    """00b1-10: an illegal receipt raises ReceiptInvalid and leaves the directory listing unchanged."""
    bad = _valid("receipt")
    del bad["inputs_hash"]
    target = tmp_path / ("a/b/r.json" if nested else "r.json")
    before = _tree(tmp_path)
    with pytest.raises(receipts.ReceiptInvalid) as exc:
        receipts.write(target, bad, "receipt")
    assert [i.path for i in exc.value.issues] == ["inputs_hash"]
    assert _tree(tmp_path) == before


def test_receipt_write_invalid_keeps_old_file(tmp_path):
    target = tmp_path / "r.json"
    old = _valid("receipt")
    receipts.write(target, old, "receipt")
    old_bytes = target.read_bytes()
    bad = dict(old, status="exploded")
    with pytest.raises(receipts.ReceiptInvalid):
        receipts.write(target, bad, "receipt")
    assert target.read_bytes() == old_bytes
    assert _tree(tmp_path) == ["r.json"]


@pytest.mark.parametrize("value", [math.nan, math.inf])
def test_receipt_write_non_json_number_refused(tmp_path, value):
    """NaN/Infinity are not JSON; refusing them is part of 'illegal content never lands'."""
    doc = _valid("receipt")
    doc["cost_usd"] = value
    with pytest.raises(receipts.ReceiptInvalid):
        receipts.write(tmp_path / "r.json", doc, "receipt")
    assert _tree(tmp_path) == []


def test_receipt_write_unknown_schema_refused(tmp_path):
    with pytest.raises(S.UnknownSchema):
        receipts.write(tmp_path / "r.json", {}, "nope")
    assert _tree(tmp_path) == []


def test_receipt_cli_invalid_exit30_no_file(tmp_path):
    bad = _valid("receipt")
    bad["status"] = "maybe"
    res = _cli(tmp_path / "r.json", bad)
    assert res.returncode == 30, res.stderr.decode()
    assert "status" in res.stderr.decode()
    assert _tree(tmp_path) == []


def test_receipt_cli_unparseable_input_exit30_no_file(tmp_path):
    res = _cli(tmp_path / "r.json", b"{not json")
    assert res.returncode == 30, res.stderr.decode()
    assert _tree(tmp_path) == []


def test_receipt_read_rejects_illegal_file(tmp_path):
    target = tmp_path / "r.json"
    bad = _valid("receipt")
    bad["exit_code"] = "zero"
    target.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(receipts.ReceiptInvalid) as exc:
        receipts.read(target, "receipt")
    assert [i.path for i in exc.value.issues] == ["exit_code"]


def test_receipt_write_creates_missing_parents(tmp_path):
    target = tmp_path / "work" / "b1" / "receipts" / "ocr.json"
    doc = _valid("receipt")
    assert receipts.write(target, doc, "receipt") == target
    assert receipts.read(target, "receipt") == doc
    assert _tree(tmp_path) == ["work", "work/b1", "work/b1/receipts", "work/b1/receipts/ocr.json"]


# --------------------------------------------------------------------------- 00b1-11


def test_receipt_crash_no_partial(tmp_path):
    """00b1-11: SIGKILL at receipts.before_replace leaves the complete old receipt at the target."""
    target = tmp_path / "receipts" / "step.json"
    old = _valid("receipt")
    receipts.write(target, old, "receipt")
    old_bytes = target.read_bytes()
    new = dict(copy.deepcopy(old), status="failed", exit_code=20)

    res = _cli(target, new, extra_env={"KLODE_KIT_CRASH_AT": "receipts.before_replace"})

    assert res.returncode == -signal.SIGKILL, res.stderr.decode()
    assert target.read_bytes() == old_bytes
    assert receipts.read(target, "receipt") == old
    # the run without the crash point does land the new content (the crash is what stopped it)
    ok = _cli(target, new)
    assert ok.returncode == 0, ok.stderr.decode()
    assert receipts.read(target, "receipt") == new


def test_receipt_crash_at_mkdir_no_partial(tmp_path):
    """00b1-11 (enumerated, added): SIGKILL right after the first missing parent is created."""
    target = tmp_path / "work" / "b1" / "step.json"
    res = _cli(target, _valid("receipt"), extra_env={"KLODE_KIT_CRASH_AT": "receipts.mkdir"})
    assert res.returncode == -signal.SIGKILL, res.stderr.decode()
    assert not target.exists()
    assert _tree(tmp_path) == ["work"]


def test_receipt_crashpoints_inert_in_real_mode(tmp_path):
    """Seams off (KLODE_KIT_REAL=1): the same crash variable is ignored and the write completes."""
    target = tmp_path / "step.json"
    doc = _valid("receipt")
    res = _cli(target, doc, extra_env={"KLODE_KIT_CRASH_AT": "receipts.before_replace", "KLODE_KIT_REAL": "1"})
    assert res.returncode == 0, res.stderr.decode()
    assert receipts.read(target, "receipt") == doc


# --------------------------------------------------------------------------- 00b1-12

HEX = "0123456789abcdef"
_sha = st.text(alphabet=HEX, min_size=64, max_size=64)
_time = st.builds(
    lambda d, h, m, s: f"2026-{d // 28 + 1:02d}-{d % 28 + 1:02d}T{h:02d}:{m:02d}:{s:02d}Z",
    st.integers(0, 335), st.integers(0, 23), st.integers(0, 59), st.integers(0, 59),
)
_text = st.text(min_size=1, max_size=30).filter(lambda s: s.strip() == s and s != "")
_relpath = st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=8),
                    min_size=1, max_size=4).map("/".join)


@st.composite
def _step_receipts(draw):
    status = draw(st.sampled_from(["running", "done", "failed"]))
    doc = {
        "schema": "klode-kit.receipt/1",
        "step": draw(st.sampled_from(["preflight", "ocr", "audit", "writeback", "build", "check", "publish"])),
        "status": status,
        "started": draw(_time),
        "inputs_hash": draw(_sha),
        "config_hash": draw(_sha),
        "config_version": 1,
        "outputs": draw(st.lists(st.fixed_dictionaries({"path": _relpath, "sha256": _sha}), max_size=4)),
    }
    if status != "running":
        doc["ended"] = draw(_time)
        doc["exit_code"] = draw(st.sampled_from([0, 10, 20, 30, 40, 50, 60]))
    else:
        doc["pid"] = draw(st.integers(1, 2**22))
    if draw(st.booleans()):
        doc["cost_usd"] = draw(st.floats(min_value=0, max_value=1e4, allow_nan=False, allow_infinity=False))
    if draw(st.booleans()):
        doc["model"] = draw(_text)
    if draw(st.booleans()):
        doc["excluded"] = draw(st.lists(st.fixed_dictionaries({
            "rec_id": st.text(alphabet=HEX, min_size=12, max_size=12).map(lambda s: "r-" + s),
            "reason": _text}), max_size=3))
    return "receipt", doc


@st.composite
def _isolation_receipts(draw):
    doc = _valid("isolation_receipt")
    doc["argv_sha"] = draw(_sha)
    doc["effective_config_sha"] = draw(_sha)
    doc["cli_version"] = draw(st.sampled_from(["2.1.259", "2.2.0", "0.47.1"]))
    doc["checks"] = draw(st.lists(st.fixed_dictionaries({"name": _text, "ok": st.booleans()}), min_size=1, max_size=5))
    doc["cloud_isolation"] = "PASS" if all(c["ok"] for c in doc["checks"]) else "FAIL"
    return "isolation_receipt", doc


@st.composite
def _lane_receipts(draw):
    doc = _valid("lane_receipt")
    n = draw(st.integers(1, 500))
    failed = draw(st.integers(0, n))
    doc.update(tests_discovered=n, tests_failed=failed, status="PASS" if failed == 0 else "FAIL",
               bootstrap=False, pytest_exit_code=0 if failed == 0 else 1, tags=sorted(set(draw(st.lists(_text, max_size=3)))))
    doc["env_fingerprint"]["release_sha"] = draw(_sha)
    return "lane_receipt", doc


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(st.one_of(_step_receipts(), _isolation_receipts(), _lane_receipts()))
def test_receipt_roundtrip_property(tmp_path, case):
    """00b1-12 (law: round-trip): any legal receipt reads back equal to what was written."""
    name, doc = case
    assert S.issues(name, doc) == [], "generator must only produce legal receipts"
    # one directory per contract: hypothesis reuses the function-scoped tmp_path across examples
    target = tmp_path / "rt" / name / f"{name}.json"
    receipts.write(target, doc, name)
    assert receipts.read(target, name) == doc
    # and writing what was read is a fixed point (byte-identical file)
    first = target.read_bytes()
    receipts.write(target, receipts.read(target, name), name)
    assert target.read_bytes() == first
    assert [p.name for p in target.parent.iterdir()] == [target.name]


# --------------------------------------------------------------------------- 00b1-13


@pytest.mark.parametrize("round_", range(3))
def test_receipt_concurrent_writers_whole_file(tmp_path, round_):
    """00b1-13: 8 processes write different legal receipts to one path; the result is exactly one of them."""
    target = tmp_path / "receipts" / "step.json"
    target.parent.mkdir()
    base = _valid("receipt")
    docs = []
    for i in range(8):
        d = copy.deepcopy(base)
        d["inputs_hash"] = f"{i:x}" * 64
        # large enough that a torn or interleaved write would be visible
        d["outputs"] = [{"path": f"w{i}/f{j:05d}.txt", "sha256": f"{(i + j) % 16:x}" * 64} for j in range(1500)]
        docs.append(d)
    payloads = [json.dumps(d).encode("utf-8") for d in docs]
    procs = [
        subprocess.Popen([sys.executable, "-m", "klode_kit.receipts", "write", "--schema", "receipt", str(target)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=base_env())
        for _ in docs
    ]
    for p, payload in zip(procs, payloads):
        p.stdin.write(payload)
    for p in procs:
        p.stdin.close()
    for p in procs:
        assert p.wait(timeout=120) == 0, p.stderr.read().decode()
        p.stdout.close()
        p.stderr.close()

    final = receipts.read(target, "receipt")
    assert sum(final == d for d in docs) == 1
    assert os.listdir(target.parent) == ["step.json"]


# --------------------------------------------------------------------------- failure paths (added)


def test_receipt_read_rejects_non_json(tmp_path):
    target = tmp_path / "r.json"
    target.write_bytes(b"\xff\xfe not json")
    with pytest.raises(receipts.ReceiptInvalid) as exc:
        receipts.read(target, "receipt")
    assert [i.path for i in exc.value.issues] == [""]


def test_receipt_write_parent_is_a_file(tmp_path):
    """A regular file where a parent directory must go: refused, nothing created around it."""
    (tmp_path / "blocker").write_text("x")
    with pytest.raises(OSError):
        receipts.write(tmp_path / "blocker" / "r.json", _valid("receipt"), "receipt")
    assert _tree(tmp_path) == ["blocker"]
    assert (tmp_path / "blocker").read_text() == "x"


def test_receipt_write_replace_failure_leaves_no_tmp(tmp_path):
    """os.replace fails (target is a non-empty directory): the error surfaces and no .tmp is left."""
    target = tmp_path / "r.json"
    target.mkdir()
    (target / "keep").write_text("k")
    with pytest.raises(OSError):
        receipts.write(target, _valid("receipt"), "receipt")
    assert _tree(tmp_path) == ["r.json", "r.json/keep"]


@pytest.mark.parametrize(("args", "code"), [(["write", "x.json"], 2), (["write", "--schema", "nope", "x.json"], 2),
                                            (["frobnicate"], 2), (["--help"], 0)])
def test_receipt_cli_usage(tmp_path, args, code):
    res = subprocess.run([sys.executable, "-m", "klode_kit.receipts", *args], input=b"{}", cwd=tmp_path,
                         env=base_env(), capture_output=True, timeout=60)
    assert res.returncode == code, res.stderr.decode()
    assert _tree(tmp_path) == []
