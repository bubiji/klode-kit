"""WI-06a: Claude read-image adapter (matrix 06-01, 06-03..07, 06-09, 06-10; plan WI-06a; design 02 r2.2-A,
r2.3 reason table, r2.6 exit 50, R2-08, R2-12 model_actual).

Public surface under test (``klode_kit.ocr.adapters_claude`` / ``klode_kit.ocr.cloud_budget``):

- ``read_page(path, der_sha256, *, page_id, project, receipt_dir, isolation_receipt, ledger, routes_state=None,
  config=None, source_env=None, timeout_s=None) -> PageRead(rows, receipt, receipt_path)`` sends the image with
  ``ocr.send.send`` (never around it) and turns the CLI's structured output into ``ocr_line`` rows:
  ``engine: claude``, the given ``page_id``, ``der_sha256`` = the image sent, ``line_idx`` 0..n-1, ``text`` the
  model's line verbatim, ``bbox_px: null``, ``tokens: null``;
- a timeout or a failed CLI raises ``ReadFailed`` (route_failed); output that breaks ``ocr_cloud_output`` is
  retried once, then ``ParseFailed`` (parse_error); ``SendRefused`` (60/61) passes through;
- the page receipt ``read-claude-<sha>.<n>.json`` (``klode-kit.cloud_read/1``) carries ``status``, ``model``
  (configured), ``effort``, ``model_actual`` (from the response), ``cost_usd`` and the attempts - never a path;
- ``Ledger(limit_usd, reserve_usd=...)``: every call reserves before dispatch and is settled with the reported
  cost (unknown cost: the reservation); a reservation is refused (``BudgetExceeded``, exit 50) once nothing is
  left; total spend <= limit + one reservation.

Boundaries: the WI-00c3 live recordings replayed by ``tests/fakes/bin/claude`` (argv-sha lookup; the production
argv must equal the recorded one) for 06-01/06-09; ``_support.send_fake.FakeCloud`` (scripted stdout, sleep,
per-call sequence) for behaviour. Paired real path: tests/model/test_adapter_claude_live.py (06-11, gate G2).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from _support.replay import write_scenario
from _support.send_fake import FIXTURE_PNG, FakeCloud, isolation_receipt, routes_state, sha256, source_env
from klode_kit.contract import schema
from klode_kit.ocr import adapters_claude as AC
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import send as S
from klode_kit.ocr.cloud_budget import BudgetExceeded, Ledger

pytestmark = pytest.mark.contract_replay

FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = REPO / "tests" / "recordings" / "claude"
FAKES_BIN = REPO / "tests" / "fakes" / "bin"
PAGE_ID = "d-0123456789ab/p001"
RECORDED = sorted(RECORDINGS.glob("*.json"))


def result_stdout(lines, *, cost=0.04, model="claude-opus-5-5", uncertain=(), is_error=False,
                  subtype="success") -> str:
    body = {"type": "result", "subtype": subtype, "is_error": is_error, "total_cost_usd": cost,
            "modelUsage": {model: {"costUSD": cost}} if model else {},
            "structured_output": {"lines": [{"text": t, "uncertain": i in uncertain} for i, t in enumerate(lines)]}}
    return json.dumps(body, ensure_ascii=False) + "\n"


MALFORMED = json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.01,
                        "structured_output": {"lines": [{"text": 7}]}}) + "\n"
NOT_JSON = "the model said something else\n"
LINES = ["示例医院 检验报告单", "项目  结果 单位", "白细胞计数 6.5 ×10^9/L", "  前后空格  "]


def read_receipts(d: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("read-claude-*.json"))]


class Rig:
    """FakeCloud-backed adapter rig: fixture images (registered in tests/fixtures/MANIFEST.json)."""

    def __init__(self, tmp: Path, **state):
        self.tmp = tmp
        self.fake = FakeCloud(tmp / "fake", stdout=result_stdout(LINES), **state)
        self.home = tmp / "home"
        self.home.mkdir(exist_ok=True)
        self.systmp = tmp / "systmp"
        self.systmp.mkdir(exist_ok=True)
        self.project = tmp / "proj"
        self.project.mkdir(exist_ok=True)
        self.receipts = tmp / "receipts"
        self.state = routes_state(tmp / "routes.json")
        self.env = source_env(self.fake, self.home, self.systmp)
        other = FakeCloud(tmp / "fake-iso")
        self.iso = isolation_receipt("claude", other, self.home, tmp / "iso", tmp / "iso-claude.json")

    def read(self, ledger: Ledger, image: Path = FIXTURE_PNG, **kw):
        data = image.read_bytes()
        kw.setdefault("project", self.project)
        kw.setdefault("receipt_dir", self.receipts)
        kw.setdefault("isolation_receipt", self.iso)
        kw.setdefault("routes_state", self.state)
        kw.setdefault("source_env", self.env)
        return AC.read_page(image, sha256(data), page_id=PAGE_ID, ledger=ledger, **kw)


@pytest.fixture
def systmp(tmp_path, monkeypatch):
    d = tmp_path / "systmp"
    d.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(d))
    return d


@pytest.fixture
def rig(tmp_path, systmp) -> Rig:
    return Rig(tmp_path)


def five_images() -> list[Path]:
    return [FIXTURES / "fx-u1-3labs" / "images" / f"p00{i}.png" for i in (1, 2, 3)] + [
        FIXTURES / "fx-rx-stamp" / "images" / "p001.png", FIXTURES / "fx-lowres" / "images" / "p001.png"]


# --------------------------------------------------------------------------- 06-01 / 06-09 (live recordings)


class RecordingRig:
    """tests/fakes/bin/claude in the scrubbed cloud environment: ``--version`` from a scenario, the model call
    from the WI-00c3 recording whose argv_sha equals the production argv (HOME settings point the fake at its
    log and scenario dir, because the r5 allowlist drops KLODE_KIT_* variables)."""

    def __init__(self, tmp: Path, monkeypatch):
        self.replay = tmp / "replay"
        write_scenario(self.replay, "claude", ["--version"], f"{IC.CLAUDE_MIN_VERSION} (Claude Code)\n")
        monkeypatch.setenv("KLODE_KIT_REPLAY_DIR", str(self.replay))
        self.home = tmp / "home"
        self.home.mkdir()
        self.log = tmp / "model-calls.jsonl"
        (self.home / ".klode-kit-fake.json").write_text(
            json.dumps({"log": str(self.log), "replay_dir": str(self.replay)}), encoding="utf-8")
        self.systmp = tmp / "systmp"
        self.env = {"PATH": os.pathsep.join([str(FAKES_BIN), "/usr/bin", "/bin"]), "HOME": str(self.home),
                    "TMPDIR": str(self.systmp), "LANG": "C.UTF-8", "USER": "synthetic"}
        self.project = tmp / "proj"
        self.project.mkdir()
        self.receipts = tmp / "receipts"
        self.state = routes_state(tmp / "routes.json")
        data = FIXTURE_PNG.read_bytes()
        send_dir = CA.prepare_send_dir(data, sha256(data))
        res = IC.check(CA.claude(send_dir, model="opus", effort="max", max_budget_usd=5.0, source_env=self.env),
                       project=self.project)
        shutil.move(str(send_dir), str(tmp / "iso-send-dir"))
        assert res.ok, res.failed
        self.iso = tmp / "iso.json"
        self.iso.write_text(json.dumps(res.receipt()), encoding="utf-8")

    def model_calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip()]


@pytest.fixture
def recording_rig(tmp_path, systmp, monkeypatch) -> RecordingRig:
    return RecordingRig(tmp_path, monkeypatch)


def test_recordings_present():
    """The seven WI-00c3 claude recordings are the replay corpus (a vanished corpus must not pass silently)."""
    assert len(RECORDED) == 7


@pytest.mark.parametrize("rec_path", RECORDED, ids=[p.stem[:12] for p in RECORDED])
def test_replay_conforms_claude(recording_rig, rec_path):
    """06-01 (S2) + 06-09 (S1): each live recording, replayed for the production argv (opus / max /
    --max-budget-usd 5), gives ocr_line rows - bbox_px and tokens null, text = the recorded line verbatim, line_idx
    0..n-1, der_sha256 the image sent; the receipt has the configured model, model_actual from the response (not
    the configured alias) and the recorded cost, which the ledger was charged."""
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    image = FIXTURES / rec["image"]
    assert sha256(image.read_bytes()) == rec["der_sha256"]
    ledger = Ledger(5.0, reserve_usd=Decimal("5.0"))
    out = AC.read_page(image, rec["der_sha256"], page_id=PAGE_ID, project=recording_rig.project,
                       receipt_dir=recording_rig.receipts, isolation_receipt=recording_rig.iso, ledger=ledger,
                       routes_state=recording_rig.state, source_env=recording_rig.env)
    assert len(recording_rig.model_calls()) == 1
    assert [r["text"] for r in out.rows] == rec["lines"]
    for idx, row in enumerate(out.rows):
        schema.validate("ocr_line", row)
        assert row == {"engine": "claude", "page_id": PAGE_ID, "der_sha256": rec["der_sha256"], "line_idx": idx,
                       "text": rec["lines"][idx], "bbox_px": None, "tokens": None}
        assert row["text"][0:len(rec["lines"][idx])] == rec["lines"][idx]  # span [0, len) holds in the row text
    stdout = json.loads(rec["stdout"])
    assert out.receipt["status"] == "done"
    assert out.receipt["model"] == "opus"
    assert out.receipt["effort"] == "max"
    assert out.receipt["model_actual"] == next(iter(stdout["modelUsage"])) != "opus"
    assert out.receipt["cost_usd"] == pytest.approx(rec["cost_usd"])
    assert float(ledger.spent_usd) == pytest.approx(rec["cost_usd"])
    assert ledger.calls == 1
    assert json.loads(out.receipt_path.read_text(encoding="utf-8")) == out.receipt


# --------------------------------------------------------------------------- success details


def test_rows_verbatim_and_uncertain_lines_recorded(rig):
    """Text is kept byte for byte (inner/outer spaces, full-width), lines the model marked uncertain are listed in
    the receipt by index (ocr_line has no field for it)."""
    rig.fake.set(stdout=result_stdout(LINES, uncertain=(1, 3)))
    out = rig.read(Ledger(1.0))
    assert [r["text"] for r in out.rows] == LINES
    assert out.receipt["uncertain_lines"] == [1, 3]
    assert out.receipt["lines"] == len(LINES)
    assert rig.fake.model_calls()[0]["pngs"] == {f"{sha256(FIXTURE_PNG.read_bytes())}.png":
                                                 sha256(FIXTURE_PNG.read_bytes())}


def test_empty_page_gives_no_rows(rig):
    """S2: a page the model reads as empty is valid output: zero rows, status done (the batch decides what an
    empty page means)."""
    rig.fake.set(stdout=result_stdout([]))
    out = rig.read(Ledger(1.0))
    assert out.rows == []
    assert out.receipt["status"] == "done"


def test_model_actual_from_response(rig):
    """06-09 (S1): the configured alias is 'opus'; the response names another model -> model_actual is the
    response's, model stays the configured value."""
    rig.fake.set(stdout=result_stdout(LINES, model="claude-sonnet-9-1"))
    out = rig.read(Ledger(1.0))
    assert (out.receipt["model"], out.receipt["model_actual"]) == ("opus", "claude-sonnet-9-1")


def test_model_actual_none_when_response_has_no_usage(rig):
    rig.fake.set(stdout=result_stdout(LINES, model=None))
    out = rig.read(Ledger(1.0))
    assert out.receipt["model_actual"] is None
    assert out.receipt["status"] == "done"


# --------------------------------------------------------------------------- 06-03 timeout / failures


def test_timeout_route_failed(rig):
    """06-03 (S3): the CLI sleeps past the timeout (1 s) -> ReadFailed 'timeout'; the receipt says route_failed
    with the attempt timed out; the unknown cost is charged at the reservation (never under-counted)."""
    rig.fake.set(sleep_s=5)
    ledger = Ledger(1.0, reserve_usd=Decimal("0.5"))
    with pytest.raises(AC.ReadFailed) as info:
        rig.read(ledger, timeout_s=1)
    assert info.value.reason == "timeout"
    (rec,) = read_receipts(rig.receipts)
    assert rec["status"] == "route_failed"
    assert rec["attempts"][0]["timed_out"] is True
    assert ledger.spent_usd == Decimal("0.5")
    assert len(rig.fake.model_calls()) == 1


@pytest.mark.parametrize("state", [
    pytest.param({"exit_code": 3, "stdout": ""}, id="exit-3-no-output"),
    pytest.param({"exit_code": 1, "stdout": result_stdout([], is_error=True, subtype="error_max_budget_usd")},
                 id="exit-1-is-error"),
    pytest.param({"exit_code": 0, "stdout": result_stdout(LINES, is_error=True, subtype="error_during_execution")},
                 id="exit-0-is-error"),
])
def test_cli_failure_is_route_failed_not_retried(rig, state):
    """S3: a failing CLI (non-zero exit, or is_error) is ReadFailed after one call - the parse retry is only for
    output that breaks the schema."""
    rig.fake.set(**state)
    with pytest.raises(AC.ReadFailed):
        rig.read(Ledger(1.0))
    assert len(rig.fake.model_calls()) == 1
    assert read_receipts(rig.receipts)[0]["status"] == "route_failed"


# --------------------------------------------------------------------------- 06-04 / 06-05 parse retry


@pytest.mark.parametrize("bad", [MALFORMED, NOT_JSON], ids=["schema-invalid", "not-json"])
def test_schema_invalid_retry_once_then_parse_error(rig, bad):
    """06-04 (S3): two malformed answers -> exactly 2 calls, ParseFailed, receipt parse_error; both calls charged."""
    rig.fake.set(stdout_seq=[bad, bad, result_stdout(LINES)])
    ledger = Ledger(1.0, reserve_usd=Decimal("0.25"))
    with pytest.raises(AC.ParseFailed):
        rig.read(ledger)
    assert len(rig.fake.model_calls()) == 2
    (rec,) = read_receipts(rig.receipts)
    assert rec["status"] == "parse_error"
    assert [a["status"] for a in rec["attempts"]] == ["parse_error", "parse_error"]
    assert ledger.calls == 2


def test_schema_invalid_then_valid(rig):
    """06-05 (S2): malformed, then valid -> 2 calls, valid rows, receipt done with 2 attempts, cost summed."""
    rig.fake.set(stdout_seq=[MALFORMED, result_stdout(LINES, cost=0.03)])
    ledger = Ledger(1.0)
    out = rig.read(ledger)
    assert len(rig.fake.model_calls()) == 2
    assert [r["text"] for r in out.rows] == LINES
    for row in out.rows:
        schema.validate("ocr_line", row)
    assert out.receipt["status"] == "done"
    assert [a["status"] for a in out.receipt["attempts"]] == ["parse_error", "ok"]
    assert out.receipt["cost_usd"] == pytest.approx(0.04)
    assert ledger.spent_usd == Decimal("0.04")


# --------------------------------------------------------------------------- refusals pass through


def test_unregistered_image_refused_no_charge(rig, tmp_path):
    """S3: an image not in any MANIFEST -> SendRefused 60 from the sender, zero calls, the reservation released
    (no spend, no call counted)."""
    stray = tmp_path / "stray.png"
    stray.write_bytes(b"\x89PNG not registered")
    ledger = Ledger(1.0)
    with pytest.raises(S.SendRefused) as info:
        rig.read(ledger, image=stray)
    assert info.value.exit_code == 60
    assert rig.fake.calls() == []
    assert (ledger.spent_usd, ledger.calls) == (Decimal("0"), 0)
    assert read_receipts(rig.receipts)[0]["status"] == "refused"


# --------------------------------------------------------------------------- 06-06 / 06-07 budget


def test_budget_stop_exit50(rig):
    """06-06 (S3): limit 0.10, every call 0.04, five pages -> calls 1-3 are dispatched (spend 0.08 < 0.10 before
    the third), the 4th reservation is refused: BudgetExceeded exit 50; exactly 3 model calls; nothing after."""
    rig.fake.set(stdout=result_stdout(LINES, cost=0.04))
    ledger = Ledger(0.10)
    done, stopped = [], []
    for image in five_images():
        try:
            done.append(rig.read(ledger, image=image))
        except BudgetExceeded as exc:
            stopped.append(exc)
    assert len(done) == 3
    assert len(stopped) == 2 and all(e.exit_code == 50 for e in stopped)
    assert len(rig.fake.model_calls()) == 3
    assert ledger.spent_usd == Decimal("0.12")
    assert ledger.exceeded


def test_budget_boundary_exact(rig):
    """06-07 (S2): two calls of 0.05 against 0.10 -> spend exactly 0.10, no BudgetExceeded (the rule is 'more
    than'), both pages read."""
    rig.fake.set(stdout=result_stdout(LINES, cost=0.05))
    ledger = Ledger(0.10)
    for image in five_images()[:2]:
        assert rig.read(ledger, image=image).receipt["status"] == "done"
    assert ledger.spent_usd == Decimal("0.10")
    assert not ledger.exceeded
    assert len(rig.fake.model_calls()) == 2


def test_budget_reservation_is_the_cli_budget(rig):
    """The amount reserved before dispatch is what the CLI may spend: --max-budget-usd = min(reserve, limit)."""
    ledger = Ledger(0.3, reserve_usd=Decimal("1.0"))
    rig.read(ledger)
    argv = rig.fake.model_calls()[0]["argv"]
    assert argv[argv.index("--max-budget-usd") + 1] == "0.3"


# --------------------------------------------------------------------------- 06-10 concurrency


COSTS = st.lists(st.decimals(min_value=Decimal("0"), max_value=Decimal("0.05"), places=3), min_size=1, max_size=12)


@settings(max_examples=150, deadline=None)
@given(limit=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("0.30"), places=2), a=COSTS, b=COSTS)
def test_parallel_routes_budget_shared(limit, a, b):
    """06-10 (S4) ledger law: two routes reserve -> spend -> settle concurrently from one ledger (reservation
    0.05 = the most one call may cost). Total spend <= limit + one reservation; once a route is refused, no
    route is admitted again; every admitted call is settled exactly once."""
    reserve = Decimal("0.05")
    ledger = Ledger(limit, reserve_usd=reserve)
    admitted_after_refusal: list[bool] = []
    refused = threading.Event()
    barrier = threading.Barrier(2)

    def route(costs):
        barrier.wait()
        for cost in costs:
            try:
                r = ledger.reserve()
            except BudgetExceeded:
                refused.set()
                return
            admitted_after_refusal.append(refused.is_set())
            ledger.settle(r, float(cost))

    threads = [threading.Thread(target=route, args=(c,)) for c in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not any(t.is_alive() for t in threads)
    assert ledger.spent_usd <= limit + reserve
    assert ledger.outstanding_usd == 0
    assert not any(admitted_after_refusal)
    if sum(a) + sum(b) <= limit - reserve:  # never near the limit: nobody is refused
        assert ledger.calls == len(a) + len(b)


def test_parallel_reads_share_budget(rig):
    """06-10 concrete: two threads read pages through one ledger (limit 0.10, cost 0.04, reservation 0.04) ->
    at most 3 calls happen in total and the spend stays <= limit + one reservation."""
    rig.fake.set(stdout=result_stdout(LINES, cost=0.04), sleep_s=0.3)
    ledger = Ledger(0.10, reserve_usd=Decimal("0.04"))
    errors: list[BaseException] = []

    def worker(images):
        for image in images:
            try:
                rig.read(ledger, image=image)
            except BudgetExceeded:
                return
            except BaseException as exc:  # pragma: no cover - surfaced by the assert below
                errors.append(exc)

    imgs = five_images()
    threads = [threading.Thread(target=worker, args=(imgs[i::2],)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert errors == []
    assert len(rig.fake.model_calls()) == ledger.calls <= 3
    assert ledger.spent_usd <= Decimal("0.14")


# --------------------------------------------------------------------------- receipt privacy


NAMES = st.text(alphabet=st.sampled_from(list("数据项目甲乙 abcXYZ-_.")), min_size=1, max_size=12).filter(
    lambda s: s.strip(" .") != "")


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(dirname=NAMES, filename=NAMES)
def test_adapter_receipt_no_path_leak(rig, dirname, filename):
    """S4 (like 05b-11, for the adapter's own receipt): an image stored under any directory / file name (Chinese,
    spaces) -> neither name appears in the page receipt."""
    marker_dir, marker_file = f"Q目录{dirname}", f"Q文件{filename}"
    where = rig.tmp / "store" / marker_dir
    where.mkdir(parents=True, exist_ok=True)
    image = where / f"{marker_file}.png"
    shutil.copyfile(FIXTURE_PNG, image)
    out = rig.read(Ledger(5.0), image=image)
    text = out.receipt_path.read_text(encoding="utf-8")
    assert marker_dir not in text and marker_file not in text
    assert str(rig.tmp) not in text
