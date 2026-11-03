"""WI-40: a record's date is never a date of birth; ``kb-audit --redate`` re-dates the records that got one (P36) - unit
lane (spec ``.claude/tdd-guardian/wi-40-spec.md``). All names, dates and lines are invented.

Part 1 is ``record_meta.page_date``: ``生年月日`` / ``DOB`` ... lines (and the line after a bare birth label) never
contribute a date; ``採取日`` / ``検査日`` ... label a line like ``日期``. Part 2 is the command on the WI-38/39 base
project (``_support/reopen_fx``): ``AUTO_REC`` is verified, ``PENDING_REC`` / ``OTHER_REC`` wait for a human; all three
are dated 2026-10-22 by the page. A fixture "gets a birth date" by rewriting the record file's ``date`` (and, for the
export tests, the revision body's) in the tmp copy.

Law (S4/S5): a birth date is never the page date, whatever the line layout (generated); ``--redate --apply`` is
idempotent (a second run changes nothing) and append-only (``_rev/`` bodies are byte-identical; only one log line and
the record file's ``date`` change).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from _support import reopen_fx as RF
from _support import writeback_fx as W
from _support.kb_ocr import tree, tree_sha
from _support.reopen_fx import AUTO_REC, PENDING_REC, base, template  # noqa: F401
from klode_kit.audit import record_meta as M

pytestmark = pytest.mark.unit

OTHER_REC = "r-27bf950bdbbd"
GOOD = "2026-10-22"
BIRTH = "1971-03-02"
RECEIPT = "klode-kit.redate_receipt/1"


# --------------------------------------------------------------------------- 1: page_date


def test_birth_date_line_is_not_a_candidate_labelled_visit_wins():
    lines = ["示例病院 検査結果", "氏名：テスト患者", "生年月日：1971-03-02", "採取日：2026-10-22", "報告日：2026-10-24"]
    assert M.page_date({"vision": lines}) == "2026-10-22"


def test_birth_date_on_the_line_after_a_bare_label_is_not_a_candidate():
    for bare in ("生年月日", "生年月日：", "出生日期", "DOB", "Birth", "誕生日"):
        lines = [bare, "1971-03-02", "検査日：2026-10-22"]
        assert M.page_date({"vision": lines}) == "2026-10-22", bare


def test_birth_labels_latin_case_insensitive_and_all_listed():
    for label in ("生年月日", "出生日期", "出生", "生日", "誕生日", "DOB", "dob", "Birth", "BIRTH"):
        assert M.page_date({"vision": [f"{label}: 1971-03-02", "報告日：2026-10-22"]}) == "2026-10-22", label


def test_a_page_with_only_a_birth_date_has_no_date():
    assert M.page_date({"vision": ["生年月日：1971-03-02", "血红蛋白 128 g/L"]}) is None
    assert M.page_date({"vision": ["生年月日", "1971-03-02"], "mlx": ["DOB 1971-03-02"]}) is None
    # label and the date of the same line: the line is a birth line even when it also names a visit label
    assert M.page_date({"vision": ["生年月日 1971-03-02 採取日 2026-10-22"]}) is None


def test_birth_date_does_not_become_the_anywhere_fallback():
    # no labelled line: the earliest date anywhere, but never the birth date
    assert M.page_date({"vision": ["生年月日：1971-03-02", "2026-10-24 印刷", "2026-10-22 来院"]}) == "2026-10-22"


def test_new_date_labels_label_a_line():
    for label in ("采样", "采集", "送检", "报告时间", "检验时间", "検査日", "採取日", "採血日", "受付日", "報告日", "依頼日",
                  "実施日", "診療日", "処方日", "交付年月日", "調剤日", "日期"):
        lines = ["2026-10-30 印刷", f"{label}：2026-10-22"]
        assert M.page_date({"vision": lines}) == "2026-10-22", label


def test_japanese_labels_after_nfkc_and_spaces():
    assert M.page_date({"vision": ["2026-10-30 印刷", "採 取 日 ２０２６-１０-２２"]}) == "2026-10-22"


def test_riqi_pages_are_unchanged():
    lab = ["示例医院 检验报告单", "姓名：测试患者甲", "日期：2026-10-21", "血红蛋白 128 g/L 115-150"]
    assert M.page_date({"vision": lab}) == "2026-10-21"
    assert M.page_date({"vision": ["打印 2026-01-02", "采样日期：2026-10-21", "报告日期：2026-10-23"]}) == "2026-10-21"
    assert M.page_date({"vision": ["2026-10-23 打印", "复查 2026-10-21"]}) == "2026-10-21"


def test_votes_across_routes_still_decide_after_birth_dates_are_dropped():
    good = ["生年月日：1971-03-02", "採取日：2026-10-22"]
    other = ["生年月日：1971-03-02", "採取日：2026-10-27"]
    assert M.page_date({"vision": good, "mlx": good, "claude": other}) == "2026-10-22"
    assert M.page_date({"vision": good, "mlx": other}) == "2026-10-22"   # 1:1 -> earliest


_DATES = st.dates(min_value=dt.date(2000, 1, 1), max_value=dt.date(2099, 12, 31))
_BIRTHS = st.dates(min_value=dt.date(1900, 1, 1), max_value=dt.date(1999, 12, 31))
_BIRTH_LABELS = st.sampled_from(["生年月日", "出生日期", "出生", "生日", "誕生日", "DOB", "Birth"])
_VISIT_LABELS = st.sampled_from(["日期", "採取日", "検査日", "受付日", "報告日", "调剤日", "処方日"])


@settings(max_examples=120, deadline=None)
@given(_BIRTHS, _DATES, _BIRTH_LABELS, _VISIT_LABELS, st.booleans(), st.booleans())
def test_property_a_birth_date_is_never_the_page_date(birth, visit, blabel, vlabel, split, birth_first):
    """S4: whichever label, whichever layout (one line or two), whichever order, the earliest date of the page being
    the date of birth never makes it the record's date; a page holding only a birth date has none."""
    b_lines = [blabel, birth.isoformat()] if split else [f"{blabel}：{birth.isoformat()}"]
    v_lines = [f"{vlabel}：{visit.isoformat()}"]
    lines = b_lines + v_lines if birth_first else v_lines + b_lines
    assert M.page_date({"vision": lines}) == visit.isoformat()
    assert M.page_date({"vision": b_lines}) is None


# --------------------------------------------------------------------------- helpers for the command


def redate(project: Path, batch, *extra: str, capsys=None):
    from klode_kit.audit import stage

    argv = ["--project", str(project), "--redate", *extra]
    if batch is not None:
        argv += ["--batch", batch]
    code = stage.main(argv)
    out = capsys.readouterr().out if capsys is not None else ""
    lines = [x for x in out.splitlines() if x.strip()]
    return code, (json.loads(lines[-1]) if lines else None)


def set_front_date(project: Path, rec: str, old: str, new: str) -> None:
    path = W.record_path(project, rec)
    text = path.read_text(encoding="utf-8")
    needle = f'"date": "{old}"'
    assert text.count(needle) == 1
    path.write_text(text.replace(needle, f'"date": "{new}"'), encoding="utf-8")


def set_body_date(project: Path, rec: str, rev: int, old: str, new: str) -> None:
    path = W.body_path(project, rec, rev)
    text = path.read_text(encoding="utf-8")
    needle = f'"date": "{old}"'
    assert text.count(needle) == 1
    os.chmod(path, 0o644)
    path.write_text(text.replace(needle, f'"date": "{new}"'), encoding="utf-8")
    os.chmod(path, 0o444)


def birth_dated(base_: RF.Base, *recs: str) -> None:
    """The records ``recs`` carry the date of birth, as the four real ones did, and the views are named after it."""
    from klode_kit.audit import views

    for rec in recs:
        set_front_date(base_.project, rec, GOOD, BIRTH)
    views.rebuild(base_.project)


def drop_receipt_dates(project: Path, batch: str) -> None:
    """As for the real records: kb-ocr's receipt carries no ``date`` for the pages (``record_meta`` derives it)."""
    path = project / "work" / batch / "receipt.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for page in data["pages"]:
        page.pop("date", None)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def view_names(project: Path) -> dict[str, set[str]]:
    return {d: {p.name for p in (project / d).glob("*.md")} for d in ("records", "verified") if (project / d).is_dir()}


def rev_bodies(project: Path) -> dict[str, bytes]:
    return {k: v for k, v in tree(project / "records" / "_rev").items() if not k.endswith("/")}


def log_of(project: Path, batch: str) -> list[dict]:
    return W.jsonl(project / "work" / batch / "audit" / "redate.jsonl")


# --------------------------------------------------------------------------- 2: the plan


def test_plan_lists_the_birth_dated_record_and_writes_nothing(base, capsys):
    p, b = base.project, base.batch
    birth_dated(base, AUTO_REC)
    assert f"{BIRTH}-lab.md" in view_names(p)["records"]
    before = tree_sha(p)
    code, rc = redate(p, b, capsys=capsys)
    assert code == 0
    assert rc["schema"] == RECEIPT and rc["result"] == "planned" and rc["exit_code"] == 0 and rc["batch_id"] == b
    assert rc["records"] == [{"rec_id": AUTO_REC, "old": BIRTH, "new": GOOD, "source": "page"}]
    assert tree_sha(p) == before                 # not a byte, not a view, not a log line


def test_plan_of_a_correct_batch_is_nothing(base, capsys):
    p, b = base.project, base.batch
    before = tree_sha(p)
    code, rc = redate(p, b, capsys=capsys)
    assert (code, rc["result"], rc["records"]) == (0, "nothing", [])
    assert tree_sha(p) == before
    code, rc = redate(p, b, "--apply", capsys=capsys)
    assert (code, rc["result"], rc["records"]) == (0, "nothing", [])
    assert not log_of(p, b) and tree_sha(p) == before


def test_plan_takes_records_of_any_status(base, capsys):
    p, b = base.project, base.batch
    birth_dated(base, AUTO_REC, PENDING_REC, OTHER_REC)
    code, rc = redate(p, b, capsys=capsys)
    assert code == 0
    assert [r["rec_id"] for r in rc["records"]] == sorted([AUTO_REC, PENDING_REC, OTHER_REC])
    assert all((r["old"], r["new"], r["source"]) == (BIRTH, GOOD, "page") for r in rc["records"])


# --------------------------------------------------------------------------- 3: apply


def test_apply_logs_sets_the_date_renames_the_views_and_keeps_the_revision_bodies(base, capsys):
    p, b = base.project, base.batch
    birth_dated(base, AUTO_REC)
    bodies, other_front = rev_bodies(p), W.record_path(p, PENDING_REC).read_bytes()
    code, rc = redate(p, b, "--apply", capsys=capsys)
    assert code == 0 and rc["result"] == "applied"
    assert rc["records"] == [{"rec_id": AUTO_REC, "old": BIRTH, "new": GOOD, "source": "page"}]

    (row,) = log_of(p, b)
    assert {k: row[k] for k in ("op", "rec_id", "old", "new", "source")} == \
        {"op": "redate", "rec_id": AUTO_REC, "old": BIRTH, "new": GOOD, "source": "page"}
    assert isinstance(row["at"], str) and row["at"]
    front = W.front(p, AUTO_REC)
    assert front["date"] == GOOD
    # both views follow the new date (it shares the date with the two other records: the name may carry a counter)
    for d in ("records", "verified"):
        mine = [x for x in (p / d).glob("*.md") if os.readlink(x).endswith(f"{AUTO_REC}.r1.md")]
        assert len(mine) == 1 and mine[0].name.startswith(f"{GOOD}-lab"), (d, mine)
        assert f"{BIRTH}-lab.md" not in view_names(p)[d]
    # append-only store: revision bodies untouched, a record that was right untouched
    assert rev_bodies(p) == bodies
    assert W.record_path(p, PENDING_REC).read_bytes() == other_front


def test_second_apply_changes_nothing(base, capsys):
    p, b = base.project, base.batch
    birth_dated(base, AUTO_REC, PENDING_REC)
    assert redate(p, b, "--apply", capsys=capsys)[1]["result"] == "applied"
    after = tree_sha(p)
    code, rc = redate(p, b, "--apply", capsys=capsys)
    assert (code, rc["result"], rc["records"]) == (0, "nothing", [])
    assert tree_sha(p) == after and len(log_of(p, b)) == 2


# --------------------------------------------------------------------------- 4: the export


def _obs_text(res: dict) -> str:
    """The haemoglobin observation line of AUTO_REC (the record also prints its own ``日期`` field as a source line)."""
    (line,) = [x for x in res["files"][f"library/records/{AUTO_REC}.txt"].splitlines() if x.startswith("血红蛋白")]
    return line


def test_export_uses_the_front_date_of_a_redated_record(base, capsys):
    from klode_kit.build import records_to_txt as RT

    p, b = base.project, base.batch
    set_body_date(p, AUTO_REC, 1, GOOD, BIRTH)           # the body was written with the birth date
    set_front_date(p, AUTO_REC, GOOD, BIRTH)
    assert BIRTH in _obs_text(RT.export(p)) and GOOD not in _obs_text(RT.export(p))
    assert redate(p, b, "--apply", capsys=capsys)[1]["result"] == "applied"
    assert W.body(p, AUTO_REC, 1)["date"] == BIRTH       # the body keeps its date ...
    res = RT.export(p)
    assert GOOD in _obs_text(res) and BIRTH not in _obs_text(res)   # ... the export does not use it
    assert RT.export(p) == res
    assert any(GOOD in line["text"] for line in res["export_map"] if line["text"].startswith("血红蛋白"))


def test_export_of_a_record_without_a_redate_event_uses_the_body_date(base):
    from klode_kit.build import records_to_txt as RT

    p = base.project
    set_body_date(p, AUTO_REC, 1, GOOD, BIRTH)
    set_front_date(p, AUTO_REC, GOOD, "2026-10-23")      # a front date that was never written by --redate
    text = _obs_text(RT.export(p))
    assert BIRTH in text and "2026-10-23" not in text and GOOD not in text


# --------------------------------------------------------------------------- 5: a later writeback


def test_writeback_after_apply_writes_the_new_date_into_the_new_revision(base, tmp_path, capsys):
    p, b, adir = base.project, base.batch, base.adir
    drop_receipt_dates(p, b)
    birth_dated(base, PENDING_REC)
    assert redate(p, b, "--apply", capsys=capsys)[1]["result"] == "applied"
    code, _, err = W.submit(p, W.export(p, b, adir, W.picks(adir, [PENDING_REC])), tmp_path / "w")
    assert code == 0, err
    assert W.body(p, PENDING_REC, 2)["date"] == GOOD
    assert W.front(p, PENDING_REC)["date"] == GOOD
    assert W.body(p, PENDING_REC, 1)["date"] == GOOD     # r1 was written before the corruption of the fixture


def test_writeback_without_redate_would_keep_the_birth_date(base, tmp_path):
    """The reason ``--redate`` exists: a redo never renames its views, so fixing the rule alone repairs nothing."""
    p, b, adir = base.project, base.batch, base.adir
    drop_receipt_dates(p, b)
    birth_dated(base, PENDING_REC)
    code, _, err = W.submit(p, W.export(p, b, adir, W.picks(adir, [PENDING_REC])), tmp_path / "w")
    assert code == 0, err
    assert W.body(p, PENDING_REC, 2)["date"] == BIRTH


# --------------------------------------------------------------------------- 6: refusals and usage


def test_refusals_write_nothing(base, tmp_path, capsys):
    p = base.project
    empty = tmp_path / "empty"
    empty.mkdir()
    for project, batch in ((p, "nope"), (p, "0" * 12), (empty, base.batch)):
        for extra in ((), ("--apply",)):
            before = tree_sha(project)
            code, rc = redate(project, batch, *extra, capsys=capsys)
            assert code == 30, (batch, extra, rc)
            assert (rc["schema"], rc["result"], rc["exit_code"]) == (RECEIPT, "refused", 30) and rc["error"]
            assert tree_sha(project) == before


def test_a_batch_with_no_audit_revision_is_refused(base, capsys):
    p, b = base.project, base.batch
    ghost = "f" * 12
    (p / "work" / ghost).mkdir()
    before = tree_sha(p)
    code, rc = redate(p, ghost, "--apply", capsys=capsys)
    assert code == 30 and rc["result"] == "refused"
    assert tree_sha(p) == before


@pytest.mark.parametrize("extra", [("--again",), ("--reopen", "r-x/r001-number-0"), ("--writeback",),
                                   ("--attribute", "--reviewer-model", "m", "--reviewer-rule", "r"),
                                   ("--reason", "x")])
def test_redate_is_a_step_of_its_own(base, capsys, extra):
    p = base.project
    before = tree_sha(p)
    code, rc = redate(p, base.batch, *extra, capsys=capsys)
    assert code == 2 and rc is None
    assert tree_sha(p) == before


def test_usage_errors(base, capsys):
    p = base.project
    before = tree_sha(p)
    assert redate(p, None, capsys=capsys) == (2, None)                         # --redate needs --batch
    from klode_kit.audit import stage

    assert stage.main(["--project", str(p), "--apply", "--batch", base.batch]) == 2   # --apply belongs to --redate
    assert tree_sha(p) == before


def test_apply_holds_the_work_lock(base, capsys):
    import fcntl

    p, b = base.project, base.batch
    birth_dated(base, AUTO_REC)
    fd = os.open(p / "work" / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = tree_sha(p)
        code, rc = redate(p, b, "--apply", capsys=capsys)
        assert code == 40 and rc["result"] == "refused"
        assert tree_sha(p) == before
    finally:
        os.close(fd)
