"""WI-06b unit cases: the codex argv the adapter sends (matrix 06-08), the ``--json`` event-stream parser and the
token-based cost estimate the batch ledger is charged with (plan WI-06b; design 02 r2.6, R2-08, R2-12).

Public surface under test (``klode_kit.ocr.adapters_codex``):

- ``EFFORT_LOCK == "max"``: the codex effort is locked to the highest level (WI-00c3 decision, "never downgrade");
- ``parse_events(stdout) -> Parsed(lines, tokens, cost_usd, model_actual, is_error, problem)``: ``stdout`` is the
  ``codex exec --json`` JSONL event stream; the answer is the LAST ``item.completed`` whose item is an
  ``agent_message``, its ``text`` parsed as JSON and checked against ``schemas/ocr_cloud_output.json`` (``lines``
  None + ``problem`` otherwise); ``tokens`` sums the integer fields of every ``turn.completed.usage``; an
  ``error`` / ``turn.failed`` event sets ``is_error``; ``model_actual`` is a ``model`` string carried by an event
  (codex 0.163.0 carries none: then None);
- ``estimate_cost_usd(tokens) -> Decimal | None``: USD estimate from the priced token kinds (``EST_USD_PER_MTOK``);
  None when no priced kind is present (the ledger then charges the reservation).

Law (S4): the estimate is additive over usage (two turns cost the sum of each), monotone in every token count and
never negative - so summing turn usages before or after estimating gives the same charge.
No mocks: pure functions over strings; the recordings are WI-00c3 live captures (tests/recordings/codex).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit import config as config_mod
from klode_kit.ocr import adapters_codex as AX
from klode_kit.ocr import cloud_argv as CA

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
RECORDINGS = sorted((REPO / "tests" / "recordings" / "codex").glob("*.json"))
MAX_REC = REPO / "tests" / "recordings" / "codex" / \
    "3e68e97d5936c3c9df501b6009171de839302556c1ad08fe5200834240d46f80.effort-max.json"


def stream(*events: dict) -> str:
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)


def message(obj, idx: int = 0) -> dict:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return {"type": "item.completed", "item": {"id": f"item_{idx}", "type": "agent_message", "text": text}}


def answer(lines, uncertain=()) -> dict:
    return {"lines": [{"text": t, "uncertain": i in uncertain} for i, t in enumerate(lines)]}


def turn(**usage) -> dict:
    return {"type": "turn.completed", "usage": usage}


START = ({"type": "thread.started", "thread_id": "t-1"}, {"type": "turn.started"})


# --------------------------------------------------------------------------- 06-08 argv


def test_codex_argv_effort_and_output(tmp_path, monkeypatch):
    """06-08 (S1): the argv built from the packaged configuration carries the event stream (--json), the output
    schema file of the send directory (--output-schema schema.json) and the locked effort
    (-c model_reasoning_effort=max); no -o / --output-last-message (the answer is read from the event stream, so
    the send directory keeps exactly its three files)."""
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    project = tmp_path / "proj"
    project.mkdir()
    cfg = config_mod.load(project)
    assert cfg["codex.effort"] == AX.EFFORT_LOCK == "max"
    png = (REPO / "tests" / "fixtures" / "fx-labs-1x" / "images" / "p001.png").read_bytes()
    import hashlib
    send = CA.prepare_send_dir(png, hashlib.sha256(png).hexdigest())
    argv = list(CA.codex(send, model=cfg["codex.model"], effort=cfg["codex.effort"], source_env={"PATH": "/bin"}).argv)
    assert "--json" in argv
    assert argv[argv.index("--output-schema") + 1] == "schema.json"
    efforts = [argv[i + 1] for i, t in enumerate(argv[:-1]) if t == "-c" and argv[i + 1].startswith("model_reasoning")]
    assert efforts == ["model_reasoning_effort=max"]
    assert "-o" not in argv and "--output-last-message" not in argv


# --------------------------------------------------------------------------- parser on live recordings


def test_codex_recordings_present():
    """The eight WI-00c3 codex recordings (seven xhigh + one effort-max) are the corpus; a vanished corpus fails."""
    assert len(RECORDINGS) == 8
    assert MAX_REC in RECORDINGS


@pytest.mark.parametrize("rec_path", RECORDINGS, ids=[p.name[:12] + p.name[64:] for p in RECORDINGS])
def test_parse_recorded_streams(rec_path):
    """S2 over every live capture: the parser's lines equal the recorded transcription, tokens equal the recorded
    usage, no error, and model_actual is None (codex 0.163.0 events name no model)."""
    rec = json.loads(rec_path.read_text(encoding="utf-8"))
    parsed = AX.parse_events(rec["stdout"])
    assert parsed.problem is None
    assert [line["text"] for line in parsed.lines] == rec["lines"]
    assert parsed.tokens == rec["tokens"]
    assert parsed.is_error is False
    assert parsed.model_actual is None
    assert parsed.cost_usd == AX.estimate_cost_usd(rec["tokens"])


def test_estimate_of_effort_max_recording():
    """S1, hand-computed: 8827 input x $10/M + (675 output + 516 reasoning) x $60/M = 0.08827 + 0.07146."""
    rec = json.loads(MAX_REC.read_text(encoding="utf-8"))
    assert AX.estimate_cost_usd(rec["tokens"]) == Decimal("0.15973")


# --------------------------------------------------------------------------- parser edges


def test_last_agent_message_is_the_answer():
    """S2: an earlier agent_message (progress text, or an older answer) is ignored; the last one is the answer."""
    out = AX.parse_events(stream(*START, message("Reading the image..."), message(answer(["甲", "乙"]), 1),
                                 turn(input_tokens=10, output_tokens=2)))
    assert [x["text"] for x in out.lines] == ["甲", "乙"]
    assert out.problem is None


def test_uncertain_flags_kept():
    out = AX.parse_events(stream(*START, message(answer(["a", "b", "c"], uncertain=(2,))), turn(input_tokens=1)))
    assert [x["uncertain"] for x in out.lines] == [False, False, True]


@pytest.mark.parametrize("events", [
    pytest.param((*START, turn(input_tokens=5)), id="no-agent-message"),
    pytest.param((*START, message("not json at all"), turn(input_tokens=5)), id="text-not-json"),
    pytest.param((*START, message({"lines": [{"text": 7, "uncertain": False}]}), turn(input_tokens=5)),
                 id="schema-invalid"),
    pytest.param((*START, message({"rows": []}), turn(input_tokens=5)), id="wrong-key"),
    pytest.param((*START, message([1, 2]), turn(input_tokens=5)), id="not-an-object"),
], )
def test_unusable_answer_has_problem(events):
    """S3: no answer, an answer that is not JSON, or one that breaks ocr_cloud_output -> lines None and a problem;
    the usage is still read (the call was paid for)."""
    out = AX.parse_events(stream(*events))
    assert out.lines is None
    assert out.problem
    assert out.tokens == {"input_tokens": 5}


@pytest.mark.parametrize("stdout", ["", "\n\n", "plain text, no events\n", "[1, 2]\n"])
def test_no_event_stream(stdout):
    """S3: empty or non-JSONL stdout -> lines None, no tokens, cost unknown (None)."""
    out = AX.parse_events(stdout)
    assert out.lines is None and out.problem
    assert out.tokens == {}
    assert out.cost_usd is None


def test_non_json_lines_between_events_ignored():
    """S2: a stray non-JSON line in the stream does not hide the answer."""
    out = AX.parse_events("warning: something\n" + stream(*START, message(answer(["x"])), turn(input_tokens=3)))
    assert [x["text"] for x in out.lines] == ["x"]


@pytest.mark.parametrize("bad", [
    {"type": "turn.failed", "error": {"message": "usage limit reached"}},
    {"type": "error", "message": "stream disconnected"},
])
def test_error_events_mark_is_error(bad):
    """S3: a turn.failed or error event marks the call failed even when an answer was emitted before it."""
    out = AX.parse_events(stream(*START, message(answer(["x"])), bad))
    assert out.is_error is True


def test_tokens_summed_over_turns_and_non_ints_ignored():
    """S2: several turn.completed events add up; booleans, floats and strings in usage are not counted."""
    out = AX.parse_events(stream(*START, turn(input_tokens=100, output_tokens=10, flag=True, ratio=0.5),
                                 message(answer(["x"])), turn(input_tokens=50, output_tokens=5, note="n"),
                                 {"type": "turn.completed", "usage": "not a mapping"}))
    assert out.tokens == {"input_tokens": 150, "output_tokens": 15}
    assert out.cost_usd == Decimal("150") * Decimal("10") / 10**6 + Decimal("15") * Decimal("60") / 10**6


def test_model_actual_from_event_when_present():
    """06-09 for codex (S1): should an event name the model (not the case in codex 0.163.0), that name - not the
    configured alias - is model_actual; the last one named wins."""
    out = AX.parse_events(stream({"type": "thread.started", "thread_id": "t", "model": "gpt-6-sol-2026-09-01"},
                                 message(answer(["x"])),
                                 {"type": "turn.completed", "usage": {"input_tokens": 1}, "model": "gpt-6-sol-b"}))
    assert out.model_actual == "gpt-6-sol-b"


def test_to_rows_ocr_line_shape():
    rows = AX.to_rows([{"text": " a ", "uncertain": False}, {"text": "b", "uncertain": True}], page_id="d-x/p001",
                      der_sha256="ab" * 32)
    assert rows == [
        {"engine": "codex", "page_id": "d-x/p001", "der_sha256": "ab" * 32, "line_idx": 0, "text": " a ",
         "bbox_px": None, "tokens": None},
        {"engine": "codex", "page_id": "d-x/p001", "der_sha256": "ab" * 32, "line_idx": 1, "text": "b",
         "bbox_px": None, "tokens": None}]


# --------------------------------------------------------------------------- cost estimate


def test_estimate_examples():
    """S1/S2: 1000 input + 500 output = 0.01 + 0.03; cached input is part of input (not charged twice); no priced
    kind -> None (unknown, never zero); zero tokens -> 0."""
    assert AX.estimate_cost_usd({"input_tokens": 1000, "output_tokens": 500}) == Decimal("0.04")
    assert AX.estimate_cost_usd({"input_tokens": 1000, "cached_input_tokens": 1000}) == Decimal("0.01")
    assert AX.estimate_cost_usd({}) is None
    assert AX.estimate_cost_usd({"cached_input_tokens": 9}) is None
    assert AX.estimate_cost_usd({"input_tokens": 0}) == Decimal("0")


KINDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens",
         "reasoning_output_tokens")
USAGE = st.dictionaries(st.sampled_from(KINDS), st.integers(min_value=0, max_value=5_000_000), min_size=1)


def _add(a: dict, b: dict) -> dict:
    return {k: a.get(k, 0) + b.get(k, 0) for k in {*a, *b}}


@settings(max_examples=300, deadline=None)
@given(a=USAGE, b=USAGE)
def test_estimate_additive(a, b):
    """S4 law: estimate(a + b) == estimate(a) + estimate(b) whenever both are priced - charging per turn or per
    summed usage gives the same money."""
    ea, eb = AX.estimate_cost_usd(a), AX.estimate_cost_usd(b)
    if ea is not None and eb is not None:
        assert AX.estimate_cost_usd(_add(a, b)) == ea + eb


@settings(max_examples=300, deadline=None)
@given(a=USAGE, kind=st.sampled_from(KINDS), more=st.integers(min_value=1, max_value=1_000_000))
def test_estimate_monotone_nonnegative(a, kind, more):
    """S4 law: more tokens of any kind never lower the estimate, and it is never negative."""
    before = AX.estimate_cost_usd(a)
    after = AX.estimate_cost_usd({**a, kind: a.get(kind, 0) + more})
    if before is not None:
        assert before >= 0
        assert after is not None and after >= before
