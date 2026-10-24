"""WI-08b: the confirm page's exporter run by real node (matrix 08b-06; 04 U8; the pairing of the unit lane's
Python exporter in tests/unit/test_confirm_page.py).

``templates/confirm_export.js`` is the exact script embedded in ``confirm.html``; run by node as
``node confirm_export.js <confirm.html> [<selections.json>]`` it prints ``choices.json``. A missing node FAILS
(never skips). The confirm page is written by the real ``klode-kit confirm-page`` process.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from _support.confirm_fx import FIXTURES, audited, candidates
from _support.flows import base_env
from _support.htmltree import parse
from klode_kit.audit import confirm_page as CP
from klode_kit.contract import schema

pytestmark = [pytest.mark.cli_integration, pytest.mark.needs_node]

U8 = FIXTURES / "fx-u8"
ROUTES = ("vision", "mlx", "claude", "codex")
STAMP = re.compile(rb'(<meta name="klode-kit-confirm-sha256" content=")([0-9a-f]{64})(")')


def node() -> str:
    exe = shutil.which("node")
    assert exe is not None, "node is required by the cli_integration lane (needs_node): install node"
    return exe


def export(html: Path, selections: list[dict] | None, tmp: Path) -> subprocess.CompletedProcess:
    argv = [node(), str(CP.EXPORT_JS), str(html)]
    if selections is not None:
        sel = tmp / "selections.json"
        sel.write_text(json.dumps(selections, ensure_ascii=False), encoding="utf-8")
        argv.append(str(sel))
    return subprocess.run(argv, capture_output=True, timeout=60)


def confirm_page(project: Path, batch: str) -> subprocess.CompletedProcess:
    argv = [sys.executable, "-m", "klode_kit", "confirm-page", "--batch", batch, "--project", str(project)]
    return subprocess.run(argv, capture_output=True, timeout=120, env=base_env({}), cwd=str(project))


def written_u8(tmp_path: Path) -> tuple[str, Path, Path, list[tuple[dict, dict]]]:
    project = tmp_path / "proj"
    project.mkdir()
    batch, adir = audited(project, U8)
    res = confirm_page(project, batch)
    assert res.returncode == 0, res.stderr.decode()
    html = adir / "confirm.html"
    assert str(html) in res.stdout.decode()
    fields = [(c, f) for c in candidates(adir) for f in c["fields"] if f["verdict"] != "agree"]
    return batch, adir, html, fields


def test_node_available():
    """The needs_node subset FAILS without node (plan: missing dependencies fail, never skip)."""
    out = subprocess.run([node(), "--version"], capture_output=True, timeout=30)
    assert out.returncode == 0 and out.stdout.startswith(b"v")


def test_export_js_node(tmp_path):
    """08b-06 (S1): fx-u8 with simulated clicks (one route pick, one typed value, one 'none of these', one 'not a
    field'); node prints a choices.json that passes the schema, whose confirm_sha256 is the page's sealed sha
    (sha256 of the file with its stamp zeroed) and whose fingerprints are every run of the revision."""
    batch, adir, html, fields = written_u8(tmp_path)
    picks = [{"field_id": fields[0][1]["field_id"], "kind": "route", "route": "vision"},
             {"field_id": fields[1][1]["field_id"], "kind": "manual", "value": "210"},
             {"field_id": fields[2][1]["field_id"], "kind": "none"},
             {"field_id": fields[3][1]["field_id"], "kind": "none", "reason": "not_a_field"}]
    res = export(html, picks, tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    choices = json.loads(res.stdout)
    assert schema.issues("choices", choices) == []
    data = html.read_bytes()
    m = STAMP.search(data)
    assert m is not None
    sealed = hashlib.sha256(data[:m.start(2)] + b"0" * 64 + data[m.end(2):]).hexdigest()
    assert choices["confirm_sha256"] == sealed == m.group(2).decode()
    runs = json.loads((adir / "runs.json").read_text("utf-8"))
    assert choices["fingerprints"] == {e: r for e, r in runs.items() if r}
    assert set(choices["fingerprints"]) == set(ROUTES)
    assert (choices["batch_id"], choices["audit_rev"]) == (batch, f"{batch}/a1")
    assert [i["field_id"] for i in choices["items"]] == [f["field_id"] for _, f in fields]
    assert [i["choice"] for i in choices["items"]] == [{"kind": "route", "route": "vision"},
                                                       {"kind": "manual", "value": "210"}, {"kind": "none"},
                                                       {"kind": "none"}]
    assert choices["items"][3]["evidence"] == {"reason": "not_a_field"}
    assert choices == CP.export_choices(data, picks)


def test_embedded_script_is_the_template(tmp_path):
    """The script the browser runs is byte for byte the exporter node runs (so 08b-06 covers the page's export
    logic)."""
    _, _, html, _ = written_u8(tmp_path)
    doc = parse(html.read_bytes())
    (script,) = [s for s in doc.find_all("script") if s.get("id") == "klode-kit-confirm-export"]
    assert script.text().strip() == CP.EXPORT_JS.read_text(encoding="utf-8").strip()


def test_node_no_clicks_exports_empty(tmp_path):
    """Boundary (S2): without selections node exports no items (and nothing from the page's hints)."""
    _, _, html, _ = written_u8(tmp_path)
    res = export(html, None, tmp_path)
    assert res.returncode == 0, res.stderr.decode()
    assert json.loads(res.stdout)["items"] == []


@pytest.mark.parametrize("case", ["edited", "unknown_field", "bad_route", "partial_kind", "usage"])
def test_node_refusals(tmp_path, case):
    """Guards (S3): an edited page (stamp no longer matches) exits 30; a selection the page cannot produce
    exits 2; a missing argument exits 2. Nothing is printed on stdout."""
    _, _, html, fields = written_u8(tmp_path)
    fid = fields[0][1]["field_id"]
    if case == "edited":
        copy = tmp_path / "edited.html"
        copy.write_bytes(html.read_bytes().replace("都不对".encode(), "都不队".encode(), 1))
        res, want = export(copy, [], tmp_path), 30
    elif case == "unknown_field":
        res, want = export(html, [{"field_id": "r-000000000000/r001-number-0", "kind": "none"}], tmp_path), 2
    elif case == "bad_route":
        res, want = export(html, [{"field_id": fid, "kind": "route", "route": "tesseract"}], tmp_path), 2
    elif case == "partial_kind":
        res, want = export(html, [{"field_id": fid, "kind": "partial"}], tmp_path), 2
    else:
        res, want = subprocess.run([node(), str(CP.EXPORT_JS)], capture_output=True, timeout=60), 2
    assert res.returncode == want, res.stderr.decode()
    assert res.stdout == b""
    assert res.stderr


SEL = st.fixed_dictionaries({
    "slot": st.integers(0, 3),
    "kind": st.sampled_from(["route", "manual", "none"]),
    "route": st.sampled_from(ROUTES),
    "value": st.text(max_size=6),
    "reason": st.sampled_from([None, "not_a_field"]),
})
_PAGE: dict = {}


@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(clicks=st.lists(SEL, max_size=8))
def test_node_matches_python_exporter(tmp_path_factory, clicks):
    """Parity (S6, metamorphic): for any click sequence node and the Python exporter produce the same
    choices.json (the unit lane's export cases therefore hold for the page's own script)."""
    if "u8" not in _PAGE:
        _PAGE["u8"] = written_u8(tmp_path_factory.mktemp("u8"))
    _, _, html, fields = _PAGE["u8"]
    sels = []
    for c in clicks:
        s = {"field_id": fields[c["slot"]][1]["field_id"], "kind": c["kind"]}
        if c["kind"] == "route":
            s["route"] = c["route"]
        elif c["kind"] == "manual":
            s["value"] = c["value"]
        elif c["reason"]:
            s["reason"] = c["reason"]
        sels.append(s)
    res = export(html, sels, tmp_path_factory.mktemp("sel"))
    assert res.returncode == 0, res.stderr.decode()
    assert json.loads(res.stdout) == CP.export_choices(html.read_bytes(), sels)
