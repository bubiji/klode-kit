"""WI-00c3 matrix 00c3-09 (gate G2): the four routes on the hardest synthetic pages, recorded for replay.

Pages (all registered in ``tests/fixtures/MANIFEST.json``; synthetic, whitelisted names only):
``fx-u1-3labs`` p001-p003, ``fx-rx-stamp`` (red stamp over a cell), ``fx-lowres`` (0.4x), ``fx-vertical``
(a vertical text column) and ``fx-rotated90`` (its upright derived image, ``derived/p001.upright.png``).
The images are sent unmasked: the masker (WI-05a) does not exist yet and only MANIFEST-registered images may be
sent; their header holds the whitelisted placeholder name and number only.

Routes: A ``vision`` (ocrmac / Apple Vision), B ``mlx`` (Unlimited-OCR via mlx-vlm, local cache, tesseract
fallback), C ``claude`` and D ``codex`` - both through ``python -m klode_kit.eval.spike4``: production argv
(``cloud_argv``), ``isolation_check`` PASS and an isolation receipt bound to the CLI version before every call,
claude with ``--max-budget-usd 5``. WI budget: claude <= $8 (ledger ``work/isolation/ledger.jsonl``,
``wi: WI-00c3``), codex <= 20 calls; the test stops (FAIL) before a call that could exceed either.

Every raw output is written to ``tests/recordings/<engine>/<der_sha256>.json`` (``recorded: true``; kept outside
the generated ``tests/fixtures`` tree, own ``MANIFEST.json``); an
existing recording with the same parameters is reused, so a rerun costs nothing (``KLODE_KIT_SPIKE_REDO=1`` forces
new calls). ``KLODE_KIT_SPIKE_PAGES=fx-rx-stamp/p001,...`` limits the pages (probe runs).

The metrics (``klode_kit.eval.eval_fields``, 02 r2.7), the CER per route (``klode_kit.cer``) and the R3-05
granularity analysis go to ``work/spike/WI-00c3/<run>/report.json`` and ``work/spike/WI-00c3/latest.json``.

Run: ``KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> .venv/bin/python -m pytest -m model_integration
tests/model/test_hardest_pages_4routes.py``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from _support import spike_engines as SE
from _support.flows import REPO
from klode_kit import cer as CER
from klode_kit.eval import eval_fields as E

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

WI = "WI-00c3"
RUN_ID = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
FIXTURES = REPO / "tests" / "fixtures"
REPLAY = REPO / "tests" / "recordings"
WORK = REPO / "work" / "spike" / WI
TRASH = REPO / "_trash" / f"{WI.lower()}-{RUN_ID}"
LEDGER = REPO / "work" / "isolation" / "ledger.jsonl"
RECEIPTS = REPO / "docs" / "spikes" / "receipts"
RECEIPT = {"claude": RECEIPTS / "claude-isolation_receipt-20261022T224341Z.json",
           "codex": RECEIPTS / "codex-isolation_receipt-20261022T234046Z.json"}
DOC = REPO / "docs" / "spikes" / "WI-00c-4routes.md"
ROUTES = ("vision", "mlx", "claude", "codex")
# The 7-page set was recorded with codex at xhigh, before (5') locked `max` (defaults.toml since WI-00c3); it is
# reused as recorded. `max` itself is confirmed on the hardest page by test_codex_max_effort_live.
CLOUD = {"claude": ("opus", "max"), "codex": ("gpt-6-sol", "xhigh")}
CODEX_MAX = ("gpt-6-sol", "max")
HARDEST = ("fx-rx-stamp", "p001", "images/p001.png")
CLAUDE_BUDGET_USD, CLAUDE_CALL_BUDGET, CODEX_MAX_CALLS = 8.0, 5.0, 20
MAX_ATTEMPTS = 2

PAGES = [
    ("fx-u1-3labs", "p001", "images/p001.png"),
    ("fx-u1-3labs", "p002", "images/p002.png"),
    ("fx-u1-3labs", "p003", "images/p003.png"),
    ("fx-rx-stamp", "p001", "images/p001.png"),
    ("fx-lowres", "p001", "images/p001.png"),
    ("fx-vertical", "p001", "images/p001.png"),
    ("fx-rotated90", "p001", "derived/p001.upright.png"),
]


def _require_real() -> None:
    if os.environ.get("KLODE_KIT_REAL") != "1":
        pytest.fail("model_integration needs KLODE_KIT_REAL=1 (missing dependency is a FAIL, never a skip)")
    for tool in ("claude", "codex", "tesseract"):
        if shutil.which(tool, path=_real_env()["PATH"]) is None:
            pytest.fail(f"{tool} not on PATH")


def _real_env() -> dict[str, str]:
    env = dict(os.environ)
    fakes = str(REPO / "tests" / "fakes" / "bin")
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and p != fakes)
    env["KLODE_KIT_REAL"] = "1"
    return env


def _ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


def _book(route: str, page: str, rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    row = {"run": RUN_ID, "wi": WI, "label": "ocr", "route": route, "page": page, "cost_usd": rec.get("cost_usd"),
           "model_actual": rec.get("model_actual"), "tokens": rec.get("tokens"), "exit_code": rec.get("exit_code")}
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _budget_gate(route: str) -> None:
    rows = [r for r in _ledger() if r.get("wi") == WI and r.get("route") == route and r.get("label") == "ocr"]
    if route == "claude":
        spent = sum(float(r.get("cost_usd") or 0.0) for r in rows)
        reserve = max([0.5, *(float(r.get("cost_usd") or 0.0) for r in rows)])
        if spent + reserve > CLAUDE_BUDGET_USD:
            pytest.fail(f"{WI}: claude spent ${spent:.2f}; next call could pass ${CLAUDE_BUDGET_USD} - stop (G2)")
    elif len(rows) >= CODEX_MAX_CALLS:
        pytest.fail(f"{WI}: {len(rows)} codex calls booked (cap {CODEX_MAX_CALLS}) - stop (G2)")


def _pages() -> list[tuple[str, str, str]]:
    sel = os.environ.get("KLODE_KIT_SPIKE_PAGES")
    if not sel:
        return PAGES
    want = {s.strip() for s in sel.split(",") if s.strip()}
    return [p for p in PAGES if f"{p[0]}/{p[1]}" in want]


def _reusable(path: Path, **params) -> dict | None:
    if os.environ.get("KLODE_KIT_SPIKE_REDO") == "1" or not path.exists():
        return None
    rec = json.loads(path.read_text(encoding="utf-8"))
    if rec.get("recorded") is not True or any(rec.get(k) != v for k, v in params.items()):
        return None
    if rec.get("lines") is None:
        return None
    return rec


def _cloud(route: str, png: Path, sha: str, label: str, project: Path, *, model_effort: tuple[str, str] | None = None,
           out: Path | None = None) -> dict:
    out = out or REPLAY / route / f"{sha}.json"
    model, effort = model_effort or CLOUD[route]
    rec = _reusable(out, model=model, effort=effort)
    if rec is not None:
        return rec
    for _ in range(MAX_ATTEMPTS):
        _budget_gate(route)
        args = [sys.executable, "-m", "klode_kit.eval.spike4", "--route", route, "--png", str(png),
                "--png-label", label, "--project", str(project), "--receipt", str(RECEIPT[route]),
                "--model", model, "--effort", effort, "--out", str(out), "--trash", str(TRASH / route),
                "--timeout-s", "900"]
        if route == "claude":
            args += ["--max-budget-usd", str(CLAUDE_CALL_BUDGET)]
        proc = subprocess.run(args, env=_real_env(), capture_output=True, text=True, timeout=1200)
        if proc.returncode == 61:
            pytest.fail(f"{route}: cloud_isolation FAIL, nothing sent - {proc.stderr.strip()}")
        rec = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
        _book(route, label, rec)
        if proc.returncode == 0:
            return rec
    pytest.fail(f"{route} {label}: no structured answer after {MAX_ATTEMPTS} attempts (recording kept: {out})")


class _Local:
    def __init__(self):
        self._mlx = None

    def mlx(self):
        if self._mlx is None:
            self._mlx = SE.Mlx()
        return self._mlx

    def vision(self, png: Path, sha: str, label: str) -> dict:
        out = REPLAY / "vision" / f"{sha}.json"
        rec = _reusable(out)
        if rec is None:
            obs, meta = SE.vision_observations(png)
            SE.write_recording(out, {"engine": "vision", "der_sha256": sha, "image": label, "at": SE.now(),
                                     "params": meta, "raw": obs, "lines": SE.group_lines(obs)})
            rec = json.loads(out.read_text(encoding="utf-8"))
        return rec

    def tesseract(self, png: Path, sha: str, label: str) -> dict:
        out = REPLAY / "tesseract" / f"{sha}.json"
        rec = _reusable(out)
        if rec is None:
            tsv, lines, words, meta = SE.tesseract(png)
            SE.write_recording(out, {"engine": "tesseract", "der_sha256": sha, "image": label, "at": SE.now(),
                                     "params": meta, "raw": tsv, "words": words, "lines": lines})
            rec = json.loads(out.read_text(encoding="utf-8"))
        return rec

    def mlx_route(self, png: Path, sha: str, label: str, tess: dict) -> dict:
        out = REPLAY / "mlx" / f"{sha}.json"
        rec = _reusable(out)
        if rec is None:
            engine = self.mlx()
            try:
                raw, lines, meta = engine.ocr(png)
                error = None
            except Exception as exc:  # recorded, then the fallback decides
                raw, lines, meta, error = "", [], {}, f"{type(exc).__name__}: {exc}"
            fallback = None
            if error or meta.get("degenerate") or not lines:
                fallback, lines = "tesseract", list(tess["lines"])
            SE.write_recording(out, {"engine": "mlx", "der_sha256": sha, "image": label, "at": SE.now(),
                                     "params": engine.params(), "meta": meta, "error": error, "raw": raw,
                                     "fallback": fallback, "lines": lines})
            rec = json.loads(out.read_text(encoding="utf-8"))
        elif not rec.get("fallback") and rec["lines"] != SE.mlx_lines(rec["raw"]):
            # lines are a pure function of the recorded raw output: re-derive after an adapter fix
            rec = {k: v for k, v in rec.items() if k not in ("schema", "recorded")}
            rec.update(lines=SE.mlx_lines(rec["raw"]), lines_rederived_at=SE.now())
            SE.write_recording(out, rec)
            rec = json.loads(out.read_text(encoding="utf-8"))
        return rec


def _keyed_truth(fx: str, key: str) -> dict:
    truth = E.truth_rows(json.loads((FIXTURES / fx / "truth.json").read_text(encoding="utf-8")))
    return {f"{fx}/{rid}": {**r, "key": f"{fx}/{r['key']}"} for rid, r in truth.items() if r["key"] == key}


@pytest.fixture(scope="module")
def spike(tmp_path_factory):
    _require_real()
    project = tmp_path_factory.mktemp("spike4") / "synth_project"
    shutil.copytree(FIXTURES / "synth_project", project)
    local = _Local()
    truth: dict = {}
    preds: dict = {r: {} for r in ROUTES}
    pages, recordings = [], []
    cer_sum = {r: [0, 0] for r in ROUTES}
    geo_pages = []
    for fx, key, rel in _pages():
        png = FIXTURES / fx / rel
        sha = SE.sha256_file(png)
        label = f"{fx}/{rel}"
        recs = {"vision": local.vision(png, sha, label)}
        tess = local.tesseract(png, sha, label)
        recs["mlx"] = local.mlx_route(png, sha, label, tess)
        for route in ("claude", "codex"):
            recs[route] = _cloud(route, png, sha, label, project)
        recordings += [REPLAY / e / f"{sha}.json" for e in (*ROUTES, "tesseract")]

        page_truth = _keyed_truth(fx, key)
        truth.update(page_truth)
        gold = SE.gold_lines(FIXTURES / fx, key)
        page = {"page": label, "der_sha256": sha, "cer": {}, "mlx_fallback": recs["mlx"].get("fallback")}
        for route in ROUTES:
            lines = recs[route]["lines"] or []
            preds[route].update(E.parse_rows(lines, page_truth))
            c = CER.cer("\n".join(gold), "\n".join(lines))
            page["cer"][route] = round(c["cer"], 4)
            cer_sum[route][0] += c["distance"]
            cer_sum[route][1] += c["gold_chars"]
        c = CER.cer("\n".join(gold), "\n".join(tess["lines"]))
        page["cer"]["tesseract"] = round(c["cer"], 4)

        tj = json.loads((FIXTURES / fx / "truth.json").read_text(encoding="utf-8"))
        fields = [{"field_id": f["field_id"], **f["routes"]["vision"]} for f in tj["fields"]
                  if f["key"] == key and f["routes"].get("vision")]
        geo = SE.granularity(recs["vision"]["raw"], SE.segment_boxes(FIXTURES / fx, key), fields,
                             tess.get("words") or [])
        geo_pages.append({"page": label, **{k: v for k, v in geo.items() if k not in ("kinds",)}})
        pages.append(page)

    report = E.evaluate(truth, preds)
    totals = {k: sum(g[k] for g in geo_pages) for k in ("observations", "exact", "multi", "other", "cells_total",
                                                         "cells_exact", "cells_approx", "cells_none", "cells_manual")}
    ledger = [r for r in _ledger() if r.get("wi") == WI and r.get("label") == "ocr"]
    out = {
        "run": RUN_ID, "wi": WI, "pages": pages, "metrics": report,
        "cer_overall": {r: round(d / g, 4) if g else None for r, (d, g) in cer_sum.items()},
        "r3_05": {"pages": geo_pages, "totals": totals},
        "cloud": {"claude": {"model": CLOUD["claude"][0], "effort": CLOUD["claude"][1],
                             "calls": sum(1 for r in ledger if r["route"] == "claude"),
                             "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in ledger
                                                   if r["route"] == "claude"), 4)},
                  "codex": {"model": CLOUD["codex"][0], "effort": CLOUD["codex"][1],
                            "calls": sum(1 for r in ledger if r["route"] == "codex")}},
        "recordings": sorted(str(p.relative_to(REPO)) for p in recordings),
    }
    WORK.joinpath(RUN_ID).mkdir(parents=True, exist_ok=True)
    blob = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
    (WORK / RUN_ID / "report.json").write_text(blob, encoding="utf-8")
    (WORK / "latest.json").write_text(blob, encoding="utf-8")
    return out


def test_hardest_pages_4routes_live(spike):
    """00c3-09 (L3/S1): recordings in replay, false auto pass == 0, four-way agreement >= 20 fields."""
    for rel in spike["recordings"]:
        rec = json.loads((REPO / rel).read_text(encoding="utf-8"))
        assert rec["recorded"] is True and rec["lines"] is not None, rel
    m = spike["metrics"]
    assert m["auto_verified_total"] > 0, "no auto-verified samples"
    assert m["false_auto_pass_rate"] == 0, f"false auto pass {m['false_auto_pass']}/{m['auto_verified_total']} - STOP, replan"
    assert m["all_agree_fields"] >= E.MIN_ALL_AGREE


def test_spike_doc_records_r305_and_codex_effort(spike):
    """00c3-09: the spike document states the R3-05 granularity verdict and the (5') effort value."""
    text = DOC.read_text(encoding="utf-8")
    assert "R3-05" in text and "⑤′" in text
    assert spike["run"] in text or "latest.json" in text
    for marker in ("geometry: approx", "model_reasoning_effort"):
        assert marker in text


def test_codex_max_effort_live(tmp_path):
    """(5'): the production codex invocation at `model_reasoning_effort=max` passes the gate and answers (one call on
    the hardest page; reused when the recording exists)."""
    _require_real()
    project = tmp_path / "synth_project"
    shutil.copytree(FIXTURES / "synth_project", project)
    fx, key, rel = HARDEST
    png = FIXTURES / fx / rel
    sha = SE.sha256_file(png)
    out = REPLAY / "codex" / f"{sha}.effort-max.json"
    rec = _cloud("codex", png, sha, f"{fx}/{rel}", project, model_effort=CODEX_MAX, out=out)
    assert rec["recorded"] is True and rec["exit_code"] == 0
    assert "model_reasoning_effort=max" in rec["argv"]
    assert rec["lines"]
    truth = _keyed_truth(fx, key)
    m = E.field_metrics(truth, E.parse_rows(rec["lines"], truth))
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "codex-max.json").write_text(json.dumps({"page": f"{fx}/{rel}", "metrics": m, "tokens": rec.get("tokens"),
                                                     "recording": str(out.relative_to(REPO))}, indent=1) + "\n")
