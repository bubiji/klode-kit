"""WI-15i: ``contract/ids`` pinned exactly (mutation-gate survivors, score 0.663 -> >= 0.85).

The existing ``test_ids*.py`` check the shapes and the laws (set semantics, contiguity, sensitivity). They do not pin
*what the key builders say when they refuse* (the ``malformed <what>: <value!r>`` text of every ID format check), the
bytes that feed the fingerprints (the literal key names and the compact canonical JSON of ``cfg_fp``, ``batch_id``,
``idempotency_key`` and ``build_id``), the bool/int and clock guards, the attempt-directory scan (``_highest``), the
claim loop, the directory fsync and the two crash points.

Law: none new - batch/rec/cfg_fp/attempt laws stay in ``test_ids.py`` (hypothesis). These are S1/S2/S3 pins; the
hash cases recompute the expected digest from hand-written JSON text, so a renamed key or a changed separator fails.

Mutants left alone as equivalent (5 of the 161 non-killed), with the reason: ``encode("UTF-8")`` / ``"UTF-8"``
(``_sha`` and ``_canonical``) is the same codec as ``"utf-8"``; ``ensure_ascii=None`` and ``allow_nan=None`` are falsy
like ``False``; ``claimed = None`` in the ``FileExistsError`` branch is falsy like ``False``.
Mutants that hang the claim loop (``_highest`` returning 0 or never matching) are turned into failures by a bounded
``os.mkdir`` so they are killed rather than timed out.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone, tzinfo

import pytest

from klode_kit import config
from klode_kit.contract import ids

pytestmark = pytest.mark.unit

A64 = "a" * 64
B64 = "b" * 64
HEX12 = "0123456789ab"
HEX12B = "fedcba987654"
DOC = "d-" + HEX12
REC = "r-" + HEX12
REV = HEX12 + "/a3"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _msg(call, *args, **kwargs) -> str:
    with pytest.raises(ValueError) as exc:
        call(*args, **kwargs)
    return str(exc.value)


# ----------------------------------------------------------------------------- refusal texts


@pytest.mark.parametrize(
    "call, args, expected",
    [
        (ids.doc_id, ("zz",), "malformed img_sha256: 'zz'"),
        (ids.doc_id, (A64.upper(),), f"malformed img_sha256: {A64.upper()!r}"),
        (ids.doc_id, (None,), "malformed img_sha256: None"),
        (ids.page_id, ("d-zz", 1), "malformed doc_id: 'd-zz'"),
        (ids.page_id, (DOC, 0), "page_no must be an integer >= 1: 0"),
        (ids.page_id, (DOC, True), "page_no must be an integer >= 1: True"),
        (ids.page_id, (DOC, "1"), "page_no must be an integer >= 1: '1'"),
        (ids.page_id, (DOC, 1.0), "page_no must be an integer >= 1: 1.0"),
        (ids.rec_id, ({"doc_id": "x", "page_id": "x/p001", "record_idx": 0},), "malformed doc_id: 'x'"),
        (ids.rec_id, ({"page_id": f"{DOC}/p001", "record_idx": 0},), "malformed doc_id: None"),
        (ids.rec_id, ({"doc_id": DOC, "page_id": "d-aaaaaaaaaaaa/p001", "record_idx": 0},),
         f"page_id 'd-aaaaaaaaaaaa/p001' does not belong to doc {DOC}"),
        (ids.rec_id, ({"doc_id": DOC, "page_id": None, "record_idx": 0},),
         f"page_id None does not belong to doc {DOC}"),
        (ids.rec_id, ({"doc_id": DOC, "page_id": f"{DOC}/p01", "record_idx": 0},),
         f"page_id '{DOC}/p01' does not belong to doc {DOC}"),
        (ids.rec_id, ({"doc_id": DOC, "page_id": f"{DOC}/p001", "record_idx": -1},),
         "record_idx must be an integer >= 0: -1"),
        (ids.rec_id, ({"doc_id": DOC, "page_id": f"{DOC}/p001", "record_idx": False},),
         "record_idx must be an integer >= 0: False"),
        (ids.rec_id, ({"doc_id": DOC, "page_id": f"{DOC}/p001"},), "record_idx must be an integer >= 0: None"),
        (ids.batch_id, (["x"],), "malformed img_sha256: 'x'"),
        (ids.batch_id, ([],), "a batch needs at least one image"),
        (ids.duplicate_flags, ([],), "a batch needs at least one image"),
        (ids.duplicate_flags, (["x"],), "malformed img_sha256: 'x'"),
        (ids.attempt_id, (0,), "attempt seq must be an integer >= 1: 0"),
        (ids.attempt_id, (True,), "attempt seq must be an integer >= 1: True"),
        (ids.run_id, ("zz", "tesseract", HEX12, "t1"), "malformed batch_id: 'zz'"),
        (ids.run_id, (HEX12, "Tess", HEX12, "t1"), "malformed engine: 'Tess'"),
        (ids.run_id, (HEX12, "tess1", HEX12, "t1"), "malformed engine: 'tess1'"),
        (ids.run_id, (HEX12, "tess", "zz", "t1"), "malformed cfg_fp: 'zz'"),
        (ids.run_id, (HEX12, "tess", HEX12, "t0"), "malformed attempt_id: 't0'"),
        (ids.run_id, (HEX12, "tess", HEX12, "t01"), "malformed attempt_id: 't01'"),
        (ids.run_id, (HEX12, "tess", HEX12, "1"), "malformed attempt_id: '1'"),
        (ids.audit_rev, ("zz", 1), "malformed batch_id: 'zz'"),
        (ids.audit_rev, (HEX12, 0), "audit revision must be an integer >= 1: 0"),
        (ids.field_id, ("r-zz", "s"), "malformed rec_id: 'r-zz'"),
        (ids.field_id, (REC, "a/b"), "malformed field slot: 'a/b'"),
        (ids.field_id, (REC, "a b"), "malformed field slot: 'a b'"),
        (ids.field_id, (REC, ""), "malformed field slot: ''"),
        (ids.decision_id, ("zz", 1), "malformed audit_rev: 'zz'"),
        (ids.decision_id, (HEX12 + "/a0", 1), f"malformed audit_rev: '{HEX12}/a0'"),
        (ids.decision_id, (REV, 0), "decision seq must be an integer >= 1: 0"),
        (ids.commit_id, ("zz", 1), "malformed audit_rev: 'zz'"),
        (ids.commit_id, (REV, 0), "commit seq must be an integer >= 1: 0"),
        (ids.idempotency_key, ("zz", 0, {}), "malformed field_id: 'zz'"),
        (ids.idempotency_key, (f"{REC}/s", -1, {}), "rev_base must be an integer >= 0: -1"),
        (ids.idempotency_key, (f"{REC}/s", True, {}), "rev_base must be an integer >= 0: True"),
    ],
)
def test_refusal_text_is_exact(call, args, expected):
    assert _msg(call, *args) == expected


def test_malformed_values_never_pass_the_patterns():
    # the ID formats are anchored: no prefix, suffix, or case folding is tolerated
    for bad in (A64 + "0", "0" + A64, A64[:-1], A64[:-1] + "A"):
        with pytest.raises(ValueError):
            ids.doc_id(bad)
    assert ids.doc_id(A64) == "d-" + "a" * 12
    for bad in (DOC + "x", "x" + DOC, "d-" + HEX12.upper()):
        with pytest.raises(ValueError):
            ids.page_id(bad, 1)
    for bad in (REC + "x", "x" + REC, "r-" + HEX12.upper()):
        with pytest.raises(ValueError):
            ids.field_id(bad, "s")


def test_positive_rejects_non_ints_with_value_error_and_bool_even_at_minimum_zero():
    assert ids.page_id(DOC, 1) == f"{DOC}/p001"
    assert ids.rec_id({"doc_id": DOC, "page_id": f"{DOC}/p001", "record_idx": 0}).startswith("r-")
    for bad in ("3", 2.0, None, [1]):
        assert _msg(ids.attempt_id, bad) == f"attempt seq must be an integer >= 1: {bad!r}"
    assert _msg(ids.idempotency_key, f"{REC}/s", False, {}) == "rev_base must be an integer >= 0: False"
    assert ids.idempotency_key(f"{REC}/s", 0, {}).startswith(f"{REC}/s@0#")


def test_rec_id_with_non_string_page_is_a_value_error_not_a_type_error():
    for page in (None, 7, b"x", ["p001"]):
        with pytest.raises(ValueError, match="does not belong to doc"):
            ids.rec_id({"doc_id": DOC, "page_id": page, "record_idx": 0})


# ----------------------------------------------------------------------------- literal formulas


def test_page_and_audit_ids_literal_shapes():
    assert ids.page_id(DOC, 7) == f"{DOC}/p007"
    assert ids.page_id(DOC, 1234) == f"{DOC}/p1234"
    assert ids.audit_rev(HEX12, 12) == f"{HEX12}/a12"
    assert ids.decision_id(REV, 7) == f"{REV}/d0007"
    assert ids.commit_id(REV, 7) == f"{REV}/c0007"
    assert ids.decision_id(REV, 12345) == f"{REV}/d12345"
    assert ids.field_id(REC, "amount") == f"{REC}/amount"
    assert ids.run_id(HEX12, "tess", HEX12B, "t12") == f"{HEX12}/tess/{HEX12B}/t12"
    assert ids.attempt_id(12) == "t12"


def test_rec_id_formula_is_pipe_joined_sha():
    rec = ids.rec_id({"doc_id": DOC, "page_id": f"{DOC}/p012", "record_idx": 3})
    assert rec == "r-" + _sha(f"{DOC}|p012|3")[:12]
    rec_long = ids.rec_id({"doc_id": DOC, "page_id": f"{DOC}/p1234", "record_idx": 0})
    assert rec_long == "r-" + _sha(f"{DOC}|p1234|0")[:12]


def test_batch_id_joins_sorted_unique_shas_with_a_newline():
    assert ids.batch_id([B64, A64, B64]) == _sha(A64 + "\n" + B64)[:12]
    assert ids.batch_id([A64]) == _sha(A64)[:12]
    assert ids.duplicate_flags([A64, B64, A64, A64]) == [False, False, True, True]


def test_idempotency_key_is_exactly_sixteen_hex_of_compact_canonical_choice():
    key = ids.idempotency_key(f"{REC}/s", 4, {"b": 1, "a": "é"})
    digest = _sha('{"a":"é","b":1}')
    assert key == f"{REC}/s@4#{digest[:16]}"
    assert re.fullmatch(re.escape(f"{REC}/s@4#") + "[0-9a-f]{16}", key)


def test_canonical_refuses_nan_and_infinity():
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ids.idempotency_key(f"{REC}/s", 0, {"x": bad})


def test_canonical_keeps_non_ascii_as_utf8_and_is_compact():
    assert ids._canonical({"k": ["é", 1], "a": None}) == '{"a":null,"k":["é",1]}'.encode("utf-8")


# ----------------------------------------------------------------------------- cfg_fp


def _inputs(**over):
    base = dict(prompt_sha="P1", schema_sha="S1", model="M1", effort="E1", adapter_code_version="V1",
                der_sha256s=[B64, A64, B64])
    base.update(over)
    return base


@pytest.mark.parametrize("name", ["prompt_sha", "schema_sha", "model", "effort", "adapter_code_version"])
def test_cfg_fp_rejects_empty_and_non_string_naming_the_parameter(name):
    cfg = config.load()
    assert _msg(ids.cfg_fp, cfg, **_inputs(**{name: ""})) == f"{name} must be a non-empty string: ''"
    assert _msg(ids.cfg_fp, cfg, **_inputs(**{name: 5})) == f"{name} must be a non-empty string: 5"


def test_cfg_fp_der_sha256_refusal_text():
    assert _msg(ids.cfg_fp, config.load(), **_inputs(der_sha256s=["nope"])) == "malformed der_sha256: 'nope'"


def test_cfg_fp_hashes_this_exact_canonical_payload():
    cfg = config.load()
    conf = json.dumps(json.loads(cfg.canonical()), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    text = (
        '{"adapter_code_version":"V1","config":' + conf + f',"der_sha256":["{A64}","{B64}"],'
        '"effort":"E1","model":"Mé","prompt_sha":"P1","schema_sha":"S1"}'
    )
    assert ids.cfg_fp(cfg, **_inputs(model="Mé")) == _sha(text)[:12]


# ----------------------------------------------------------------------------- build_id


def _build(**over):
    base = dict(rec_revs={REC: 2}, items_sha="i", desk_sha="d", holdout_sha="h", config_version=1,
                plugin_version="p1", klode_version="k1", now=datetime(2026, 10, 22, 1, 2, 3, tzinfo=timezone.utc))
    base.update(over)
    return ids.build_id(**base)


def test_build_id_hashes_this_exact_payload():
    other = "r-" + HEX12B
    text = (
        '{"config_version":3,"desk_sha":"d","holdout_sha":"h","items_sha":"i","klode_version":"k1",'
        f'"plugin_version":"p1","rec_revs":["{REC}:2","{other}:0"]}}'
    )
    got = _build(rec_revs={other: 0, REC: 2}, config_version=3)
    assert got == f"b-20261022T010203Z-{_sha(text)[:8]}"


def test_build_id_accepts_pairs_not_only_mappings_and_rev_zero():
    assert _build(rec_revs=[(REC, 2)]) == _build(rec_revs={REC: 2})
    assert _build(rec_revs=iter([(REC, 0)])) == _build(rec_revs={REC: 0})


def test_build_id_refusal_texts():
    assert _msg(_build, rec_revs={"zz": 1}) == "malformed rec_id: 'zz'"
    assert _msg(_build, rec_revs={REC: -1}) == "rev must be an integer >= 0: -1"
    assert _msg(_build, rec_revs={REC: True}) == "rev must be an integer >= 0: True"
    assert _msg(_build, config_version=0) == "config_version must be an integer >= 1: 0"


def test_build_id_clock_must_be_a_timezone_aware_datetime():
    naive = datetime(2026, 10, 22, 1, 2, 3)
    assert _msg(_build, now=naive) == f"build_id needs a timezone-aware injected clock value, got {naive!r}"
    for bad in ("2026-10-22", None, 5):
        assert _msg(_build, now=bad) == f"build_id needs a timezone-aware injected clock value, got {bad!r}"

    class NoOffset(tzinfo):
        def utcoffset(self, dt):
            return None

        def dst(self, dt):
            return None

        def tzname(self, dt):
            return "x"

    odd = datetime(2026, 10, 22, 1, 2, 3, tzinfo=NoOffset())
    assert _msg(_build, now=odd) == f"build_id needs a timezone-aware injected clock value, got {odd!r}"


def test_build_id_stamp_is_utc_of_the_injected_clock():
    plus9 = timezone(timedelta(hours=9))
    a = _build(now=datetime(2026, 10, 22, 10, 2, 3, tzinfo=plus9))
    assert a.startswith("b-20261022T010203Z-")


# ----------------------------------------------------------------------------- attempts


@pytest.fixture
def bounded_mkdir(monkeypatch):
    """A claim loop that never terminates (scan broken) becomes a failure instead of a hang."""
    real = ids.os.mkdir
    calls = []

    def mk(path, *a, **k):
        calls.append(str(path))
        assert len(calls) < 60, "allocate_attempt keeps retrying"
        return real(path, *a, **k)

    monkeypatch.setattr(ids.os, "mkdir", mk)
    return calls


def test_allocate_attempt_names_and_record(tmp_path, bounded_mkdir):
    batch = tmp_path / HEX12
    batch.mkdir()
    first = ids.allocate_attempt(batch, "tess", HEX12B)
    assert first.seq == 1 and first.attempt_id == "t1"
    assert first.run_id == f"{HEX12}/tess/{HEX12B}/t1"
    assert first.path == batch / "ocr" / "tess" / f"{HEX12B}.t1" and first.path.is_dir()
    second = ids.allocate_attempt(str(batch), "tess", HEX12B)
    assert (second.seq, second.attempt_id) == (2, "t2")


def test_allocate_attempt_scan_ignores_foreign_and_malformed_names_and_takes_the_highest(tmp_path, bounded_mkdir):
    batch = tmp_path / HEX12
    eng = batch / "ocr" / "tess"
    eng.mkdir(parents=True)
    for name in (f"{HEX12B}.t3", f"{HEX12B}.t10", f"{HEX12B}.t0", f"{HEX12B}.t07", f"{HEX12B}.t9x",
                 f"{HEX12B}.x99", f"{HEX12B}t77", f"{HEX12}.t50", f"{HEX12B}", f"{HEX12B}.t", f"{HEX12B}..t60"):
        (eng / name).mkdir()
    got = ids.allocate_attempt(batch, "tess", HEX12B)
    assert got.seq == 11 and got.path == eng / f"{HEX12B}.t11"
    other = ids.allocate_attempt(batch, "tess", HEX12)
    assert other.seq == 51


def test_allocate_attempt_retries_after_a_lost_claim(tmp_path, monkeypatch):
    batch = tmp_path / HEX12
    batch.mkdir()
    eng = batch / "ocr" / "tess"
    real = ids.os.mkdir
    state = {"raced": False}

    def racing(path, *a, **k):
        if str(path).endswith(".t1") and not state["raced"]:
            state["raced"] = True
            real(path)  # somebody else claims t1 between our scan and mkdir
            raise FileExistsError(path)
        return real(path, *a, **k)

    monkeypatch.setattr(ids.os, "mkdir", racing)
    points = []
    monkeypatch.setattr(ids, "crashpoint", points.append)
    got = ids.allocate_attempt(batch, "tess", HEX12B)
    assert got.seq == 2 and got.path == eng / f"{HEX12B}.t2"
    assert (eng / f"{HEX12B}.t1").is_dir()
    assert points.count("ids.attempt_mkdir") == 2  # after the lost claim and after the won one


def test_crashpoints_are_named_and_ordered(tmp_path, monkeypatch):
    batch = tmp_path / HEX12
    batch.mkdir()
    points = []
    monkeypatch.setattr(ids, "crashpoint", points.append)
    ids.allocate_attempt(batch, "tess", HEX12B)
    assert points == ["ids.mkdir", "ids.mkdir", "ids.attempt_mkdir"]  # ocr/, ocr/tess/, then the attempt
    points.clear()
    ids.allocate_attempt(batch, "tess", HEX12B)
    assert points == ["ids.attempt_mkdir"]
    assert ids.CRASHPOINTS == ("ids.mkdir", "ids.attempt_mkdir")


def test_allocate_attempt_refusal_texts_and_nothing_created(tmp_path):
    bad = tmp_path / "not-a-batch"
    bad.mkdir()
    assert _msg(ids.allocate_attempt, bad, "tess", HEX12B) == "malformed batch directory name (batch_id): 'not-a-batch'"
    good = tmp_path / HEX12
    good.mkdir()
    assert _msg(ids.allocate_attempt, good, "Tess", HEX12B) == "malformed engine: 'Tess'"
    assert _msg(ids.allocate_attempt, good, "tess", "zz") == "malformed cfg_fp: 'zz'"
    assert list(good.iterdir()) == []


def test_directory_fsync_failure_is_swallowed_and_targets_the_engine_directory(tmp_path, monkeypatch):
    batch = tmp_path / HEX12
    batch.mkdir()
    seen = []

    def failing_fsync(fd):
        seen.append(fd)
        raise OSError("fsync unsupported")

    monkeypatch.setattr(ids.os, "fsync", failing_fsync)
    got = ids.allocate_attempt(batch, "tess", HEX12B)
    assert got.seq == 1 and len(seen) == 1
    ids._fsync_dir(tmp_path / "does-not-exist")  # an unopenable directory is ignored as well


def test_fsync_dir_syncs_and_closes_its_descriptor(tmp_path, monkeypatch):
    opened, synced, closed = [], [], []
    real_open, real_close = ids.os.open, ids.os.close

    def spy_open(path, flags, *a, **k):
        fd = real_open(path, flags, *a, **k)
        opened.append((str(path), flags, fd))
        return fd

    monkeypatch.setattr(ids.os, "open", spy_open)
    monkeypatch.setattr(ids.os, "fsync", synced.append)
    monkeypatch.setattr(ids.os, "close", lambda fd: (closed.append(fd), real_close(fd))[1])
    ids._fsync_dir(tmp_path)
    assert [(p, f) for p, f, _ in opened] == [(str(tmp_path), ids.os.O_RDONLY)]
    assert synced == closed == [opened[0][2]]
