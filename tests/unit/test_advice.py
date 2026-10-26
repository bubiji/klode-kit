"""WI-12 (unit): the kb-verifier advice contract ``a<N>/advice.json`` and its one door, ``klode-kit kb-advice``.

Design 02 r2.3 row 3: ``kb-verifier`` is read-only and outputs ``advice.json {field_id, suggestion, evidence}``; the confirm
page shows it as a hint; it **never** changes a status (only ``writeback.py`` does). So this module:

- ``schema()`` / ``issues(doc)``: the file is a JSON array of ``{field_id, suggestion, evidence}`` (all three required,
  nothing else); ``confirm_page`` reads exactly this list;
- ``shown_fields(adir)``: the fields the confirm page would show (verdict not ``agree``, or uncertain, or missing) - the
  only ones advice may speak about;
- ``ingest(project, batch, doc, n)``: validates (schema, one entry per field, field shown on the page) and writes
  ``a<N>/advice.json`` once (read-only; the same bytes again are a no-op, other bytes are refused). It opens nothing
  under ``records/`` for writing.

Law (S5): ingesting advice leaves every byte of the project except the new ``advice.json`` as it was, and the file reads
back as the same list (round trip); the confirm page then shows the hint without selecting anything (08b-04).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.confirm_fx import FIXTURES, audited, write_record
from _support.htmltree import parse
from klode_kit.audit import advice as A
from klode_kit.audit import confirm_page as CP
from klode_kit.__main__ import COMMANDS

pytestmark = pytest.mark.unit

U8 = FIXTURES / "fx-u8"


@pytest.fixture(scope="module")
def u8_world(tmp_path_factory):
    root = tmp_path_factory.mktemp("advice-u8")
    batch, adir = audited(root, U8)
    for rec in {f["field_id"].split("/")[0] for c in A_cands(adir) for f in c["fields"]}:
        write_record(root, {"rec_id": rec, "doc_id": "d-000000000000", "page_id": "d-000000000000/p001",
                            "record_idx": 0, "type": "lab", "status": "pending_confirm", "candidates": []})
    return root, batch, adir


def A_cands(adir: Path) -> list[dict]:
    return [json.loads(x) for x in (adir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.fixture()
def world(u8_world, tmp_path):
    root, batch, adir = u8_world
    project = tmp_path / "proj"
    shutil.copytree(root, project, symlinks=True)
    n = int(adir.name[1:])
    return project, batch, project / "work" / batch / "audit" / adir.name, n


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def good_advice(adir: Path, k: int = 2) -> list[dict]:
    return [{"field_id": f["field_id"], "suggestion": f"{f['field_id'][-8:]}-sug", "evidence": "原图该行可读"}
            for f in A.shown_fields(adir)[:k]]


# --------------------------------------------------------------------------- the contract


def test_advice_is_a_registered_command():
    assert COMMANDS["kb-advice"][0] == "klode_kit.audit.advice:main"


def test_schema_document():
    doc = A.schema()
    assert doc["type"] == "array"
    assert doc["items"]["required"] == ["field_id", "suggestion", "evidence"]
    assert doc["items"]["additionalProperties"] is False
    assert doc["$id"].endswith("advice.json")


VALID = {"field_id": "r-750a826105c6/r006-number-0", "suggestion": "6.2", "evidence": "原图第 6 行为 6.2"}


@pytest.mark.parametrize("doc,ok", [
    ([], True),
    ([VALID], True),
    ([VALID, {**VALID, "field_id": "r-750a826105c6/r006-unit-0"}], True),
    ({"advice": []}, False),                                   # the file is the list, not the wrapper
    (None, False),
    ([{**VALID, "extra": 1}], False),
    ([{k: v for k, v in VALID.items() if k != "evidence"}], False),
    ([{k: v for k, v in VALID.items() if k != "suggestion"}], False),
    ([{**VALID, "suggestion": ""}], False),
    ([{**VALID, "suggestion": 6.2}], False),
    ([{**VALID, "evidence": ""}], False),
    ([{**VALID, "field_id": "no-slash"}], False),
    ([{**VALID, "field_id": "r-XYZ/r006-number-0"}], False),
    ([{**VALID, "suggestion": "x" * 301}], False),
    ([{**VALID, "evidence": "x" * 1001}], False),
    ([VALID, "text"], False),
])
def test_issues(doc, ok):
    assert (A.issues(doc) == []) is ok, A.issues(doc)


# --------------------------------------------------------------------------- shown fields


def test_shown_fields_are_the_confirm_page_fields(world):
    project, batch, adir, n = world
    shown = A.shown_fields(adir)
    assert shown, "fx-u8 has fields in dispute"
    page = parse(CP.render(project, batch, n).decode("utf-8"))
    assert {f["field_id"] for f in shown} == {i.get("data-field-id") for i in page.by_class("kk-item")}
    for f in shown:
        assert set(f) == {"field_id", "item_label", "verdict", "seen", "crop"}
        assert isinstance(f["seen"], dict) and f["seen"]
        if f["crop"] is not None:                      # relative to a<N>, readable by an agent whose cwd is a<N>
            assert f["crop"].startswith("crops/") and (adir / f["crop"]).is_file()
    assert any(f["crop"] for f in shown)


# --------------------------------------------------------------------------- ingest


def test_ingest_writes_once_read_only_and_touches_nothing_else(world):
    project, batch, adir, n = world
    before = snapshot(project)
    doc = good_advice(adir)
    path = A.ingest(project, batch, doc, n)
    assert path == adir / "advice.json"
    assert json.loads(path.read_text(encoding="utf-8")) == doc
    assert not os.access(path, os.W_OK) and stat.S_IMODE(path.stat().st_mode) == 0o444
    after = snapshot(project)
    assert set(after) - set(before) == {str(path.relative_to(project))}
    assert {k: v for k, v in after.items() if k in before} == before          # every other byte as it was
    assert not any(p.name.startswith(".advice") for p in adir.iterdir()), "temporary file left behind"
    # same bytes again: no-op; other bytes: refused, file untouched
    assert A.ingest(project, batch, doc, n) == path
    with pytest.raises(A.AdviceError) as err:
        A.ingest(project, batch, doc[:1], n)
    assert err.value.exit_code == 30
    assert json.loads(path.read_text(encoding="utf-8")) == doc


def test_empty_advice_is_a_legal_answer(world):
    project, batch, adir, n = world
    A.ingest(project, batch, [], n)
    assert json.loads((adir / "advice.json").read_text(encoding="utf-8")) == []
    page = parse(CP.render(project, batch, n).decode("utf-8"))
    assert page.by_class("kk-advice") == []


def test_default_revision_is_the_newest(world):
    project, batch, adir, n = world
    assert A.ingest(project, batch, [], None) == adir / "advice.json"


@pytest.mark.parametrize("make,why", [
    (lambda d, a: [{"field_id": "r-000000000000/r001-number-0", "suggestion": "1", "evidence": "e"}], "not on the page"),
    (lambda d, a: [{"field_id": next(f["field_id"] for f in d if f["verdict"] == "agree"), "suggestion": "1",
                    "evidence": "e"}], "agreeing field is not shown"),
    (lambda d, a: a + a[:1], "two entries for one field"),
    (lambda d, a: [{**a[0], "extra": True}], "schema"),
    (lambda d, a: {"advice": a}, "schema"),
])
def test_ingest_refuses_bad_advice_and_writes_nothing(world, make, why):
    project, batch, adir, n = world
    cells = A_cands(adir)
    flat = [f for c in cells for f in c["fields"]]
    before = snapshot(project)
    with pytest.raises(A.AdviceError) as err:
        A.ingest(project, batch, make(flat, good_advice(adir)), n)
    assert err.value.exit_code == 30, why
    assert snapshot(project) == before, why


def test_unknown_batch_or_revision_is_a_usage_error(world):
    project, batch, adir, n = world
    with pytest.raises(A.AdviceError) as err:
        A.ingest(project, "0" * 12, [], None)
    assert err.value.exit_code == 2
    with pytest.raises(A.AdviceError) as err:
        A.ingest(project, batch, [], 99)
    assert err.value.exit_code == 2


def test_the_confirm_page_shows_the_hint_and_selects_nothing(world):
    project, batch, adir, n = world
    doc = good_advice(adir)
    plain = parse(CP.render(project, batch, n).decode("utf-8"))
    A.ingest(project, batch, doc, n)
    page = parse(CP.render(project, batch, n).decode("utf-8"))
    hints = [h.text() for h in page.by_class("kk-advice")]
    assert len(hints) == len(doc)
    for d in doc:
        assert any(d["suggestion"] in h for h in hints)
    assert [i.get("data-field-id") for i in page.by_class("kk-item")] == [i.get("data-field-id")
                                                                           for i in plain.by_class("kk-item")]
    assert not [n_ for n_ in page.iter() if "checked" in n_.attrs], "advice must not pre-select a radio"


# --------------------------------------------------------------------------- law: S5


TEXT = st.text(alphabet=st.one_of(st.characters(whitelist_categories=("Lu", "Ll", "Nd", "Lo")),
                                  st.sampled_from(list("<>&\"'/;."))), min_size=1, max_size=40)


@given(data=st.data())
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_ingest_round_trip_and_nothing_else_changes(u8_world, data):
    root, batch, adir0 = u8_world
    with tempfile.TemporaryDirectory(prefix="advice-prop-") as tmp:
        project = Path(tmp) / "proj"
        shutil.copytree(root, project, symlinks=True)
        adir = project / "work" / batch / "audit" / adir0.name
        shown = A.shown_fields(adir)
        picked = data.draw(st.lists(st.sampled_from([f["field_id"] for f in shown]), unique=True, max_size=len(shown)))
        doc = [{"field_id": fid, "suggestion": data.draw(TEXT), "evidence": data.draw(TEXT)} for fid in picked]
        before = snapshot(project)
        A.ingest(project, batch, doc, int(adir0.name[1:]))
        after = snapshot(project)
        assert json.loads((adir / "advice.json").read_bytes()) == doc
        assert {k: v for k, v in after.items() if k in before} == before
        assert len(set(after) - set(before)) == 1
        page = parse(CP.render(project, batch, int(adir0.name[1:])).decode("utf-8"))
        hints = [h.text() for h in page.by_class("kk-advice")]
        assert len(hints) == len(doc)                             # one hint per entry; text arrives as text, never markup
        for d in doc:
            assert any(d["suggestion"] in h for h in hints)


# --------------------------------------------------------------------------- CLI


def test_cli_from_file(world, capsys):
    project, batch, adir, n = world
    src = project.parent / "in.json"
    src.write_text(json.dumps(good_advice(adir)), encoding="utf-8")
    assert A.main(["--batch", batch, "--project", str(project), "--from", str(src)]) == 0
    assert str(adir / "advice.json") in capsys.readouterr().out
    assert json.loads((adir / "advice.json").read_text(encoding="utf-8")) == json.loads(src.read_text(encoding="utf-8"))
    assert A.main(["--batch", batch, "--project", str(project), "--from", str(src)]) == 0   # idempotent
    src.write_text("[]", encoding="utf-8")
    assert A.main(["--batch", batch, "--project", str(project), "--from", str(src)]) == 30


def test_cli_usage_and_input_errors(world, capsys):
    project, batch, adir, n = world
    base = ["--batch", batch, "--project", str(project)]
    assert A.main([]) == 2
    assert A.main(base) == 2                                           # neither --from nor --run
    assert A.main([*base, "--from", "x.json", "--run"]) == 2           # both
    assert A.main([*base, "--from", str(project / "missing.json")]) == 2
    bad = project / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert A.main([*base, "--from", str(bad)]) == 30
    bad.write_bytes(b"\xff\xfe")
    assert A.main([*base, "--from", str(bad)]) == 30
    assert A.main(["--batch", "zz", "--project", str(project), "--from", str(bad)]) == 2
    assert A.main([*base, "--run", "--max-budget-usd", "0"]) == 2
    assert A.main([*base, "--run", "--timeout-s", "-1"]) == 2
    assert not (adir / "advice.json").exists()
