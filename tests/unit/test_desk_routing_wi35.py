"""WI-35 (unit): desk acceptance by model routing - ``desk-route-tasks``, ``desk-build --verify --routing`` and
``build --desk-routing`` (decision D-46; a real finding of the jtbd migration: keyword ``diagnose`` hits 1/12 of a
frozen holdout of new phrasings while a model routing the same questions zero-shot hits 12/12, and iron rule 2 forbids
tuning cues on the holdout, so an honest library can never pass the keyword desk gate).

Specification:

- ``klode-kit desk-route-tasks --build BUILD --out DIR [--project P]`` writes ``DIR/routing_prompt.txt`` for one worker
  and prints one JSON document ``klode-kit.desk_route_tasks/1`` (``build``, ``prompt_file``, ``output`` = where the
  worker must write the answer, ``prompt_sha256``, ``dimensions``, ``questions``). The prompt names every dimension
  (id, title, core question) and the holdout QUESTIONS ONLY, each under a ``qid`` (``q01``, ``q02``... in the order of
  ``src/holdout.jsonl``), and states the answer format ``{schema: "klode-kit.desk_routing/1", prompt_sha256, answers:
  [{qid, dimension}]}`` (dimension null = none fits). Exit 0; 2 usage (no build / build outside the project / ``--out``
  inside the build); 30 contract (no desk, no holdout, bad holdout).
- Separation law: the prompt is a function of the dimensions and the holdout *questions*; the expected dimensions
  (``gold``) never reach it. Relabelling the gold of every question leaves the prompt byte-identical, and the line of a
  question never names a dimension.
- ``desk-build KB --verify --routing FILE``: the file must be valid (schema, ``prompt_sha256`` equal to the sha of the
  prompt regenerated from this KB's dimensions and holdout, every holdout qid exactly once, no other qid, each
  ``dimension`` an existing dimension id or null) else exit 30 and nothing is written. Then the gate is the hit rate of
  the routing over the frozen holdout, computed here from the gold labels: at least ``desk.holdout_min`` questions and
  ``hit/total >= desk.holdout_pass``. Keyword ``diagnose`` is still run and reported but does not gate. The receipt
  gets a ``routing`` block (sha256 of the file, ``prompt_sha256``, hit / total / pass / results). Without ``--routing``
  everything is as before (no ``routing`` block, keyword gate).
- ``build --desk-routing FILE`` hands the file to ``desk_verify`` (``desk-build --verify --routing``); a missing file is
  a usage error (2). The first build without it that fails ``desk_verify`` names the next command
  (``desk-route-tasks``) in its detail.

Laws: (S6) the hit rate is invariant under the order of the answers in the file; (S4) turning one correct answer into
a wrong one lowers the hits by exactly one; (S6) the prompt does not depend on the gold labels.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from _support.desk_fx import FakeKlode, make_kb, spec_with
from klode_kit.__main__ import COMMANDS
from klode_kit.build import desk_build as D
from klode_kit.build import desk_route as DR
from klode_kit.build import run_build as RB
from klode_kit.build.run_build import Reason

pytestmark = pytest.mark.unit

BUILD_ID = "b-20261023T000000Z-0123abcd"
QLINE = re.compile(r"^(q\d{2,}): (.+)$", re.M)


@pytest.fixture
def klode(tmp_path, monkeypatch) -> FakeKlode:
    fk = FakeKlode(tmp_path / "fakebin")
    fk.install(monkeypatch)
    return fk


def holdout_for(spec: dict, n: int = 5) -> list[dict]:
    dims = [d["dimension"] for d in spec["dimensions"]]
    return [{"q": f"holdout question {k} about nothing?", "gold": dims[k % len(dims)]} for k in range(n)]


def world(root: Path, spec: dict | None = None, holdout: list[dict] | None = None) -> tuple[Path, Path, list[dict]]:
    """``(project, build, holdout)``: a project with an applied library at ``kb/demo/builds/<id>``."""
    spec = spec or spec_with(5)
    holdout = holdout if holdout is not None else holdout_for(spec)
    project = root / "proj"
    build = project / "kb" / "demo" / "builds" / BUILD_ID
    make_kb(build, spec, holdout)
    assert D.main([str(build), "--apply"]) == 0
    return project, build, holdout


def keyword_all_miss(klode: FakeKlode, spec: dict, holdout: list[dict]) -> None:
    """Keyword diagnose answers every tuning question right and every holdout question with nothing."""
    answers: dict = {q: d["dimension"] for d in spec["dimensions"] for q in d["tests"]}
    answers.update({h["q"]: None for h in holdout})
    klode.route(answers)


def tasks(capsys, project: Path, build: Path, out: Path) -> dict:
    capsys.readouterr()
    assert DR.main(["--build", str(build), "--out", str(out), "--project", str(project)]) == 0
    return json.loads(capsys.readouterr().out)


def asked(doc: dict) -> list[tuple[str, str]]:
    return QLINE.findall(Path(doc["prompt_file"]).read_text(encoding="utf-8"))


def answer_file(doc: dict, answers: dict[str, str | None], *, path: Path | None = None, **over) -> Path:
    body = {"schema": "klode-kit.desk_routing/1", "prompt_sha256": doc["prompt_sha256"],
            "answers": [{"qid": q, "dimension": d} for q, d in answers.items()]}
    body.update(over)
    target = path or Path(doc["output"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return target


def right_answers(doc: dict, holdout: list[dict]) -> dict[str, str | None]:
    gold = {h["q"]: h["gold"] for h in holdout}
    return {qid: gold[text] for qid, text in asked(doc)}


def receipt(kb: Path) -> dict:
    return json.loads((kb / "desk_receipt.json").read_text(encoding="utf-8"))


def verify(build: Path, routing: Path | None) -> int:
    argv = [str(build), "--verify"] + (["--routing", str(routing)] if routing is not None else [])
    return D.main(argv)


# --------------------------------------------------------------------------- the command and the prompt


def test_command_is_registered():
    assert "desk-route-tasks" in COMMANDS
    assert COMMANDS["desk-route-tasks"][0] == "klode_kit.build.desk_route:main"


def test_prompt_lists_dimensions_and_the_holdout_questions_only(tmp_path, capsys):
    project, build, holdout = world(tmp_path)
    spec = D.load(build)
    out = tmp_path / "work" / "route"
    doc = tasks(capsys, project, build, out)
    assert doc["schema"] == "klode-kit.desk_route_tasks/1"
    assert doc["build"] == f"kb/demo/builds/{BUILD_ID}"
    assert doc["prompt_file"] == str(out / "routing_prompt.txt")
    assert doc["output"] == str(out / "routing.json")
    assert re.fullmatch(r"[0-9a-f]{64}", doc["prompt_sha256"])
    assert doc["dimensions"] == [d["dimension"] for d in spec["dimensions"]] and doc["questions"] == 5
    prompt = Path(doc["prompt_file"]).read_text(encoding="utf-8")
    for dim in spec["dimensions"]:
        assert dim["dimension"] in prompt and dim["title"] in prompt and dim["question"] in prompt
    assert [text for _, text in asked(doc)] == [h["q"] for h in holdout]
    assert [qid for qid, _ in asked(doc)] == ["q01", "q02", "q03", "q04", "q05"]
    assert "klode-kit.desk_routing/1" in prompt and doc["prompt_sha256"] in prompt and doc["output"] in prompt
    assert "null" in prompt


def test_prompt_does_not_contain_tuning_questions_or_cues(tmp_path, capsys):
    project, build, _ = world(tmp_path)
    prompt = Path(tasks(capsys, project, build, tmp_path / "r")["prompt_file"]).read_text(encoding="utf-8")
    for dim in D.load(build)["dimensions"]:
        for t in dim["tests"] + dim["cues"]:
            assert t not in prompt


def test_running_again_prints_and_writes_the_same(tmp_path, capsys):
    project, build, _ = world(tmp_path)
    out = tmp_path / "r"
    first = tasks(capsys, project, build, out)
    before = (out / "routing_prompt.txt").read_bytes()
    mtime = (out / "routing_prompt.txt").stat().st_mtime_ns
    assert tasks(capsys, project, build, out) == first
    assert (out / "routing_prompt.txt").read_bytes() == before
    assert (out / "routing_prompt.txt").stat().st_mtime_ns == mtime


def test_the_build_is_only_read(tmp_path, capsys):
    project, build, _ = world(tmp_path)
    snap = {p: p.read_bytes() for p in build.rglob("*") if p.is_file()}
    tasks(capsys, project, build, tmp_path / "r")
    assert {p: p.read_bytes() for p in build.rglob("*") if p.is_file()} == snap


def test_usage_and_contract_refusals(tmp_path, capsys):
    project, build, _ = world(tmp_path)
    out = tmp_path / "r"
    assert DR.main(["--build", str(build / "nope"), "--out", str(out), "--project", str(project)]) == 2
    assert DR.main(["--build", str(build), "--out", str(build / "x"), "--project", str(project)]) == 2
    assert DR.main(["--build", str(build), "--out", str(out), "--project", str(tmp_path / "elsewhere")]) == 2
    other = tmp_path / "other"
    other.mkdir()
    assert DR.main(["--build", str(build), "--out", str(out), "--project", str(other)]) == 2
    assert not out.exists()
    (build / "src" / "holdout.jsonl").unlink()
    assert DR.main(["--build", str(build), "--out", str(out), "--project", str(project)]) == 30
    assert "holdout" in capsys.readouterr().err
    (build / "src" / "desk.json").unlink()
    assert DR.main(["--build", str(build), "--out", str(out), "--project", str(project)]) == 30


def test_a_holdout_question_that_is_also_a_tuning_question_is_a_contract_error(tmp_path, capsys):
    spec = spec_with(5)
    bad = holdout_for(spec) + [{"q": spec["dimensions"][0]["tests"][0], "gold": spec["dimensions"][0]["dimension"]}]
    project, build, _ = world(tmp_path, spec, bad)
    assert DR.main(["--build", str(build), "--out", str(tmp_path / "r"), "--project", str(project)]) == 30


# --------------------------------------------------------------------------- separation law


WORD = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=3, max_size=24).map(lambda s: " ".join(s.split())).filter(
    bool)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(n_dims=st.integers(2, 5), golds=st.lists(st.integers(0, 4), min_size=5, max_size=9, unique=False),
       shift=st.integers(1, 4), words=st.lists(WORD, min_size=9, max_size=9, unique=True))
def test_the_prompt_never_depends_on_the_expected_dimensions(n_dims, golds, shift, words):
    names = ["dim0x1", "dim1x2", "dim2x3", "dim3x4", "dim4x5"][:n_dims]
    spec = spec_with(n_dims, dim_names=names)
    questions = [f"{w}?" for w in words[:len(golds)]]
    prompts = []
    with tempfile.TemporaryDirectory() as tmp:
        for relabel in (0, shift):
            root = Path(tmp) / f"w{relabel}"
            hold = [{"q": q, "gold": names[(g + relabel) % n_dims]} for q, g in zip(questions, golds)]
            project, build, _ = world(root, spec, hold)
            out = Path(tmp) / "same-out"
            buf = io.StringIO()
            import contextlib

            with contextlib.redirect_stdout(buf):
                assert DR.main(["--build", str(build), "--out", str(out), "--project", str(project)]) == 0
            prompts.append((out / "routing_prompt.txt").read_bytes())
            text = prompts[-1].decode("utf-8")
            assert "gold" not in text
            for qid, line in QLINE.findall(text):
                assert not any(nm in line for nm in names)  # the question line never names a dimension
    assert prompts[0] == prompts[1]


def test_prompt_changes_with_the_questions_and_the_dimensions(tmp_path, capsys):
    project, build, _ = world(tmp_path / "a")
    base = tasks(capsys, project, build, tmp_path / "ra")["prompt_sha256"]
    spec = spec_with(5)
    h2 = holdout_for(spec)
    h2[0]["q"] = "a completely different first question?"
    project2, build2, _ = world(tmp_path / "b", spec, h2)
    assert tasks(capsys, project2, build2, tmp_path / "rb")["prompt_sha256"] != base
    project3, build3, _ = world(tmp_path / "c", spec_with(6, dim_names=["construction", "whistle", "night-market-care",
                                                                        "safety", "storage-plan", "extra"]),
                                holdout_for(spec))
    assert tasks(capsys, project3, build3, tmp_path / "rc")["prompt_sha256"] != base


# --------------------------------------------------------------------------- verify with a routing file


def test_routing_passes_where_keyword_routing_fails(tmp_path, klode, capsys):
    project, build, holdout = world(tmp_path)
    keyword_all_miss(klode, D.load(build), holdout)
    # without a routing file: the old keyword gate fails this library
    capsys.readouterr()
    assert verify(build, None) == 1
    assert "routing" not in receipt(build)
    doc = tasks(capsys, project, build, tmp_path / "r")
    f = answer_file(doc, right_answers(doc, holdout))
    capsys.readouterr()
    assert verify(build, f) == 0
    out = capsys.readouterr().out
    assert "书桌验收：通过" in out
    kw = [ln for ln in out.splitlines() if ln.startswith("diagnose 留出集：")]
    assert len(kw) == 1 and "0/5" in kw[0]  # keyword result is still computed and reported
    rt = [ln for ln in out.splitlines() if ln.startswith("desk-routing 留出集：")]
    assert len(rt) == 1 and "5/5" in rt[0]
    r = receipt(build)
    assert r["status"] == "pass"
    assert r["routing"]["hit"] == 5 and r["routing"]["total"] == 5 and r["routing"]["pass"] is True
    assert r["routing"]["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
    assert r["routing"]["prompt_sha256"] == doc["prompt_sha256"]
    assert all(x["ok"] and x["got"] == x["expect"] for x in r["routing"]["results"])
    assert r["holdout"]["hit"] == 0 and r["holdout"]["pass"] is False  # keyword numbers kept, not gating
    assert list(Draft202012Validator(D.JSON_SCHEMA).iter_errors(r)) == []


def test_routing_misses_fail_even_when_keyword_routing_is_perfect(tmp_path, klode, capsys):
    project, build, holdout = world(tmp_path)
    spec = D.load(build)
    answers = {q: d["dimension"] for d in spec["dimensions"] for q in d["tests"]}
    answers.update({h["q"]: h["gold"] for h in holdout})
    klode.route(answers)
    doc = tasks(capsys, project, build, tmp_path / "r")
    good = right_answers(doc, holdout)
    bad = dict(good)
    for qid in list(bad)[:2]:
        bad[qid] = None
    assert verify(build, answer_file(doc, bad)) == 1
    r = receipt(build)
    assert r["status"] == "fail" and r["routing"]["hit"] == 3 and r["routing"]["pass"] is False
    assert r["holdout"]["pass"] is True


@pytest.mark.parametrize(("n", "wrong", "rc"), [(5, 1, 0), (5, 2, 1), (4, 0, 1), (6, 1, 0), (6, 2, 1)])
def test_routing_gate_boundary_uses_the_configured_thresholds(tmp_path, klode, capsys, n, wrong, rc):
    spec = spec_with(5)
    hold = holdout_for(spec, n)
    project, build, _ = world(tmp_path, spec, hold)
    keyword_all_miss(klode, spec, hold)
    doc = tasks(capsys, project, build, tmp_path / "r")
    ans = right_answers(doc, hold)
    for qid in list(ans)[:wrong]:
        ans[qid] = "safety" if ans[qid] != "safety" else "whistle"
    assert verify(build, answer_file(doc, ans)) == rc


def test_a_null_answer_is_a_miss_and_a_wrong_dimension_is_a_miss(tmp_path, klode, capsys):
    project, build, holdout = world(tmp_path)
    keyword_all_miss(klode, D.load(build), holdout)
    doc = tasks(capsys, project, build, tmp_path / "r")
    ans = right_answers(doc, holdout)
    q1, q2 = list(ans)[:2]
    ans[q1] = None
    ans[q2] = "whistle" if ans[q2] != "whistle" else "safety"
    assert verify(build, answer_file(doc, ans)) == 1
    res = receipt(build)["routing"]["results"]
    assert [x["ok"] for x in res] == [False, False, True, True, True]
    assert res[0]["got"] is None


def mutate_drop(doc):
    doc["answers"].pop()


def mutate_dup(doc):
    doc["answers"][1]["qid"] = doc["answers"][0]["qid"]


def mutate_extra(doc):
    doc["answers"].append({"qid": "q99", "dimension": None})


def mutate_unknown_dim(doc):
    doc["answers"][0]["dimension"] = "no-such-dimension"


def mutate_sha(doc):
    doc["prompt_sha256"] = "0" * 64


def mutate_schema(doc):
    doc["schema"] = "klode-kit.desk_routing/2"


def mutate_dim_type(doc):
    doc["answers"][0]["dimension"] = 3


def mutate_answers_type(doc):
    doc["answers"] = {"q01": "whistle"}


def mutate_missing_key(doc):
    del doc["answers"][0]["qid"]


def mutate_extra_key(doc):
    doc["answers"][0]["gold"] = "whistle"


@pytest.mark.parametrize("mutate", [mutate_drop, mutate_dup, mutate_extra, mutate_unknown_dim, mutate_sha,
                                    mutate_schema, mutate_dim_type, mutate_answers_type, mutate_missing_key,
                                    mutate_extra_key], ids=lambda f: f.__name__)
def test_an_invalid_routing_file_is_a_contract_error_and_writes_nothing(tmp_path, klode, capsys, mutate):
    project, build, holdout = world(tmp_path)
    keyword_all_miss(klode, D.load(build), holdout)
    doc = tasks(capsys, project, build, tmp_path / "r")
    f = answer_file(doc, right_answers(doc, holdout))
    body = json.loads(f.read_text(encoding="utf-8"))
    mutate(body)
    f.write_text(json.dumps(body), encoding="utf-8")
    capsys.readouterr()
    assert verify(build, f) == 30
    assert "routing" in capsys.readouterr().err
    assert not (build / "desk_receipt.json").exists()


def test_a_routing_file_that_is_not_json_or_missing_is_a_contract_error(tmp_path, klode, capsys):
    project, build, holdout = world(tmp_path)
    keyword_all_miss(klode, D.load(build), holdout)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert verify(build, bad) == 30
    assert verify(build, tmp_path / "missing.json") == 30
    assert not (build / "desk_receipt.json").exists()


def test_a_routing_made_for_other_dimensions_is_refused(tmp_path, klode, capsys):
    """The prompt sha binds the routing to the dimensions (and questions) it was generated for."""
    project, build, holdout = world(tmp_path / "a")
    doc = tasks(capsys, project, build, tmp_path / "r")
    f = answer_file(doc, right_answers(doc, holdout))
    spec6 = spec_with(6, dim_names=["construction", "whistle", "night-market-care", "safety", "storage-plan", "extra"])
    _, build6, _ = world(tmp_path / "b", spec6, holdout)
    keyword_all_miss(klode, spec6, holdout)
    capsys.readouterr()
    assert verify(build6, f) == 30
    assert "prompt" in capsys.readouterr().err
    assert not (build6 / "desk_receipt.json").exists()


def test_routing_flag_only_with_verify(tmp_path, capsys):
    _, build, _ = world(tmp_path)
    assert D.main([str(build), "--status", "--routing", str(tmp_path / "x.json")]) == 2


def test_without_routing_the_receipt_is_the_old_one(tmp_path, klode):
    _, build, holdout = world(tmp_path)
    spec = D.load(build)
    answers = {q: d["dimension"] for d in spec["dimensions"] for q in d["tests"]}
    answers.update({h["q"]: h["gold"] for h in holdout})
    klode.route(answers)
    assert verify(build, None) == 0
    assert "routing" not in receipt(build)


# --------------------------------------------------------------------------- laws on the hit rate


def plan_quiet(tmp: str, project: Path, build: Path) -> dict:
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert DR.main(["--build", str(build), "--out", str(Path(tmp) / "r"), "--project", str(project)]) == 0
    return json.loads(buf.getvalue())


@settings(max_examples=30, deadline=None)
@given(wrong=st.lists(st.booleans(), min_size=5, max_size=12), seed=st.integers(0, 10_000))
def test_hit_rate_counts_matches_and_ignores_the_order_of_the_file(wrong, seed):
    n = len(wrong)
    spec = spec_with(5)
    hold = holdout_for(spec, n)
    with tempfile.TemporaryDirectory() as tmp:
        project, build, _ = world(Path(tmp), spec, hold)
        doc = plan_quiet(tmp, project, build)
        ans = right_answers(doc, hold)
        for qid, w in zip(list(ans), wrong):
            if w:
                ans[qid] = None
        loaded = D.load(build)
        items, _raw = D.load_holdout(build / "src" / "holdout.jsonl", loaded)
        order = sorted(ans, key=lambda k: hashlib.sha256(f"{k}{seed}".encode()).hexdigest())
        a = DR.score(answer_file(doc, ans, path=Path(tmp) / "a.json"), loaded, items)
        b = DR.score(answer_file(doc, {k: ans[k] for k in order}, path=Path(tmp) / "b.json"), loaded, items)
        assert sum(r["ok"] for r in a) == n - sum(wrong)
        assert a == b


@settings(max_examples=20, deadline=None)
@given(which=st.integers(0, 5))
def test_one_more_wrong_answer_is_exactly_one_less_hit(which):
    spec = spec_with(5)
    hold = holdout_for(spec, 6)
    with tempfile.TemporaryDirectory() as tmp:
        project, build, _ = world(Path(tmp), spec, hold)
        doc = plan_quiet(tmp, project, build)
        loaded = D.load(build)
        items, _ = D.load_holdout(build / "src" / "holdout.jsonl", loaded)
        good = right_answers(doc, hold)
        worse = dict(good)
        worse[f"q{which + 1:02d}"] = None

        def hits(ans):
            return sum(r["ok"] for r in DR.score(answer_file(doc, ans, path=Path(tmp) / "x.json"), loaded, items))

        assert hits(good) == 6 and hits(worse) == 5


# --------------------------------------------------------------------------- the build step


def fake_build(tmp_path, verify_rc=0, routing=None):
    b = RB.Build.__new__(RB.Build)
    b.project = tmp_path / "proj"
    b.dir = b.project / "kb" / "demo" / "builds" / BUILD_ID
    b.routing_file = routing
    seen: list[tuple] = []
    b.kit = lambda *a: (seen.append(("kit", a)), (verify_rc, "", ""))[1]
    b.klode_run = lambda *a: (seen.append(("klode", a)),
                              (0, "OK: 0 errors, 0 warning(s)\n", ""))[1]
    b.seen = seen
    return b


def test_desk_verify_passes_the_routing_file_to_desk_build(tmp_path):
    f = tmp_path / "routing.json"
    b = fake_build(tmp_path, routing=f)
    assert b.step_desk_verify().reason is Reason.ok
    (kit,) = [a for k, a in b.seen if k == "kit"]
    assert kit[:3] == ("desk-build", str(b.dir), "--verify") and kit[kit.index("--routing") + 1] == str(f)


def test_desk_verify_without_routing_passes_no_routing_flag(tmp_path):
    b = fake_build(tmp_path)
    assert b.step_desk_verify().reason is Reason.ok
    (kit,) = [a for k, a in b.seen if k == "kit"]
    assert "--routing" not in kit


def test_a_failed_desk_gate_without_routing_names_the_next_command(tmp_path):
    b = fake_build(tmp_path, verify_rc=1)
    res = b.step_desk_verify()
    assert res.reason is Reason.content_gate
    assert "desk-route-tasks" in res.detail and f"kb/demo/builds/{BUILD_ID}" in res.detail
    assert "--desk-routing" in res.detail


def test_a_failed_desk_gate_with_routing_does_not_send_the_user_round_again(tmp_path):
    b = fake_build(tmp_path, verify_rc=1, routing=tmp_path / "r.json")
    res = b.step_desk_verify()
    assert res.reason is Reason.content_gate and "desk-route-tasks" not in res.detail


def run_main(project: Path, *argv: str) -> tuple[int, str]:
    import contextlib

    err, out = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        code = RB.main(["--project", str(project), *argv])
    return code, out.getvalue() + err.getvalue()


def test_desk_routing_must_be_a_file(tmp_path):
    project = tmp_path / "proj"
    project.mkdir()
    code, text = run_main(project, "--desk-routing", str(tmp_path / "nope.json"))
    assert code == 2 and "--desk-routing" in text
    assert not (project / "kb").exists()
