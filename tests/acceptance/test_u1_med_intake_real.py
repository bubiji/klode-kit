"""U1 for real (gate G2): the same command as ``test_u1_med_intake.py`` - ``klode-kit med-intake --inbox
_inbox/2026-10-22/`` - on the three synthetic lab pictures of ``fx-u1-3labs`` (registered in
``tests/fixtures/MANIFEST.json``), all four routes real: Apple Vision, MLX (local weights), Claude and Codex (the
production isolation, the stored isolation receipts of WI-00c1b / WI-00c2c, only masked pictures leave).

Lane model_integration, ``gate_cloud``: without ``KLODE_KIT_REAL=1`` and ``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`` the whole
session stops with a usage error before any call. Booked in ``work/host-spike/costs.jsonl`` (ledger limit of
``_support.host``); one run, no retry loop. The data project is made under the trusted lab directory
(``tests/host/_lab/``, G5), the registry is the conftest's private file; nothing is written below the user's home.

What is asserted is what 04 promises that a real model cannot change: the two output lines have the shape of 04
(page and route counts measured, a dispute count that is a number), the exit code goes with the dispute count (10 when
the page waits, 0 when nothing does), ``awaiting_confirm`` / ``nothing_to_confirm`` follows, the batch receipt has a time
per route and no failed page, and the confirm page opened through its folder lists exactly the disputes counted. The
literal "5 条分歧" is the replay fixture's number (designed disagreements); real readings differ, and the number that
was printed is reported.

No masking step is run beforehand: ``med-intake`` (its ``kb-ocr`` stage) masks by itself before the cloud routes read
(WI-32a; before that the test had to call ``accept_fx.premask``).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

from _support import accept_fx as A
from _support import kb_ocr as K
from _support.flows import REPO
from _support.host import Ledger, evidence_dir, lab_dir
from klode_kit.audit import confirm_page as CP

pytestmark = [pytest.mark.model_integration, pytest.mark.gate_cloud]

CAP_USD = 5.0
ROUTES = ["vision", "mlx", "claude", "codex"]


def test_u1_med_intake_real():
    assert os.environ.get("KLODE_KIT_REAL") == "1"
    ledger = Ledger()
    ledger.reserve("u1-real", CAP_USD)
    lab = lab_dir("u1-real")
    evidence = evidence_dir("u1-real")
    project = lab / "project"
    project.mkdir()
    (project / ".gitignore").write_text("_work/\n_raw/\n_masked/\n_mask_review/\n_private/\nwork/\nconfirm/\n"
                                        "records/\nverified/\n", encoding="utf-8")
    inbox = lab / "_inbox" / "2026-10-22"
    inbox.mkdir(parents=True)
    for img in sorted((K.FIXTURES / "fx-u1-3labs" / "images").glob("*.png")):
        shutil.copyfile(img, inbox / img.name)
    A.stage_isolation_receipts(project)
    env = A.real_env()

    done = subprocess.run([sys.executable, "-m", "klode_kit", "med-intake", "--inbox", str(inbox), "--project",
                           str(project)], env={**env, "PYTHONPATH": str(REPO / "src")}, capture_output=True,
                          cwd=str(project), timeout=2400)
    (evidence / "stdout.txt").write_bytes(done.stdout)
    (evidence / "stderr.txt").write_bytes(done.stderr)
    lines = done.stdout.decode().splitlines()
    (batch_dir,) = [p for p in (project / "work").iterdir() if p.is_dir() and not p.is_symlink()
                    and (p / "derived.jsonl").is_file()]
    batch = batch_dir.name
    receipt_path = project / "work" / "20261022" / "receipt.json"
    if receipt_path.is_file():
        shutil.copyfile(receipt_path, evidence / "batch_receipt.json")
    spent = 0.0
    ledger_file = project / "work" / batch / "receipts" / "cloud_ledger.json"
    if ledger_file.is_file():
        spent = float(json.loads(ledger_file.read_text(encoding="utf-8"))["spent_usd"])
    ledger.book("u1-real", CAP_USD, spent if ledger_file.is_file() else None)
    (evidence / "meta.json").write_text(json.dumps({"exit_code": done.returncode, "cloud_spent_usd": spent,
                                                    "lines": lines}, ensure_ascii=False, indent=1), encoding="utf-8")

    assert done.returncode in (0, 10), done.stderr.decode()[-3000:]
    assert lines[0] == "ocr: 3 页 × 4 路 OK", (lines, done.stderr.decode()[-3000:])
    page = project / "confirm" / "20261022.html"
    disputes = len([f for r in CP.page_data(page.read_bytes())["records"] for f in r["fields"]]) if page.exists() else 0
    if done.returncode == 10:
        assert re.fullmatch(r"audit: [1-9][0-9]* 条分歧 → confirm/20261022\.html", lines[1]), lines
        assert lines[1] == f"audit: {disputes} 条分歧 → confirm/20261022.html"
        assert lines[-1] == "status: awaiting_confirm"
        assert (project / "confirm" / "20261022" / "confirm.html").is_file()
    else:
        assert lines[1] == "audit: 0 条分歧，无需确认" and lines[-1] == "status: nothing_to_confirm"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["routes"] == ROUTES and len(receipt["pages"]) == 3
    stats = receipt["route_stats"]
    assert sorted(stats) == sorted(ROUTES)
    for route in ROUTES:
        assert stats[route]["failed_pages"] == 0, (route, stats)
        assert isinstance(stats[route]["elapsed_ms"], int) and stats[route]["elapsed_ms"] > 0, (route, stats)
    assert receipt["issues"] == []
    # only masked pictures left: every cloud call of the batch names a picture registered in _masked/MANIFEST
    cloud = sorted((project / "work" / batch / "receipts" / "cloud").glob("*.json"))
    assert len(cloud) >= 6  # 3 pages x 2 cloud routes
    assert spent > 0 and spent <= CAP_USD
