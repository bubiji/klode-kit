"""WI-08c: laws of the two-layer record (matrix 08c-08, 08c-09; design 02 r4.1; K3).

Random sequences of operations on the single record of fx-redo, each run through the real stages in process:

- ``redo_t1`` / ``redo_t2`` / ``redo_t3``: a new kb-ocr attempt read from ``engine`` (codex disagrees on one value
  -> pending_confirm), ``engine_t2`` (codex lost the page -> disputed) or ``engine_t3`` (four routes agree ->
  verified auto), then ``kb-audit`` (new a<N>) and the writeback ingest (``writeback.run`` without choices);
- ``valid`` / ``none``: the newest revision's confirm page exported with route clicks (``none``: one field left as
  "none of these"), ``rev_base`` = the current rev, submitted;
- ``stale``: legal clicks with a wrong ``rev_base``; ``resubmit``: the last submitted choices again;
  ``old``: a choices file of an older audit revision (an old candidate).

Law (08c-08, S5): effective.rev never decreases and moves only to a candidate added by that very step whose status
is verified; after every advance ``effective`` equals the promoted candidate on ``{rev, audit_rev, status,
verified_by}`` and every earlier candidate carries ``superseded_by``; no old candidate ever overrides a newer rev.
Law (08c-09, S5): with a verified r1, disputed or pending candidates never change the effective bytes nor
``_rev/X.r1.md``.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from _support import writeback_fx as W
from klode_kit.contract import schema

pytestmark = pytest.mark.contract_replay

ENGINES = {"redo_t1": "engine", "redo_t2": "engine_t2", "redo_t3": "engine_t3"}


def _rev_no(audit_rev: str) -> int:
    return int(audit_rev.rsplit("/a", 1)[1])


class Sim:
    def __init__(self, root: Path):
        self.root = root
        self.project = root / "proj"
        self.project.mkdir()
        self.seq = 1
        self.b, self.adir = W.audited(self.project, W.REDO)
        self.rec_id = W.candidates(self.adir)[0]["rec_id"]
        self.history: list[dict] = []
        self.ingest()

    def ingest(self) -> None:
        code, _, err = W.submit(self.project, None, self.root, batch_id=self.b)
        assert code in (0, 10), err

    def front(self) -> dict:
        f = W.front(self.project, self.rec_id)
        assert f is not None
        return f

    def current(self) -> int:
        return max(c["rev"] for c in self.front()["candidates"])

    def redo(self, op: str) -> None:
        self.seq += 1
        self.b, self.adir = W.audited(self.project, W.REDO, engine=ENGINES[op], seq=self.seq)
        self.ingest()

    def clicks(self, *, none: bool) -> dict | None:
        todo = W.to_confirm(self.adir).get(self.rec_id)
        if not todo:
            return None
        sel = W.picks(self.adir, [self.rec_id])
        if none:
            sel[0] = {"field_id": sel[0]["field_id"], "kind": "none"}
        ch = W.forged(W.export(self.project, self.b, self.adir, sel))
        for it in ch["items"]:
            it["rev_base"] = self.current()
        return ch

    def submit(self, ch: dict) -> int:
        code, _, err = W.submit(self.project, ch, self.root)
        self.history.append(ch)
        return code

    def step(self, op: str) -> str:
        """Run one operation; returns what kind of outcome the law expects."""
        if op in ENGINES:
            self.redo(op)
            return "ingest"
        if op in ("valid", "none", "stale"):
            ch = self.clicks(none=op == "none")
            if ch is None:
                return "skip"
            if op == "stale":
                for it in ch["items"]:
                    it["rev_base"] += 1
                assert self.submit(ch) == 30
                return "refused"
            assert self.submit(ch) in (0, 10)
            return "commit"
        if op == "resubmit":
            if not self.history:
                return "skip"
            self.submit(W.forged(self.history[-1]))
            return "no_new_rev"
        if op == "old":
            old = [h for h in self.history if h["audit_rev"] != f"{self.b}/{self.adir.name}"]
            if not old:
                return "skip"
            assert self.submit(W.forged(old[0])) == 30
            return "refused"
        raise AssertionError(op)  # pragma: no cover - test bug


def check_law(before: dict, after: dict, outcome: str) -> None:
    assert schema.issues("record", after) == [], schema.issues("record", after)
    cands = after["candidates"]
    assert [c["rev"] for c in cands] == list(range(1, len(cands) + 1))
    assert [_rev_no(c["audit_rev"]) for c in cands] == sorted(_rev_no(c["audit_rev"]) for c in cands)
    added = cands[len(before["candidates"]):]
    for old, new in zip(before["candidates"], cands):
        assert {k: v for k, v in old.items() if k != "superseded_by"} == \
               {k: v for k, v in new.items() if k != "superseded_by"}
        if "superseded_by" in old:
            assert new["superseded_by"] == old["superseded_by"]
    if outcome in ("refused", "no_new_rev", "skip"):
        assert added == []
        assert after == before
        return
    assert len(added) <= 1
    e0, e1 = before.get("effective"), after.get("effective")
    r0 = e0["rev"] if e0 else 0
    r1 = e1["rev"] if e1 else 0
    assert r1 >= r0
    if added and added[0]["status"] == "verified":
        new = added[0]
        assert e1 == {k: new[k] for k in ("rev", "audit_rev", "status", "verified_by")}
    else:
        assert e1 == e0
    if e1:
        promoted = next(c for c in cands if c["rev"] == e1["rev"])
        assert e1 == {k: promoted[k] for k in ("rev", "audit_rev", "status", "verified_by")}
        for c in cands:
            if c["rev"] < e1["rev"]:
                assert c["rev"] < c["superseded_by"] <= e1["rev"], c
            else:
                assert "superseded_by" not in c, c
        assert after["status"] == e1["status"]
    else:
        assert all("superseded_by" not in c for c in cands)
        assert after["status"] == cands[-1]["status"]


OPS = st.lists(st.sampled_from(["redo_t1", "redo_t2", "redo_t3", "valid", "none", "stale", "resubmit", "old"]),
               min_size=1, max_size=7)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(OPS)
@example(["valid", "redo_t2", "old", "redo_t3", "resubmit"])
@example(["none", "valid", "redo_t1", "valid", "redo_t3"])
@example(["valid", "redo_t1", "none", "stale", "redo_t3", "old"])
def test_effective_rev_monotonic(ops):
    """08c-08 (S5): see the module docstring."""
    with tempfile.TemporaryDirectory() as tmp:
        sim = Sim(Path(tmp))
        for op in ops:
            before = sim.front()
            outcome = sim.step(op)
            check_law(before, sim.front(), outcome)


TAIL = st.lists(st.sampled_from(["redo_t1", "redo_t2", "none"]), min_size=1, max_size=5)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(TAIL)
@example(["redo_t2", "redo_t1", "none", "redo_t2"])
def test_failed_candidate_never_moves_effective(ops):
    """08c-09 (S5): a verified r1 (human), then disputed or pending candidates only: the effective block is
    byte-identical to its r1 state and ``_rev/X.r1.md`` keeps its sha."""
    with tempfile.TemporaryDirectory() as tmp:
        sim = Sim(Path(tmp))
        assert sim.step("valid") == "commit"
        front = sim.front()
        assert front["effective"]["status"] == "verified"
        eff = json.dumps(front["effective"], sort_keys=True)
        r = front["effective"]["rev"]
        body_sha = W.sha(W.body_path(sim.project, sim.rec_id, r))
        for op in ops:
            n = len(sim.front()["candidates"])
            sim.step(op)
            after = sim.front()
            assert json.dumps(after["effective"], sort_keys=True) == eff
            assert after["status"] == "verified"
            assert all(c["status"] != "verified" for c in after["candidates"][n:])
            assert W.sha(W.body_path(sim.project, sim.rec_id, r)) == body_sha
