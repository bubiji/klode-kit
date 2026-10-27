"""U8 (04 section 6): open ``confirm/20261022.html``, per disagreement pick one route or "none is right / type it",
export ``choices.json``; ``kb-audit --writeback choices.json``.

What the user sees (04): every item of the page = the disagreement crop + the four routes' candidate values + the
starred recommendation (majority vote); after the writeback ``verified/2026-10-22-lab.md`` is ``status: verified``;
the record on which the user answered "none is right" and typed nothing is ``status: disputed`` with
``uncertain: [p002-l07]``, and it is not among the inputs of ``build_receipt.json``.

Matrix U8-01, U8-02, U8-03, U8-04 (lane contract_replay; U8-02 cli_integration needs_node). ``fx-u8``: two pages;
page 1 has one majority and one all-different field, page 2 a 2:2 tie on its row-2 value (the line ``p002-l07`` of the
fixture) and a majority. Replay boundary: recorded OCR rows; ``doctor`` stub. The user's browser clicks are simulated
by the page's own exporter (Python mirror ``confirm_page.export_choices`` in the replay lane; the page's real
JavaScript run by node in U8-02). Not automated and listed in 08 as the user's own check: radio buttons, the typed
field, the download button and the crops rendering from ``file:`` in a browser.

Facts reported next to the tests: the record of page 1 is ``records/2026-10-22-lab.md`` (verified; ``verified/``
links it) and the disputed record of page 2 is ``records/2026-10-22-lab-2.md``; ``uncertain`` is rendered as the id list
``['p002-l07']`` (R-14). The build of U8-03 runs with the real ``klode`` (the same replay-lane practice as
``test_build_steps``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _support import htmltree
from _support.accept_fx import Flow
from _support.accept_fx import front as _front
from klode_kit.audit import confirm_page as CP

ROUTES = ["vision", "mlx", "claude", "codex"]
TRUTH = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "fx-u8" / "truth.json").read_text(encoding="utf-8"))



@pytest.fixture()
def flow(tmp_path) -> Flow:
    f = Flow(tmp_path)
    f.intake()
    return f


# --------------------------------------------------------------------------- the page


@pytest.mark.contract_replay
def test_u8_confirm_writeback(flow):
    """U8-01 (L2 S1): the page shows crop + four candidates + star per item; after the clicks and the writeback the
    verified view is ``status: verified``."""
    root = htmltree.parse(flow.page.read_text(encoding="utf-8"))
    items = root.by_class("kk-item")
    fields = flow.fields()
    assert len(items) == len(fields) == 4  # the 4 disagreements of "audit: 4 条分歧"
    for item in items:
        fid = item.get("data-field-id")
        f = fields[fid]
        # (1) the crop of the disagreement: an image that really is on disk through the folder form of the page
        (img,) = item.by_class("kk-crop")
        crop = flow.project / "confirm" / "20261022" / img.get("src")
        assert crop.is_file() and crop.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", (fid, img.get("src"))
        # (2) the four routes' candidate values, in route order, as the audit read them
        cands = item.by_class("kk-cand")
        assert [c.get("data-route") for c in cands] == ROUTES
        shown = {c.get("data-route"): (c.by_class("kk-value")[0].text() if c.by_class("kk-value") else None)
                 for c in cands}
        assert shown == {r: f["seen"].get(r) for r in ROUTES}
        # (3) the star: on the routes that hold the majority value, only when there is a majority
        starred = [c.get("data-route") for c in cands if c.by_class("kk-star")]
        if f["verdict"] == "majority":
            assert f["recommended"] is not None
            assert starred and all(f["seen"][r] == f["recommended"] for r in starred)
            assert sorted(starred) == sorted(r for r in ROUTES if f["seen"][r] == f["recommended"])
        else:
            assert starred == [] and f["recommended"] is None, f["verdict"]
        # (4) "none is right" and "type it" are always offered
        values = {inp.get("value") for inp in item.find_all("input")}
        assert {"none", "manual"} <= values
    truth_tie = [x for x in TRUTH["fields"] if x["designed"] == "tie"] or \
        [x for x in TRUTH["fields"] if x["key"] == "p002" and x["designed"] not in ("agree", "majority")]
    assert any(f["verdict"] == "tie" for f in fields.values()) and truth_tie

    # the user clicks and the writeback runs
    done = flow.writeback(flow.export(flow.clicks()))
    assert done.returncode == 10, done.stderr.decode()  # a record is still disputed: the run says so (exit 10)
    views = flow.views()
    verified = flow.project / "verified" / "2026-10-22-lab.md"
    assert verified.is_symlink() and _front(verified)["status"] == "verified"
    assert views["2026-10-22-lab.md"]["status"] == "verified" and views["2026-10-22-lab.md"]["uncertain"] == []


@pytest.mark.contract_replay
def test_u8_none_disputed_excluded(flow):
    """U8-03 (L2 S3): the record with a "none is right" and no typed value is ``disputed``, ``uncertain`` names
    ``p002-l07``, it is not under ``verified/`` and not among the inputs of ``build_receipt.json``."""
    assert flow.writeback(flow.export(flow.clicks())).returncode == 10
    views = flow.views()
    disputed = views["2026-10-22-lab-2.md"]
    assert disputed["status"] == "disputed" and disputed["verified_by"] is None
    assert disputed["uncertain"] == ["p002-l07"]  # R-14: the entries' ids, in the page's own naming
    assert sorted(p.name for p in (flow.project / "verified").iterdir()) == ["2026-10-22-lab.md"]

    build = flow.klode_kit("build")
    assert build.returncode == 0, build.stdout.decode()[-1500:] + build.stderr.decode()[-1500:]
    (receipt_path,) = sorted(flow.project.rglob("build_receipt.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "PASS"
    assert sorted(receipt["audit_bindings"]) == [views["2026-10-22-lab.md"]["rec_id"]]
    assert receipt["excluded"] == [{"rec_id": disputed["rec_id"], "reason": "not_verified:disputed"}]
    # and nothing of the disputed record is in what the library was built from
    lib = receipt_path.parent
    assert disputed["rec_id"] not in "\n".join(p.read_text(encoding="utf-8") for p in (lib / "library").rglob("*.txt"))
    assert disputed["rec_id"] not in (lib / "src" / "export_map.jsonl").read_text(encoding="utf-8")


@pytest.mark.contract_replay
def test_u8_resubmit_same_choices(flow):
    """U8-04 (L2 S5): the same ``choices.json`` again changes nothing: no new revision, the views as they were."""
    choices = flow.export(flow.clicks())
    assert flow.writeback(choices).returncode == 10
    before_views = flow.views()
    revs = flow.revisions()
    links = {p.name: p.readlink() for d in ("records", "verified") for p in (flow.project / d).glob("*.md")
             if p.is_symlink()}
    tree = {p.relative_to(flow.project).as_posix(): p.read_bytes()
            for p in sorted((flow.project / "records").rglob("*")) if p.is_file() and not p.name.startswith(".")}
    again = flow.writeback(choices)
    assert again.returncode in (0, 10), again.stderr.decode()
    assert flow.revisions() == revs
    assert flow.views() == before_views
    assert {p.name: p.readlink() for d in ("records", "verified") for p in (flow.project / d).glob("*.md")
            if p.is_symlink()} == links
    after = {p.relative_to(flow.project).as_posix(): p.read_bytes()
             for p in sorted((flow.project / "records").rglob("*")) if p.is_file() and not p.name.startswith(".")}
    assert after == tree


# --------------------------------------------------------------------------- the page's own JavaScript, run by node


@pytest.mark.cli_integration
@pytest.mark.needs_node
def test_u8_export_via_node(flow):
    """U8-02 (L3 S1): the exporter the user's browser runs, executed by node on the page, gives the choices the Python
    exporter gives, and the writeback of them ends as in U8-01."""
    node = shutil.which("node")
    assert node is not None, "node is required by the cli_integration lane (needs_node): install node"
    clicks = flow.clicks()
    selections = flow.tmp / "selections.json"
    selections.write_text(json.dumps(clicks, ensure_ascii=False), encoding="utf-8")
    out = subprocess.run([node, str(CP.EXPORT_JS), str(flow.page), str(selections)], capture_output=True, timeout=60)
    assert out.returncode == 0, out.stderr.decode()
    from_js = json.loads(out.stdout)
    assert from_js == CP.export_choices(flow.page.read_bytes(), clicks)
    choices = flow.tmp / "choices-js.json"
    choices.write_bytes(out.stdout)
    done = flow.writeback(choices)
    assert done.returncode == 10, done.stderr.decode()
    views = flow.views()
    assert views["2026-10-22-lab.md"]["status"] == "verified"
    assert views["2026-10-22-lab-2.md"]["status"] == "disputed" and views["2026-10-22-lab-2.md"]["uncertain"] == ["p002-l07"]
