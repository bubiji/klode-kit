"""WI-06b: Codex read-image adapter (matrix 06-02..07, 06-09, 06-10; plan WI-06b; design 02 r2.2-A, r2.3 reason
table, r2.6 exit 50, R2-08, R2-12 model_actual).

Public surface under test (``klode_kit.ocr.adapters_codex``), mirroring the claude adapter (WI-06a):

- ``read_page(path, der_sha256, *, page_id, project, receipt_dir, isolation_receipt, ledger, routes_state=None,
  config=None, source_env=None, timeout_s=None) -> PageRead(rows, receipt, receipt_path)`` sends the image with
  ``ocr.send.send`` on route ``codex`` and reads the answer from the ``--json`` event stream (last
  ``agent_message``): rows ``engine: codex`` with ``bbox_px: null``, ``tokens: null``, text verbatim;
- a timeout or a failed CLI (non-zero exit, ``turn.failed`` / ``error`` event) raises ``ReadFailed``
  (route_failed); an answer that breaks ``ocr_cloud_output`` is retried once, then ``ParseFailed`` (parse_error);
  ``SendRefused`` (60/61) passes through; ``codex.effort`` other than ``max`` raises ``EffortLocked`` (exit 40)
  before anything is reserved or started;
- codex reports no money: each call is charged ``estimate_cost_usd(tokens)`` (token usage of the stream), or the
  reservation when the usage is missing - so the exit-50 rule is the claude one (same ``Ledger``);
- page receipt ``read-codex-<sha>.<n>.json`` (``klode-kit.cloud_read/1``): status, model (configured), effort,
  model_actual, cost_usd, ``cost_estimated: true``, tokens (summed over attempts), attempts - never a path.

Boundaries: the WI-00c3 live recordings (tests/recordings/codex, unchanged) replayed by ``tests/fakes/bin/codex``
through replay scenarios written for the production argv (the recorded argv names the random send directory, so
the send directory name is pinned with a ``tempfile.mkdtemp`` stub - the one non-deterministic input) for 06-02;
``_support.send_fake.FakeCloud`` for behaviour. Paired real path: tests/model/test_adapter_codex_live.py (06-12,
gate G2).
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
from klode_kit.ocr import adapters_codex as AX
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC
from klode_kit.ocr import send as S
from klode_kit.ocr.cloud_budget import BudgetExceeded, Ledger
from contract.test_adapter_claude import Rig as ClaudeRig
from contract.test_adapter_claude import result_stdout as claude_stdout

pytestmark = pytest.mark.contract_replay

FIXTURES = REPO / "tests" / "fixtures"
RECORDINGS = sorted((REPO / "tests" / "recordings" / "codex").glob("*.json"))
FAKES_BIN = REPO / "tests" / "fakes" / "bin"
PAGE_ID = "d-0123456789ab/p001"
LINES = ["示例医院 检验报告单", "项目  结果 单位", "白细胞计数 6.5 ×10^9/L", "  前后空格  "]


def codex_stdout(lines, *, input_tokens=1000, output_tokens=500, uncertain=(), usage=True, tail=()) -> str:
    """A codex ``--json`` event stream: thread/turn start, one agent_message with the answer, turn.completed."""
    body = {"lines": [{"text": t, "uncertain": i in uncertain} for i, t in enumerate(lines)]}
    events = [{"type": "thread.started", "thread_id": "00000000-0000-0000-0000-000000000000"},
              {"type": "turn.started"},
              {"type": "item.completed",
               "item": {"id": "item_0", "type": "agent_message", "text": json.dumps(body, ensure_ascii=False)}}]
    if usage:
        events.append({"type": "turn.completed",
                       "usage": {"input_tokens": input_tokens, "cached_input_tokens": 0, "output_tokens": output_tokens}})
    events.extend(tail)
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)


def bad_stdout(text: str, *, input_tokens=1000, output_tokens=0) -> str:
    events = [{"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": text}},
              {"type": "turn.completed", "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}]
    return "".join(json.dumps(e) + "\n" for e in events)


MALFORMED = bad_stdout(json.dumps({"lines": [{"text": 7, "uncertain": False}]}))
NOT_JSON = bad_stdout("Here is the transcription: 示例医院")
NO_ANSWER = json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1000}}) + "\n"


def read_receipts(d: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("read-codex-*.json"))]


class Rig:
    """FakeCloud-backed codex adapter rig: fixture images (registered in tests/fixtures/MANIFEST.json)."""

    def __init__(self, tmp: Path, **state):
        self.tmp = tmp
        self.fake = FakeCloud(tmp / "fake", stdout=codex_stdout(LINES), **state)
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
        self.iso = isolation_receipt("codex", other, self.home, tmp / "iso", tmp / "iso-codex.json")

    def read(self, ledger: Ledger, image: Path = FIXTURE_PNG, **kw):
        data = image.read_bytes()
        kw.setdefault("project", self.project)
        kw.setdefault("receipt_dir", self.receipts)
        kw.setdefault("isolation_receipt", self.iso)
        kw.setdefault("routes_state", self.state)
        kw.setdefault("source_env", self.env)
        return AX.read_page(image, sha256(data), page_id=PAGE_ID, ledger=ledger, **kw)


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


# --------------------------------------------------------------------------- 06-02 (live recordings)


class RecordingRig:
    """tests/fakes/bin/codex in the scrubbed cloud environment. ``--version`` comes from a scenario; the model call
    from a scenario holding a WI-00c3 recording's stdout, filed under the sha of the recorded argv with
    ``<send_dir>`` replaced by the pinned send directory (and, for the xhigh recordings, the effort by the locked
    ``max``) - so the fake answers only if the adapter's argv equals the recorded one."""

    def __init__(self, tmp: Path, monkeypatch):
        self.replay = tmp / "replay"
        write_scenario(self.replay, "codex", ["--version"], f"codex-cli {IC.CODEX_MIN_VERSION}\n")
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
        res = IC.check(CA.codex(send_dir, model="gpt-6-sol", effort="max", source_env=self.env),
                       project=self.project)
        shutil.move(str(send_dir), str(tmp / "iso-send-dir"))
        assert res.ok, res.failed
        self.iso = tmp / "iso.json"
        self.iso.write_text(json.dumps(res.receipt()), encoding="utf-8")
        self.send_dir = self.systmp / f"{CA.SEND_PREFIX}pinned"

        def pinned_mkdtemp(suffix=None, prefix=None, dir=None):
            os.mkdir(self.send_dir, 0o700)
            return str(self.send_dir)

        monkeypatch.setattr(tempfile, "mkdtemp", pinned_mkdtemp)

    def expected_argv(self, rec: dict) -> list[str]:
        argv = [str(self.send_dir) if t == "<send_dir>" else t for t in rec["argv"]]
        return ["model_reasoning_effort=max" if t.startswith("model_reasoning_effort=") else t for t in argv]

    def file_recording(self, rec: dict) -> list[str]:
        argv = self.expected_argv(rec)
        write_scenario(self.replay, "codex", argv[1:], rec["stdout"], stderr=rec["stderr"],
                       exit_code=rec["exit_code"])
        return argv

    def model_calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [c for c in (json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines() if x.strip())
                if c["argv"] != ["--version"]]


@pytest.fixture
def recording_rig(tmp_path, systmp, monkeypatch) -> RecordingRig:
    return RecordingRig(tmp_path, monkeypatch)


def test_effort_max_recording_is_the_production_argv():
    """The effort-max recording was taken with today's production argv: same tokens, effort max."""
    (rec_path,) = [p for p in RECORDINGS if p.name.endswith(".effort-max.json")]
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    assert rec["effort"] == "max"
    assert "model_reasoning_effort=max" in rec["argv"]


@pytest.mark.parametrize("rec_path", RECORDINGS, ids=[p.name[:12] + p.name[64:] for p in RECORDINGS])
def test_replay_conforms_codex(recording_rig, rec_path):
    """06-02 (S2): each live recording, replayed for the production argv (gpt-6-sol / effort max, permission
    profile), gives ocr_line rows - bbox_px and tokens null, text = the recorded line verbatim, line_idx 0..n-1,
    der_sha256 the image sent; exactly one model call whose argv is the recorded one; the receipt has the
    configured model and effort, the recorded token usage, model_actual None (codex names no model), and an
    estimated cost from the tokens, which the ledger was charged."""
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    image = FIXTURES / rec["image"]
    assert sha256(image.read_bytes()) == rec["der_sha256"]
    argv = recording_rig.file_recording(rec)
    ledger = Ledger(5.0)
    out = AX.read_page(image, rec["der_sha256"], page_id=PAGE_ID, project=recording_rig.project,
                       receipt_dir=recording_rig.receipts, isolation_receipt=recording_rig.iso, ledger=ledger,
                       routes_state=recording_rig.state, source_env=recording_rig.env)
    (call,) = recording_rig.model_calls()
    assert ["codex", *call["argv"]] == argv
    assert [r["text"] for r in out.rows] == rec["lines"]
    for idx, row in enumerate(out.rows):
        schema.validate("ocr_line", row)
        assert row == {"engine": "codex", "page_id": PAGE_ID, "der_sha256": rec["der_sha256"], "line_idx": idx,
                       "text": rec["lines"][idx], "bbox_px": None, "tokens": None}
    est = AX.estimate_cost_usd(rec["tokens"])
    assert est is not None and est > 0
    assert out.receipt["status"] == "done"
    assert (out.receipt["model"], out.receipt["effort"]) == ("gpt-6-sol", "max")
    assert out.receipt["model_actual"] is None
    assert out.receipt["tokens"] == rec["tokens"]
    assert out.receipt["cost_estimated"] is True
    assert Decimal(str(out.receipt["cost_usd"])) == est
    assert ledger.spent_usd == est
    assert ledger.calls == 1
    assert json.loads(out.receipt_path.read_text(encoding="utf-8")) == out.receipt


def test_unrecorded_argv_gets_no_answer(recording_rig):
    """S3 (the replay is argv-bound): with no scenario for the production argv the fake answers 97 -> ReadFailed
    cli_failed after one call, nothing parsed."""
    ledger = Ledger(5.0)
    with pytest.raises(AX.ReadFailed) as info:
        AX.read_page(FIXTURE_PNG, sha256(FIXTURE_PNG.read_bytes()), page_id=PAGE_ID, project=recording_rig.project,
                     receipt_dir=recording_rig.receipts, isolation_receipt=recording_rig.iso, ledger=ledger,
                     routes_state=recording_rig.state, source_env=recording_rig.env)
    assert info.value.reason == "cli_failed"
    assert len(recording_rig.model_calls()) == 1


# --------------------------------------------------------------------------- success details


def test_rows_verbatim_and_uncertain_lines_recorded(rig):
    rig.fake.set(stdout=codex_stdout(LINES, uncertain=(0, 2)))
    out = rig.read(Ledger(1.0))
    assert [r["text"] for r in out.rows] == LINES
    assert out.receipt["uncertain_lines"] == [0, 2]
    assert out.receipt["lines"] == len(LINES)
    assert out.receipt["route"] == "codex"
    assert rig.fake.model_calls()[0]["pngs"] == {f"{sha256(FIXTURE_PNG.read_bytes())}.png":
                                                 sha256(FIXTURE_PNG.read_bytes())}


def test_send_dir_holds_exactly_three_files(rig):
    """The codex process sees exactly <sha>.png, prompt.txt, schema.json in its cwd (no output file option)."""
    rig.read(Ledger(1.0))
    (call,) = rig.fake.model_calls()
    assert call["cwd_files"] == sorted([f"{sha256(FIXTURE_PNG.read_bytes())}.png", "prompt.txt", "schema.json"])


def test_empty_page_gives_no_rows(rig):
    rig.fake.set(stdout=codex_stdout([]))
    out = rig.read(Ledger(1.0))
    assert out.rows == []
    assert out.receipt["status"] == "done"


def test_model_actual_from_response(rig):
    """06-09 (S1): an event naming another model than the configured 'gpt-6-sol' -> model_actual is the
    response's, model stays the configured value."""
    rig.fake.set(stdout=codex_stdout(LINES, tail=({"type": "turn.completed", "usage": {}, "model": "gpt-6-sol-b"},)))
    out = rig.read(Ledger(1.0))
    assert (out.receipt["model"], out.receipt["model_actual"]) == ("gpt-6-sol", "gpt-6-sol-b")


def test_unknown_usage_charges_reservation(rig):
    """S3: no usage in the stream -> the cost is unknown: the ledger is charged the reservation (never
    under-counted), the receipt says cost_complete false."""
    rig.fake.set(stdout=codex_stdout(LINES, usage=False))
    ledger = Ledger(1.0, reserve_usd=Decimal("0.3"))
    out = rig.read(ledger)
    assert ledger.spent_usd == Decimal("0.3")
    assert out.receipt["cost_complete"] is False
    assert out.receipt["tokens"] == {}


# --------------------------------------------------------------------------- effort lock


@pytest.mark.parametrize("effort", ["high", "xhigh", "minimal"])
def test_effort_lock_refuses_lower_effort(rig, effort):
    """S3: a project that lowers codex.effort -> EffortLocked (exit 40) before anything is reserved or started:
    no process at all (not even --version), no spend, no reservation outstanding."""
    (rig.project / "klode-kit.toml").write_text(f'[codex]\neffort = "{effort}"\n', encoding="utf-8")
    ledger = Ledger(1.0)
    with pytest.raises(AX.EffortLocked) as info:
        rig.read(ledger)
    assert info.value.exit_code == 40
    assert rig.fake.calls() == []
    assert (ledger.spent_usd, ledger.outstanding_usd, ledger.calls) == (Decimal("0"), Decimal("0"), 0)


# --------------------------------------------------------------------------- 06-03 timeout / failures


def test_timeout_route_failed(rig):
    """06-03 (S3): the CLI sleeps past the timeout (1 s) -> ReadFailed 'timeout'; receipt route_failed with the
    attempt timed out; the unknown cost is charged at the reservation."""
    rig.fake.set(sleep_s=5)
    ledger = Ledger(1.0, reserve_usd=Decimal("0.5"))
    with pytest.raises(AX.ReadFailed) as info:
        rig.read(ledger, timeout_s=1)
    assert info.value.reason == "timeout"
    (rec,) = read_receipts(rig.receipts)
    assert rec["status"] == "route_failed"
    assert rec["attempts"][0]["timed_out"] is True
    assert ledger.spent_usd == Decimal("0.5")
    assert len(rig.fake.model_calls()) == 1


@pytest.mark.parametrize("state", [
    pytest.param({"exit_code": 1, "stdout": ""}, id="exit-1-no-output"),
    pytest.param({"exit_code": 1, "stdout": codex_stdout([], tail=({"type": "error", "message": "boom"},))},
                 id="exit-1-error-event"),
    pytest.param({"exit_code": 0, "stdout": codex_stdout(LINES, tail=(
        {"type": "turn.failed", "error": {"message": "usage limit"}},))}, id="exit-0-turn-failed"),
])
def test_cli_failure_is_route_failed_not_retried(rig, state):
    """S3: a failing CLI (non-zero exit, or an error / turn.failed event) is ReadFailed after one call; the
    usage it reported is still charged."""
    rig.fake.set(**state)
    ledger = Ledger(1.0)
    with pytest.raises(AX.ReadFailed) as info:
        rig.read(ledger)
    assert info.value.reason == "cli_failed"
    assert len(rig.fake.model_calls()) == 1
    assert read_receipts(rig.receipts)[0]["status"] == "route_failed"
    assert ledger.calls == 1


# --------------------------------------------------------------------------- 06-04 / 06-05 parse retry


@pytest.mark.parametrize("bad", [MALFORMED, NOT_JSON, NO_ANSWER], ids=["schema-invalid", "not-json", "no-answer"])
def test_schema_invalid_retry_once_then_parse_error(rig, bad):
    """06-04 (S3): two unusable answers -> exactly 2 calls, ParseFailed (the claude adapter's class, so kb-ocr
    records parse_error), receipt parse_error; both calls charged."""
    rig.fake.set(stdout_seq=[bad, bad, codex_stdout(LINES)])
    ledger = Ledger(1.0)
    with pytest.raises(AC.ParseFailed):
        rig.read(ledger)
    assert len(rig.fake.model_calls()) == 2
    (rec,) = read_receipts(rig.receipts)
    assert rec["status"] == "parse_error"
    assert [a["status"] for a in rec["attempts"]] == ["parse_error", "parse_error"]
    assert ledger.calls == 2


def test_schema_invalid_then_valid(rig):
    """06-05 (S2): malformed (1000 input tokens = 0.01), then valid (0.04) -> 2 calls, valid rows, receipt done
    with 2 attempts, cost and tokens summed over both."""
    rig.fake.set(stdout_seq=[MALFORMED, codex_stdout(LINES)])
    ledger = Ledger(1.0)
    out = rig.read(ledger)
    assert len(rig.fake.model_calls()) == 2
    assert [r["text"] for r in out.rows] == LINES
    for row in out.rows:
        schema.validate("ocr_line", row)
    assert [a["status"] for a in out.receipt["attempts"]] == ["parse_error", "ok"]
    assert Decimal(str(out.receipt["cost_usd"])) == Decimal("0.05")
    assert ledger.spent_usd == Decimal("0.05")
    assert out.receipt["tokens"] == {"input_tokens": 2000, "cached_input_tokens": 0, "output_tokens": 500}


# --------------------------------------------------------------------------- refusals pass through


def test_unregistered_image_refused_no_charge(rig, tmp_path):
    stray = tmp_path / "stray.png"
    stray.write_bytes(b"\x89PNG not registered")
    ledger = Ledger(1.0)
    with pytest.raises(S.SendRefused) as info:
        rig.read(ledger, image=stray)
    assert info.value.exit_code == 60
    assert rig.fake.calls() == []
    assert (ledger.spent_usd, ledger.calls) == (Decimal("0"), 0)
    assert read_receipts(rig.receipts)[0]["status"] == "refused"


def test_gate_closed_exit61_zero_calls(tmp_path, systmp, monkeypatch):
    """S3: a codex that is not a test fake, gate closed -> SendRefused 61, nothing started, nothing charged."""
    rig = Rig(tmp_path)
    real_like = FakeCloud(tmp_path / "pseudo", pseudo_real=True, stdout=codex_stdout(LINES))
    monkeypatch.setenv("KLODE_KIT_REAL", "0")
    ledger = Ledger(1.0)
    with pytest.raises(S.SendRefused) as info:
        rig.read(ledger, source_env=source_env(real_like, rig.home, rig.systmp))
    assert info.value.exit_code == 61
    assert real_like.calls() == []
    assert ledger.spent_usd == Decimal("0")


# --------------------------------------------------------------------------- 06-06 / 06-07 budget


def test_budget_stop_exit50(rig):
    """06-06 (S3): limit 0.10, every call 0.04 (1000 input + 500 output tokens) -> calls 1-3 dispatched, the 4th
    reservation refused: BudgetExceeded exit 50; exactly 3 model calls."""
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
    assert sorted(r["status"] for r in read_receipts(rig.receipts)) == ["budget_stop"] * 2 + ["done"] * 3


def test_budget_boundary_exact(rig):
    """06-07 (S2): two calls of 0.05 (2000 input + 500 output) against 0.10 -> spend exactly 0.10, no exit 50."""
    rig.fake.set(stdout=codex_stdout(LINES, input_tokens=2000))
    ledger = Ledger(0.10)
    for image in five_images()[:2]:
        assert rig.read(ledger, image=image).receipt["status"] == "done"
    assert ledger.spent_usd == Decimal("0.10")
    assert not ledger.exceeded
    assert len(rig.fake.model_calls()) == 2


# --------------------------------------------------------------------------- 06-10 two routes, one ledger


def test_parallel_claude_and_codex_share_budget(tmp_path, systmp):
    """06-10 concrete, both routes: a claude thread and a codex thread read pages through one ledger (limit 0.10,
    every call 0.04, reservation 0.04) -> at most 3 calls in total across both CLIs, spend <= limit + one
    reservation, and after the first refusal neither route starts a call."""
    (tmp_path / "c").mkdir()
    (tmp_path / "x").mkdir()
    crig = ClaudeRig(tmp_path / "c")
    crig.fake.set(stdout=claude_stdout(LINES, cost=0.04), sleep_s=0.3)
    xrig = Rig(tmp_path / "x")
    xrig.fake.set(sleep_s=0.3)
    ledger = Ledger(0.10, reserve_usd=Decimal("0.04"))
    errors: list[BaseException] = []

    def worker(rig, images):
        for image in images:
            try:
                rig.read(ledger, image=image)
            except BudgetExceeded:
                return
            except BaseException as exc:  # pragma: no cover - surfaced by the assert below
                errors.append(exc)

    imgs = five_images()
    threads = [threading.Thread(target=worker, args=(crig, imgs)), threading.Thread(target=worker, args=(xrig, imgs))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    assert errors == []
    calls = len(crig.fake.model_calls()) + len(xrig.fake.model_calls())
    assert calls == ledger.calls <= 3
    assert ledger.spent_usd <= Decimal("0.14")
    assert ledger.stopped


# --------------------------------------------------------------------------- receipt privacy


NAMES = st.text(alphabet=st.sampled_from(list("数据项目甲乙 abcXYZ-_.")), min_size=1, max_size=12).filter(
    lambda s: s.strip(" .") != "")


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(dirname=NAMES, filename=NAMES)
def test_adapter_receipt_no_path_leak(rig, dirname, filename):
    """S4: an image stored under any directory / file name -> neither name appears in the codex page receipt."""
    marker_dir, marker_file = f"Q目录{dirname}", f"Q文件{filename}"
    where = rig.tmp / "store" / marker_dir
    where.mkdir(parents=True, exist_ok=True)
    image = where / f"{marker_file}.png"
    shutil.copyfile(FIXTURE_PNG, image)
    out = rig.read(Ledger(5.0), image=image)
    text = out.receipt_path.read_text(encoding="utf-8")
    assert marker_dir not in text and marker_file not in text
    assert str(rig.tmp) not in text
