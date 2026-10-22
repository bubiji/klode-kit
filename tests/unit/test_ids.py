"""WI-00b2: keys and fingerprints (test matrix 00b2-01 .. 00b2-10, 00b2-15).

Law (matrix WI-00b2):
- ``batch_id`` is independent of input order and of repeated images (set semantics, r5) (S4);
- ``rec_id`` is independent of the record date, and sensitive to doc / page / record_idx (S6);
- ``cfg_fp`` changes when any r4.4 input changes (S4) and is invariant under the canonical
  equivalences: TOML key order, whitespace, ``config_version``, der_sha256 order (S6);
- attempts are unique and contiguous ``t1..tN`` per ``(batch, engine, cfg_fp)``, never reused (S5).

Public surface (``klode_kit.contract.ids``):
- ``doc_id(img_sha256)``, ``page_id(doc_id, page_no)``, ``rec_id(record)`` where ``record`` carries
  ``doc_id``, ``page_id``, ``record_idx`` (any other field is ignored);
- ``batch_id(img_sha256s)``, ``duplicate_flags(img_sha256s)``;
- ``cfg_fp(config, *, prompt_sha, schema_sha, model, effort, adapter_code_version, der_sha256s)``;
- ``attempt_id(seq)``, ``run_id(batch, engine, cfg_fp, attempt)``,
  ``allocate_attempt(batch_dir, engine, cfg_fp)`` -> ``Attempt(seq, attempt_id, run_id, path)``;
  the attempt directory is ``<batch_dir>/ocr/<engine>/<cfg_fp>.t<seq>`` (04b-03 layout);
- ``audit_rev(batch, n)``, ``field_id(rec_id, slot)``, ``decision_id(audit_rev, seq)``,
  ``commit_id(audit_rev, seq)``, ``idempotency_key(field_id, rev_base, choice)``;
- ``build_id(*, rec_revs, items_sha, desk_sha, holdout_sha, config_version, plugin_version,
  klode_version, now)`` with an injected, timezone-aware ``now``.

Crash point (matrix section 0.3): ``ids.attempt_mkdir`` after every attempt-directory mkdir.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from _support.flows import base_env
from klode_kit import config
from klode_kit.contract import ids
from klode_kit.contract import schema as S

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
FX_CHAIN = REPO / "tests" / "fixtures" / "fx-chain" / "clean"
CLOCK = datetime(2026, 10, 22, 0, 0, 0, tzinfo=timezone.utc)


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


shas = st.binary(min_size=1, max_size=16).map(_sha)


def _record(doc: str, page_no: int, idx: int, day: date, kind: str = "lab") -> dict:
    return {
        "doc_id": doc,
        "page_id": ids.page_id(doc, page_no),
        "record_idx": idx,
        "date": day.isoformat(),
        "type": kind,
    }


def _fp_inputs(**over):
    base = dict(
        prompt_sha=_sha("prompt"),
        schema_sha=_sha("schema"),
        model="opus",
        effort="max",
        adapter_code_version="1",
        der_sha256s=[_sha("der-1"), _sha("der-2")],
    )
    base.update(over)
    return base


def _build_inputs(**over):
    base = dict(
        rec_revs={"r-000000000001": 1, "r-000000000002": 3},
        items_sha=_sha("items.yaml"),
        desk_sha=_sha("desk.json"),
        holdout_sha=_sha("holdout.jsonl"),
        config_version=1,
        plugin_version="0.1.0",
        klode_version="0.5.0",
    )
    base.update(over)
    return base


# ---------------------------------------------------------------- batch_id (00b2-01, 00b2-02)


@settings(max_examples=150, deadline=None)
@given(items=st.lists(shas, min_size=1, max_size=12, unique=True), data=st.data())
def test_batch_id_permutation_invariant(items, data):
    """00b2-01 L1·S4: shuffling keeps the id; adding or removing an image changes it."""
    base = ids.batch_id(items)
    assert re.fullmatch(r"[0-9a-f]{12}", base)
    shuffled = data.draw(st.permutations(items))
    assert ids.batch_id(shuffled) == base
    extra = data.draw(shas.filter(lambda s: s not in items))
    assert ids.batch_id([*items, extra]) != base
    if len(items) > 1:
        drop = data.draw(st.integers(0, len(items) - 1))
        assert ids.batch_id(items[:drop] + items[drop + 1 :]) != base


def test_batch_id_duplicate_image():
    """00b2-02 L1·S2 (r5 decision): set semantics; the repeat is flagged for the manifest."""
    a, b = _sha("img-a"), _sha("img-b")
    assert ids.batch_id([a, a]) == ids.batch_id([a])
    assert ids.batch_id([a, b, a, b, b]) == ids.batch_id([b, a])
    assert ids.duplicate_flags([a, b, a, b, a]) == [False, False, True, True, True]
    assert ids.duplicate_flags([a]) == [False]
    line = {"doc_id": ids.doc_id(a), "img_sha256": a, "kind": "image", "duplicate": True}
    assert S.issues("manifest", line) == []


@settings(max_examples=100, deadline=None)
@given(items=st.lists(shas, min_size=1, max_size=8), data=st.data())
def test_batch_id_set_semantics_property(items, data):
    """Repeating any subset of the images never changes the id (r5, S4)."""
    repeats = data.draw(st.lists(st.sampled_from(items), max_size=8))
    assert ids.batch_id([*items, *repeats]) == ids.batch_id(sorted(set(items)))
    flags = ids.duplicate_flags([*items, *repeats])
    assert flags.count(False) == len(set(items))


@pytest.mark.parametrize("bad", [[], ["not-a-sha"], [_sha("x").upper()], [_sha("x")[:63]]])
def test_batch_id_rejects_empty_or_malformed(bad):
    with pytest.raises(ValueError):
        ids.batch_id(bad)


# ---------------------------------------------------------------- rec_id (00b2-03)


records = st.builds(
    lambda doc, page, idx, day: (doc, page, idx, day),
    shas.map(ids.doc_id),
    st.integers(1, 999),
    st.integers(0, 50),
    st.dates(min_value=date(1990, 1, 1), max_value=date(2030, 12, 31)),
)


@settings(max_examples=150, deadline=None)
@given(rec=records, data=st.data())
def test_rec_id_date_metamorphic(rec, data):
    """00b2-03 L1·S6: date (and type) never move rec_id; doc, page or record_idx always do."""
    doc, page, idx, day = rec
    base = ids.rec_id(_record(doc, page, idx, day))
    assert re.fullmatch(r"r-[0-9a-f]{12}", base)

    other_day = data.draw(st.dates(min_value=date(1990, 1, 1), max_value=date(2030, 12, 31)))
    assert ids.rec_id(_record(doc, page, idx, other_day, kind="rx")) == base

    other_doc = data.draw(shas.map(ids.doc_id).filter(lambda d: d != doc))
    assert ids.rec_id(_record(other_doc, page, idx, day)) != base
    other_page = data.draw(st.integers(1, 999).filter(lambda p: p != page))
    assert ids.rec_id(_record(doc, other_page, idx, day)) != base
    other_idx = data.draw(st.integers(0, 50).filter(lambda i: i != idx))
    assert ids.rec_id(_record(doc, page, other_idx, day)) != base


def test_rec_id_rejects_page_of_another_doc():
    doc, other = ids.doc_id(_sha("a")), ids.doc_id(_sha("b"))
    with pytest.raises(ValueError):
        ids.rec_id({"doc_id": doc, "page_id": ids.page_id(other, 1), "record_idx": 0})


# ---------------------------------------------------------------- formats (00b2-04)


def test_id_formats():
    """00b2-04 L1·S1: every id in its contract shape, clock injected at 2026-10-22T00:00:00Z."""
    img = _sha("synthetic-image-1")
    doc = ids.doc_id(img)
    assert re.fullmatch(r"d-[0-9a-f]{12}", doc)
    page = ids.page_id(doc, 1)
    assert page == f"{doc}/p001"
    assert ids.page_id(doc, 12) == f"{doc}/p012"
    rec = ids.rec_id({"doc_id": doc, "page_id": page, "record_idx": 0, "date": "2026-09-18"})
    assert re.fullmatch(r"r-[0-9a-f]{12}", rec)
    batch = ids.batch_id([img])
    assert re.fullmatch(r"[0-9a-f]{12}", batch)

    bid = ids.build_id(**_build_inputs(), now=CLOCK)
    assert re.fullmatch(r"b-20261022T000000Z-[0-9a-f]{8}", bid)

    rev = ids.audit_rev(batch, 1)
    assert rev == f"{batch}/a1"
    assert ids.decision_id(rev, 1) == f"{batch}/a1/d0001"
    assert ids.decision_id(rev, 12) == f"{batch}/a1/d0012"
    assert ids.commit_id(rev, 2) == f"{batch}/a1/c0002"
    fid = ids.field_id(rec, "labs[1].value")
    assert fid == f"{rec}/labs[1].value"

    fp = ids.cfg_fp(config.load(), **_fp_inputs())
    assert re.fullmatch(r"[0-9a-f]{12}", fp)
    assert ids.attempt_id(3) == "t3"
    assert ids.run_id(batch, "vision", fp, "t3") == f"{batch}/vision/{fp}/t3"

    # the ids fit the contract schemas written in WI-00b1
    fix = {
        "decision_id": ids.decision_id(rev, 1), "commit_id": ids.commit_id(rev, 1), "audit_rev": rev,
        "field_id": fid, "rev_base": 0, "rev_new": 1, "by": "human", "choice": "route", "route": "vision",
        "value": "10.5", "evidence": {"seen": ["vision"]}, "at": "2026-10-22T00:00:00Z",
    }
    assert [i for i in S.issues("fixes", fix) if i.path in ("decision_id", "commit_id", "audit_rev", "field_id")] == []


def test_ids_agree_with_fx_chain_fixture():
    """The keys the fixture generator wrote (WI-00a) are the keys this module computes."""
    manifest = json.loads((FX_CHAIN / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[0])
    chain = json.loads((FX_CHAIN / "chain.json").read_text(encoding="utf-8"))
    rec_file = next((FX_CHAIN / "records" / "_by_id").glob("r-*.md"))
    front = json.loads(rec_file.read_text(encoding="utf-8").split("---\n")[1])

    assert ids.doc_id(manifest["img_sha256"]) == manifest["doc_id"]
    assert ids.batch_id([manifest["img_sha256"]]) == chain["batch_id"]
    assert ids.rec_id(front) == front["rec_id"] == chain["rec_ids"][0]
    assert ids.audit_rev(chain["batch_id"], 1) == chain["audit_rev"]


@pytest.mark.parametrize(
    "call",
    [
        lambda: ids.doc_id("xyz"),
        lambda: ids.page_id(ids.doc_id(_sha("a")), 0),
        lambda: ids.page_id("not-a-doc", 1),
        lambda: ids.attempt_id(0),
        lambda: ids.audit_rev("short", 1),
        lambda: ids.audit_rev(ids.batch_id([_sha("a")]), 0),
        lambda: ids.field_id("r-000000000001", "has/slash"),
        lambda: ids.field_id("r-000000000001", ""),
        lambda: ids.field_id("bad", "x"),
        lambda: ids.decision_id("bad-rev", 1),
        lambda: ids.decision_id(f"{ids.batch_id([_sha('a')])}/a1", 0),
        lambda: ids.run_id("short", "vision", "0" * 12, "t1"),
        lambda: ids.run_id("0" * 12, "Vision!", "0" * 12, "t1"),
        lambda: ids.run_id("0" * 12, "vision", "0" * 12, "1"),
    ],
)
def test_id_constructors_reject_malformed_parts(call):
    with pytest.raises(ValueError):
        call()


# ---------------------------------------------------------------- cfg_fp (00b2-05 .. 00b2-07)


def _mutate_value(value, salt: int):
    if isinstance(value, bool):  # no bool keys today; keep the generator total
        return not value
    if isinstance(value, int):
        return value + 1 + salt
    if isinstance(value, float):
        return value + 0.5 + salt
    if isinstance(value, str):
        return value + "-x" + str(salt)
    if isinstance(value, list):
        return [*value, f"extra{salt}"]
    raise AssertionError(f"unexpected config value type: {type(value)}")


FP_FIELDS = ("config", "prompt_sha", "schema_sha", "model", "effort", "adapter_code_version", "der_sha256s")


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(field=st.sampled_from(FP_FIELDS), salt=st.integers(0, 1000), data=st.data())
def test_cfg_fp_sensitivity(field, salt, data):
    """00b2-05 L1·S4: changing any single r4.4 input changes the fingerprint."""
    cfg = config.load()
    inputs = _fp_inputs()
    base = ids.cfg_fp(cfg, **inputs)
    if field == "config":
        keys = sorted(k for k in cfg.flat() if k != "config_version")
        key = data.draw(st.sampled_from(keys))
        value = _mutate_value(cfg[key], salt)
        if key == "deident.mode":
            value = "dateshift"
        changed = ids.cfg_fp(config.resolve({key: value}), **inputs)
    elif field == "der_sha256s":
        extra = data.draw(shas.filter(lambda s: s not in inputs["der_sha256s"]))
        variant = data.draw(st.sampled_from(["add", "drop", "replace"]))
        ders = list(inputs["der_sha256s"])
        if variant == "add":
            ders.append(extra)
        elif variant == "drop":
            ders.pop()
        else:
            ders[0] = extra
        changed = ids.cfg_fp(cfg, **{**inputs, "der_sha256s": ders})
    else:
        changed = ids.cfg_fp(cfg, **{**inputs, field: _mutate_value(inputs[field], salt)})
    assert changed != base


DEFAULTS_SHUFFLED = """
# same content as the packaged defaults: tables reversed, keys reordered, odd whitespace
[deident]
mode="pseudonym"

[loops]
no_progress_rounds   =   2
max_total_rounds = 6
[desk]
holdout_pass = 0.8
holdout_min = 5

[cards]
writer_timeout_s = 600
writer_concurrency = 4
writer_max_rounds = 3
min_anchors_full = 5
min_aliases = 10

[text_layer]
garbage_max = 0.05
min_chars = 50

[cloud]
max_cost_usd_per_batch = 5.0
timeout_s = 180

[codex]
effort = "xhigh"
model = "gpt-6-sol"

[claude]
effort = "max"
model = "opus"
"""

TOP_SHUFFLED = """line_sim_tau = 0.6
repeat_ratio_max = 0.3
route_retry = 2
base_route = "vision"
routes = [ "vision",
  "mlx", "claude",   "codex" ]
config_version = {version}
"""


def test_cfg_fp_canonical(tmp_path):
    """00b2-06 L1·S6: TOML key order / whitespace and config_version never move the fingerprint."""
    packaged = ids.cfg_fp(config.load(), **_fp_inputs())

    shuffled = tmp_path / "defaults_shuffled.toml"
    shuffled.write_text(TOP_SHUFFLED.format(version=1) + DEFAULTS_SHUFFLED, encoding="utf-8")
    assert ids.cfg_fp(config.load(defaults=shuffled), **_fp_inputs()) == packaged

    bumped = tmp_path / "defaults_v2.toml"
    bumped.write_text(TOP_SHUFFLED.format(version=2) + DEFAULTS_SHUFFLED, encoding="utf-8")
    cfg2 = config.load(defaults=bumped)
    assert cfg2.config_version == 2
    assert ids.cfg_fp(cfg2, **_fp_inputs()) == packaged

    # an override equal to the default is the same effective configuration
    project = tmp_path / "proj"
    project.mkdir()
    (project / "klode-kit.toml").write_text("route_retry = 2\n[claude]\nmodel   = 'opus'\n", encoding="utf-8")
    assert ids.cfg_fp(config.load(project), **_fp_inputs()) == packaged


@settings(max_examples=100, deadline=None)
@given(ders=st.lists(shas, min_size=1, max_size=10, unique=True), data=st.data())
def test_cfg_fp_der_sha_order_invariant(ders, data):
    """00b2-07 L1·S4: der_sha256 is a set - any permutation gives the same fingerprint."""
    cfg = config.load()
    base = ids.cfg_fp(cfg, **_fp_inputs(der_sha256s=ders))
    perm = data.draw(st.permutations(ders))
    assert ids.cfg_fp(cfg, **_fp_inputs(der_sha256s=perm)) == base


def test_cfg_fp_rejects_malformed_der_sha():
    with pytest.raises(ValueError):
        ids.cfg_fp(config.load(), **_fp_inputs(der_sha256s=["nope"]))


# ---------------------------------------------------------------- attempts (00b2-08 .. 00b2-10)

ALLOC_CHILD = r"""
import json, os, sys, time
from pathlib import Path
from klode_kit.contract import ids
batch_dir, engine, fp, go = sys.argv[1:5]
Path(go + ".ready." + str(os.getpid())).write_text("")
deadline = time.monotonic() + 30
while not os.path.exists(go):
    if time.monotonic() > deadline:
        sys.exit(3)
    time.sleep(0.001)
a = ids.allocate_attempt(Path(batch_dir), engine, fp)
print(json.dumps({"attempt_id": a.attempt_id, "seq": a.seq, "run_id": a.run_id, "path": str(a.path)}))
"""


def _fp(tag: str) -> str:
    return _sha(tag)[:12]


def _batch_dir(tmp_path: Path, tag: str = "batch") -> Path:
    d = tmp_path / "work" / ids.batch_id([_sha(tag)])
    d.mkdir(parents=True)
    return d


def test_attempt_concurrent_unique(tmp_path):
    """00b2-08 L2·S5: 8 real processes allocate at once -> exactly t1..t8, 8 directories."""
    batch_dir = _batch_dir(tmp_path)
    fp = _fp("cfg")
    go = tmp_path / "go"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", ALLOC_CHILD, str(batch_dir), "vision", fp, str(go)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=base_env(),
        )
        for _ in range(8)
    ]
    deadline = time.monotonic() + 60
    while len(list(tmp_path.glob("go.ready.*"))) < 8:
        assert time.monotonic() < deadline, "children did not get ready"
        time.sleep(0.01)
    go.write_text("")
    results = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err.decode()
        results.append(json.loads(out))

    assert sorted(r["seq"] for r in results) == list(range(1, 9))
    assert sorted(r["attempt_id"] for r in results) == sorted(f"t{i}" for i in range(1, 9))
    engine_dir = batch_dir / "ocr" / "vision"
    assert sorted(p.name for p in engine_dir.iterdir()) == sorted(f"{fp}.t{i}" for i in range(1, 9))
    for r in results:
        path = Path(r["path"])
        assert path.is_dir()
        assert path.name == f"{fp}.{r['attempt_id']}"
        assert r["run_id"] == f"{batch_dir.name}/vision/{fp}/{r['attempt_id']}"


def test_attempt_monotonic_across_redo(tmp_path):
    """00b2-09 L2·S5: with t1 done, two redos give t2 then t3; t1 is never reused or touched."""
    batch_dir = _batch_dir(tmp_path)
    fp = _fp("cfg")
    first = ids.allocate_attempt(batch_dir, "claude", fp)
    assert first.attempt_id == "t1"
    (first.path / "receipt.json").write_text('{"status": "done"}', encoding="utf-8")
    before = sorted((p.name, p.read_bytes()) for p in first.path.iterdir())

    second = ids.allocate_attempt(batch_dir, "claude", fp)
    third = ids.allocate_attempt(batch_dir, "claude", fp)
    assert (second.attempt_id, third.attempt_id) == ("t2", "t3")
    assert (second.seq, third.seq) == (2, 3)
    assert sorted((p.name, p.read_bytes()) for p in first.path.iterdir()) == before
    assert list(second.path.iterdir()) == [] and list(third.path.iterdir()) == []


def test_attempt_never_fills_gaps(tmp_path):
    """Monotonic, not lowest-free: if t1..t2 were moved away, the next attempt is still t4 after t3."""
    batch_dir = _batch_dir(tmp_path)
    fp = _fp("cfg")
    for _ in range(3):
        ids.allocate_attempt(batch_dir, "codex", fp)
    trash = tmp_path / "_trash"
    trash.mkdir()
    for name in (f"{fp}.t1", f"{fp}.t2"):
        (batch_dir / "ocr" / "codex" / name).rename(trash / name)
    assert ids.allocate_attempt(batch_dir, "codex", fp).attempt_id == "t4"


def test_attempt_scoped_by_engine_and_fp(tmp_path):
    """00b2-10 L2·S2: each (engine, cfg_fp) pair has its own sequence starting at t1."""
    batch_dir = _batch_dir(tmp_path)
    fps = (_fp("cfg-a"), _fp("cfg-b"))
    got = {}
    for engine in ("vision", "codex"):
        for fp in fps:
            got[(engine, fp)] = ids.allocate_attempt(batch_dir, engine, fp)
    assert {k: a.attempt_id for k, a in got.items()} == {k: "t1" for k in got}
    assert len({a.path for a in got.values()}) == 4
    assert ids.allocate_attempt(batch_dir, "vision", fps[0]).attempt_id == "t2"
    assert ids.allocate_attempt(batch_dir, "codex", fps[1]).attempt_id == "t2"


def test_attempt_crash_after_claim_is_not_reused(tmp_path):
    """A process killed right after claiming t1 leaves t1 behind; the next allocation is t2 (S5)."""
    batch_dir = _batch_dir(tmp_path)
    fp = _fp("cfg")
    code = (
        "import sys; from pathlib import Path; from klode_kit.contract import ids; "
        "ids.allocate_attempt(Path(sys.argv[1]), 'mlx', sys.argv[2]); print('survived')"
    )
    res = subprocess.run(
        [sys.executable, "-c", code, str(batch_dir), fp],
        env=base_env({"KLODE_KIT_CRASH_AT": "ids.attempt_mkdir"}), capture_output=True, timeout=60,
    )
    assert res.returncode == -9, res.stderr.decode()
    assert b"survived" not in res.stdout
    assert (batch_dir / "ocr" / "mlx" / f"{fp}.t1").is_dir()
    assert ids.allocate_attempt(batch_dir, "mlx", fp).attempt_id == "t2"


@pytest.mark.parametrize(
    ("dir_name", "engine", "fp"),
    [("not-a-batch", "vision", "0" * 12), ("0" * 12, "Vision", "0" * 12), ("0" * 12, "vision", "short")],
)
def test_allocate_attempt_rejects_malformed(tmp_path, dir_name, engine, fp):
    d = tmp_path / dir_name
    d.mkdir()
    with pytest.raises(ValueError):
        ids.allocate_attempt(d, engine, fp)
    assert list(d.iterdir()) == []


# ---------------------------------------------------------------- build_id (00b2-15)


def test_build_id_uses_injected_clock():
    """00b2-15 L1·S2: the time part is the injected instant; equal inputs share the hash part."""
    t1 = CLOCK
    t2 = datetime(2027, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    a = ids.build_id(**_build_inputs(), now=t1)
    b = ids.build_id(**_build_inputs(), now=t2)
    assert a.startswith("b-20261022T000000Z-")
    assert b.startswith("b-20270102T030405Z-")
    assert a.rsplit("-", 1)[1] == b.rsplit("-", 1)[1]
    # an aware non-UTC instant is converted to UTC
    tokyo = t2.astimezone(timezone(timedelta(hours=9)))
    assert ids.build_id(**_build_inputs(), now=tokyo) == b


def test_build_id_rejects_naive_clock():
    with pytest.raises(ValueError):
        ids.build_id(**_build_inputs(), now=datetime(2026, 10, 22))


BUILD_FIELDS = ("rec_revs", "items_sha", "desk_sha", "holdout_sha", "config_version", "plugin_version", "klode_version")


@settings(max_examples=100, deadline=None)
@given(field=st.sampled_from(BUILD_FIELDS), salt=st.integers(0, 1000))
def test_build_id_hash_sensitivity(field, salt):
    """Any r3.2 build input changing changes the hash part; rec_revs order does not (S4)."""
    base = ids.build_id(**_build_inputs(), now=CLOCK)
    if field == "rec_revs":
        revs = dict(_build_inputs()["rec_revs"])
        revs["r-000000000001"] += 1 + salt
        changed = ids.build_id(**_build_inputs(rec_revs=revs), now=CLOCK)
        reordered = dict(reversed(list(_build_inputs()["rec_revs"].items())))
        assert ids.build_id(**_build_inputs(rec_revs=reordered), now=CLOCK) == base
    else:
        changed = ids.build_id(**_build_inputs(**{field: _mutate_value(_build_inputs()[field], salt)}), now=CLOCK)
    assert changed != base


# ---------------------------------------------------------------- idempotency key (r2.3)


@settings(max_examples=100, deadline=None)
@given(
    value=st.one_of(st.none(), st.text(max_size=8)),
    route=st.sampled_from(["vision", "mlx", "claude", "codex"]),
    rev_base=st.integers(0, 50),
)
def test_idempotency_key_law(value, route, rev_base):
    """(field_id, rev_base, choice_hash): key order of the choice is irrelevant; any part changing matters."""
    fid = "r-000000000001/labs[1].value"
    choice = {"choice": "route", "route": route, "value": value}
    key = ids.idempotency_key(fid, rev_base, choice)
    assert ids.idempotency_key(fid, rev_base, dict(reversed(list(choice.items())))) == key
    assert ids.idempotency_key(fid, rev_base + 1, choice) != key
    assert ids.idempotency_key("r-000000000001/labs[2].value", rev_base, choice) != key
    assert ids.idempotency_key(fid, rev_base, {**choice, "value": (value or "") + "!"}) != key
    assume(route != "codex")
    assert ids.idempotency_key(fid, rev_base, {**choice, "route": "codex"}) != key


# ---------------------------------------------------------------- added after red (guards; no recorded line changed)


@pytest.mark.parametrize("field", ["model", "effort", "prompt_sha", "schema_sha", "adapter_code_version"])
def test_cfg_fp_rejects_empty_inputs(field):
    with pytest.raises(ValueError):
        ids.cfg_fp(config.load(), **_fp_inputs(**{field: ""}))


def test_allocate_attempt_ignores_foreign_names(tmp_path):
    """Entries that share the fp prefix but are not ``t<seq>`` never count as attempts."""
    batch_dir = _batch_dir(tmp_path)
    fp = _fp("cfg")
    engine_dir = batch_dir / "ocr" / "vision"
    engine_dir.mkdir(parents=True)
    for name in (f"{fp}.tmp", f"{fp}.t0", f"{fp}.t07", f"{fp}.tx9", f"{_fp('other')}.t5"):
        (engine_dir / name).mkdir()
    assert ids.allocate_attempt(batch_dir, "vision", fp).attempt_id == "t1"


def test_allocate_attempt_parent_is_a_file(tmp_path):
    """A regular file where the ``ocr`` directory belongs is an error, not silently replaced."""
    batch_dir = _batch_dir(tmp_path)
    (batch_dir / "ocr").write_text("not a directory", encoding="utf-8")
    with pytest.raises(FileExistsError):
        ids.allocate_attempt(batch_dir, "vision", _fp("cfg"))
    assert (batch_dir / "ocr").read_text(encoding="utf-8") == "not a directory"
