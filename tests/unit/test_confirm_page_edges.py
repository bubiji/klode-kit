"""WI-08b edges: guard clauses of the confirm page renderer and exporter (malformed revision files, record files,
pages that are not confirm pages). Added while implementing; the matrix rows are in
tests/unit/test_confirm_page.py and tests/cli/test_confirm_export_node.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support.confirm_fx import FIXTURES, audited, candidates, write_record
from _support.htmltree import parse
from klode_kit.audit import confirm_page as CP

pytestmark = pytest.mark.unit

U8 = FIXTURES / "fx-u8"
ROUTES = ("vision", "mlx", "claude", "codex")


def hand_rev(tmp_path: Path, *, crop: str | None = None, field_extra: dict | None = None) -> tuple[str, Path]:
    batch = "0123456789ab"
    adir = tmp_path / "work" / batch / "audit" / "a1"
    adir.mkdir(parents=True)
    rec, fid = "r-000000000001", "r-000000000001/r001-number-0"
    (adir / "runs.json").write_text(json.dumps({e: f"{batch}/{e}/0123456789ab/t1" for e in ROUTES}), "utf-8")
    cell = {"field_id": fid, "rec_id": rec, "slot": "r001-number-0", "cell": {"row": 1, "col": "number-0"},
            "candidates": {e: {"value": str(i)} for i, e in enumerate(ROUTES)}, "prov": {}}
    (adir / "cells.jsonl").write_text(json.dumps(cell) + "\n", encoding="utf-8")
    field = {"field_id": fid, "verdict": "all_differ", "recommended": None, "missing": [],
             "uncertain": ["all_differ"], "item_label": "x", "votes": [[str(i), [e]] for i, e in enumerate(ROUTES)],
             "crop": crop, **(field_extra or {})}
    cand = {"rec_id": rec, "doc_id": "d-000000000000", "page_id": "d-000000000000/p001", "record_idx": 0,
            "audit_rev": f"{batch}/a1", "status": "pending_confirm", "verified_by": None, "uncertain": [],
            "fields": [field]}
    (adir / "candidates.jsonl").write_text(json.dumps(cand) + "\n", encoding="utf-8")
    return batch, adir


def test_no_crop_says_so(tmp_path):
    batch, _ = hand_rev(tmp_path)
    doc = parse(CP.render(tmp_path, batch))
    (item,) = doc.by_class("kk-item")
    assert item.find_all("img") == [] and "无小图" in item.text()


def test_incomplete_revision_refused(tmp_path):
    batch, adir = hand_rev(tmp_path)
    (adir / "cells.jsonl").rename(adir / "cells.moved")
    with pytest.raises(CP.ConfirmError) as exc:
        CP.render(tmp_path, batch)
    assert exc.value.exit_code == 2


@pytest.mark.parametrize("name,content", [
    ("cells.jsonl", "not json\n"),
    ("cells.jsonl", "[1]\n"),
    ("runs.json", "[1]"),
    ("runs.json", "{"),
    ("cells.jsonl", ""),
])
def test_malformed_revision_files_refused(tmp_path, name, content):
    batch, adir = hand_rev(tmp_path)
    (adir / name).write_text(content, encoding="utf-8")
    with pytest.raises(CP.ConfirmError) as exc:
        CP.render(tmp_path, batch)
    assert exc.value.exit_code == 30


def test_duplicate_field_in_candidates_refused(tmp_path):
    batch, adir = hand_rev(tmp_path)
    cand = json.loads((adir / "candidates.jsonl").read_text("utf-8"))
    cand["fields"].append(dict(cand["fields"][0]))
    (adir / "candidates.jsonl").write_text(json.dumps(cand) + "\n", encoding="utf-8")
    with pytest.raises(CP.ConfirmError):
        CP.render(tmp_path, batch)


@pytest.mark.parametrize("text", ["no front matter", "---\n[1]\n---\n", "---\n{\"rec_id\": \"r-999999999999\"}\n---\n",
                                  "---\n{bad\n---\n"])
def test_bad_record_file_refused(tmp_path, text):
    batch, _ = hand_rev(tmp_path)
    path = tmp_path / "records" / "_by_id" / "r-000000000001.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(CP.ConfirmError):
        CP.render(tmp_path, batch)


def test_candidate_rev_from_record(tmp_path):
    """The candidate of this audit revision already in the record file keeps its rev number; rev_base is the
    record's latest rev."""
    batch, adir = hand_rev(tmp_path)
    write_record(tmp_path, {"rec_id": "r-000000000001", "candidates": [
        {"rev": 1, "audit_rev": f"{batch}/a1", "status": "pending_confirm", "uncertain": [], "created": "x"},
        {"rev": 2, "audit_rev": f"{batch}/a9", "status": "pending_confirm", "uncertain": [], "created": "y"}]})
    html = CP.render(tmp_path, batch)
    assert "生效 无 / 候选 r1 (pending_confirm)" in parse(html).text()
    out = CP.export_choices(html, [{"field_id": "r-000000000001/r001-number-0", "kind": "none"}])
    assert out["items"][0]["rev_base"] == 2


def test_cell_missing_from_cells_refused(tmp_path):
    batch, adir = hand_rev(tmp_path, field_extra={"field_id": "r-000000000001/r002-number-0"})
    with pytest.raises(CP.ConfirmError):
        CP.render(tmp_path, batch)


def test_unembeddable_script_refused(tmp_path, monkeypatch):
    batch, _ = hand_rev(tmp_path)
    bad = tmp_path / "x.js"
    bad.write_text("var a = '</script>';\n", encoding="utf-8")
    monkeypatch.setattr(CP, "EXPORT_JS", bad)
    with pytest.raises(CP.ConfirmError):
        CP.render(tmp_path, batch)


@pytest.mark.parametrize("page", [
    b"<html>no stamp</html>",
    b'<meta name="klode-kit-confirm-sha256" content="' + b"0" * 64 + b'">no data',
])
def test_not_a_confirm_page(page):
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(page, [])


def test_page_data_guards():
    with pytest.raises(CP.ConfirmError):
        CP.page_data("<p>nothing</p>")
    with pytest.raises(CP.ConfirmError):
        CP.page_data('<script type="application/json" id="klode-kit-confirm-data">{x</script>')
    with pytest.raises(CP.ConfirmError):
        CP.page_data('<script type="application/json" id="klode-kit-confirm-data">{"schema": "other"}</script>')


def test_export_accepts_text_and_non_mapping_click_refused(tmp_path):
    batch, adir = audited(tmp_path, U8)
    html = CP.write(tmp_path, batch).read_text(encoding="utf-8")
    assert CP.export_choices(html, [])["items"] == []
    assert CP.confirm_sha256(html) == CP.confirm_sha256(html.encode("utf-8"))
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(html, ["r-000000000001/r001-number-0"])
    fid = next(f["field_id"] for c in candidates(adir) for f in c["fields"] if f["verdict"] != "agree")
    with pytest.raises(CP.ConfirmError):
        CP.export_choices(html, [{"field_id": fid, "kind": "none", "reason": None}])


def test_cli_usage_and_explicit_rev(tmp_path, capsys):
    assert CP.main([]) == 2
    assert CP.main(["--help"]) == 0
    batch, adir = audited(tmp_path, U8)
    assert CP.main(["--batch", batch, "--project", str(tmp_path), "--rev", "1"]) == 0
    assert (adir / "confirm.html").is_file()
    assert CP.main(["--batch", batch, "--project", str(tmp_path), "--rev", "3"]) == 2
    capsys.readouterr()


def test_cli_default_project_is_cwd(tmp_path, monkeypatch, capsys):
    batch, adir = audited(tmp_path, U8)
    monkeypatch.chdir(tmp_path)
    assert CP.main(["--batch", batch]) == 0
    assert str(adir / "confirm.html") in capsys.readouterr().out


def test_cli_bad_revision_files_exit30(tmp_path, capsys):
    batch, adir = hand_rev(tmp_path, crop="work/elsewhere.png")
    assert CP.main(["--batch", batch, "--project", str(tmp_path)]) == 30
    assert "crop" in capsys.readouterr().err
    assert not (adir / "confirm.html").exists()
