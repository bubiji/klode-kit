"""WI-14 matrix 14-03 and 14-05: ``workflows/kb-pipeline.js`` run by real node with a stubbed ``agent()``.

The harness below gives the script what the Claude Code Workflow runtime gives it (``agent``, ``args``, ``log``,
``phase``; ``Date.now()``, ``Math.random()`` and a no-argument ``new Date()`` throw - docs
https://code.claude.com/docs/en/workflows , "Behavior and limits") and nothing else, so the script cannot reach a
file or a process, and the test sees the exact sequence of ``agent()`` calls and what the script returns. A missing
node FAILS (never skips).

Law (S4/S5): the sequence of calls is a deterministic function of ``args`` - the same args give byte-identical calls,
and for every combination of resume / publish / failing stage the calls are a prefix of the full stage list that
ends at the first failure (audit exit 10 in stage 1 is a stop that is not a failure). Real host runs of the same
script are 14-04 (tests/host/test_workflow_two_stage.py).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.flows import REPO

pytestmark = [pytest.mark.cli_integration, pytest.mark.needs_node]

SCRIPT = REPO / "workflows" / "kb-pipeline.js"
FIRST = ["preflight", "ocr", "audit"]
RESUME = ["writeback", "build", "check"]
SUMMARY = json.dumps({"schema": "klode-kit.pipeline/1", "batch_id": "b-0001", "stages": [], "exit_code": 10})

HARNESS = r"""
const fs = require('fs')
const [, , scriptPath, argsJson, scenarioJson, outPath] = process.argv
const src = fs.readFileSync(scriptPath, 'utf8').replace(/^export const meta =/m, 'const meta =')
const scenario = JSON.parse(scenarioJson)
const calls = []
const RealDate = Date
class NoClock extends RealDate {
  constructor(...a) { if (a.length === 0) throw new Error('new Date() is not allowed'); super(...a) }
  static now() { throw new Error('Date.now is not allowed') }
}
globalThis.Date = NoClock
Math.random = () => { throw new Error('Math.random is not allowed') }
async function agent(prompt, opts) {
  calls.push({ prompt, opts })
  const code = Object.prototype.hasOwnProperty.call(scenario.codes, opts.label) ? scenario.codes[opts.label] : 0
  if (code === null) return null
  return { stage: opts.label, exit_code: code, stdout: scenario.stdout[opts.label] || '' }
}
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor
const args = argsJson === 'undefined' ? undefined : JSON.parse(argsJson)
const fn = new AsyncFunction('agent', 'args', 'log', 'phase', src)
fn(agent, args, () => {}, () => {}).then(
  (result) => fs.writeFileSync(outPath, JSON.stringify({ ok: true, result, calls })),
  (error) => fs.writeFileSync(outPath, JSON.stringify({ ok: false, error: String(error), calls })),
)
"""


def node() -> str:
    exe = shutil.which("node")
    assert exe is not None, "node is required by the cli_integration lane (needs_node): install node"
    return exe


@pytest.fixture(scope="module")
def harness(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("wf-harness") / "harness.cjs"
    path.write_text(HARNESS, encoding="utf-8")
    return path


class Runner:
    def __init__(self, harness: Path, tmp: Path):
        self.harness, self.tmp, self.n = harness, tmp, 0

    def __call__(self, args, codes=None, stdout=None) -> dict:
        self.n += 1
        out = self.tmp / f"out{self.n}.json"
        scenario = {"codes": codes if codes is not None else {"audit": 10}, "stdout": stdout or {"audit": SUMMARY}}
        argv = [node(), str(self.harness), str(SCRIPT), "undefined" if args is None else json.dumps(args),
                json.dumps(scenario), str(out)]
        proc = subprocess.run(argv, capture_output=True, timeout=60)
        assert proc.returncode == 0, proc.stderr.decode()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["ok"], data
        return data


@pytest.fixture
def run(harness, tmp_path) -> Runner:
    return Runner(harness, tmp_path)


BASE = {"project": "/data/proj", "inbox": "/data/inbox"}
RESUMED = {**BASE, "resume": True, "choices": "/data/choices.json"}


def labels(data) -> list[str]:
    return [c["opts"]["label"] for c in data["calls"]]


def test_stage1_ends_at_audit(run):
    """14-03 (L5 S2): preflight, ocr, audit - then the run ends, awaiting the human; nothing after audit is called."""
    data = run(BASE)
    assert labels(data) == FIRST
    result = data["result"]
    assert result["stage"] == 1
    assert result["exit_code"] == 10
    assert result["awaiting_confirm"] is True
    assert result["batch_id"] == "b-0001"
    assert [s["stage"] for s in result["stages"]] == FIRST
    assert "confirm/<alias>/confirm.html" in result["next"] and "b-0001" in result["next"]
    assert result["published"] is False


def test_every_stage_is_one_agent_with_the_output_schema_and_one_launcher_command(run):
    data = run(BASE)
    for call in data["calls"]:
        schema = call["opts"]["schema"]
        assert schema["required"] == ["stage", "exit_code", "stdout"]
        assert schema["properties"]["exit_code"] == {"type": "integer"}
        stage = call["opts"]["label"]
        command = ("${CLAUDE_PLUGIN_ROOT}/bin/klode-kit pipeline --batch '/data/inbox' --project '/data/proj' "
                   f"--stage {stage}")
        assert f"\n\n{command}\n\n" in call["prompt"]
        assert call["prompt"].count("${CLAUDE_PLUGIN_ROOT}/bin/klode-kit pipeline") == 1
        assert "--force" not in call["prompt"]


def test_routes_and_type_are_passed_to_the_pipeline(run):
    data = run({**BASE, "routes": "vision,claude", "type": "lab"})
    for call in data["calls"]:
        assert "--stage " + call["opts"]["label"] + " --routes vision,claude --type lab\n" in call["prompt"]


@pytest.mark.parametrize("failing,code", [("preflight", 40), ("ocr", 30), ("ocr", 61)])
def test_stage1_stops_at_the_first_failure(run, failing, code):
    data = run(BASE, codes={failing: code})
    assert labels(data) == FIRST[: FIRST.index(failing) + 1]
    assert data["result"]["exit_code"] == code
    assert data["result"]["awaiting_confirm"] is False
    assert data["result"]["next"] is None


def test_exit_10_before_audit_is_a_failure_not_an_await(run):
    data = run(BASE, codes={"ocr": 10})
    assert labels(data) == ["preflight", "ocr"]
    assert data["result"]["awaiting_confirm"] is False


def test_audit_without_disagreements_is_done_not_awaiting(run):
    data = run(BASE, codes={"audit": 0})
    assert labels(data) == FIRST
    assert data["result"]["exit_code"] == 0 and data["result"]["awaiting_confirm"] is False


def test_audit_failure_is_not_awaiting(run):
    data = run(BASE, codes={"audit": 20})
    assert data["result"]["exit_code"] == 20 and data["result"]["awaiting_confirm"] is False


def test_a_stopped_agent_ends_the_run_without_further_calls(run):
    data = run(BASE, codes={"ocr": None})
    assert labels(data) == ["preflight", "ocr"]
    assert data["result"]["exit_code"] is None and data["result"]["awaiting_confirm"] is False


def test_resume_runs_writeback_build_check_and_stops_before_publish(run):
    """14-05 (L5 S2) with the manual gate: without args.publish the sequence ends at check and says so."""
    data = run(RESUMED, codes={})
    assert labels(data) == RESUME
    result = data["result"]
    assert result["stage"] == 2 and result["exit_code"] == 0
    assert result["awaiting_confirm"] is False and result["published"] is False
    assert "args.publish: true" in result["next"]
    for call in data["calls"]:
        assert "--choices '/data/choices.json'" in call["prompt"]


def test_resume_with_publish_true_appends_publish(run):
    """14-05 (L5 S2): writeback, build, check, publish - the user's explicit consent is args.publish === true."""
    data = run({**RESUMED, "publish": True}, codes={})
    assert labels(data) == RESUME + ["publish"]
    assert data["result"]["published"] is True and data["result"]["next"] is None
    assert "--stage publish" in data["calls"][-1]["prompt"]


@pytest.mark.parametrize("value", ["true", 1, "yes", None, False, [True]])
def test_only_the_boolean_true_publishes(run, value):
    data = run({**RESUMED, "publish": value}, codes={})
    assert labels(data) == RESUME and data["result"]["published"] is False


def test_publish_without_resume_is_ignored(run):
    data = run({**BASE, "publish": True})
    assert labels(data) == FIRST


@pytest.mark.parametrize("failing", RESUME)
def test_a_failed_stage_two_stops_before_publish(run, failing):
    data = run({**RESUMED, "publish": True}, codes={failing: 20})
    assert labels(data) == RESUME[: RESUME.index(failing) + 1]
    assert data["result"]["published"] is False and data["result"]["exit_code"] == 20


def test_resume_without_choices_is_refused_with_zero_calls(run):
    data = run({**BASE, "resume": True})
    assert data["calls"] == []
    assert "args.choices" in data["result"]["error"]


def test_resume_without_choices_is_allowed_when_declared(run):
    data = run({**BASE, "resume": True, "no_choices": True}, codes={})
    assert labels(data) == RESUME
    assert all("--choices" not in c["prompt"] for c in data["calls"])


@pytest.mark.parametrize("args", [
    None, {}, {"project": "/p"}, {"inbox": "/i"}, {"project": "", "inbox": "/i"}, {"project": 3, "inbox": "/i"},
    {"project": "/p'; rm x; '", "inbox": "/i"}, {"project": "/p", "inbox": "/i\nrm"},
    {**BASE, "routes": "a;b"}, {**BASE, "type": "xray"}, {**RESUMED, "choices": "/c'.json"},
])
def test_bad_arguments_are_refused_with_zero_calls(run, args):
    data = run(args)
    assert data["calls"] == []
    assert isinstance(data["result"]["error"], str) and data["result"]["exit_code"] is None


def test_same_args_give_byte_identical_calls(run):
    """S5 determinism: a relaunched run repeats the same agent() calls (the reason the runtime bans clock and random)."""
    first, second = run(RESUMED, codes={}), run(RESUMED, codes={})
    assert json.dumps(first["calls"], sort_keys=True) == json.dumps(second["calls"], sort_keys=True)
    assert json.dumps(first["result"], sort_keys=True) == json.dumps(second["result"], sort_keys=True)


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(resume=st.booleans(), publish=st.one_of(st.booleans(), st.just("true")),
       failing=st.one_of(st.none(), st.sampled_from(FIRST + RESUME + ["publish"])))
def test_calls_are_a_prefix_of_the_stage_list_ending_at_the_first_failure(run, resume, publish, failing):
    args = dict(RESUMED if resume else BASE)
    args["publish"] = publish
    full = (RESUME + (["publish"] if publish is True else [])) if resume else FIRST
    codes = {} if resume else {"audit": 10}
    if failing is not None:
        codes[failing] = 40
    data = run(args, codes=codes)
    expected = full[: full.index(failing) + 1] if failing in full else full
    assert labels(data) == expected
    assert data["result"]["awaiting_confirm"] == (not resume and failing not in FIRST)
