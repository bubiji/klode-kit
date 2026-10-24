"""Same gold questions over two or more klode libraries: ``search --full`` hit rates (WI-02c; vendored).

Vendored from the user's klode-build skill ``scripts/eval_compare.py`` (read-only source, 2026-10-24). It answers
questions like "do cards written by different models change retrieval?".

    klode-kit eval-compare --gold G.jsonl --kb NAME=library.toml [--kb NAME=TOML ...] [--k 5]
                           [--desk-gold D.jsonl] [--out summary.md]
    exit 0 report written; 2 usage; 40 no klode on PATH

``--gold`` lines: ``{"q": query, "gold": ["card-id", ...], "kind": scenario}``. Card-level Hit@1, Hit@k and MRR
per library, one table per kind, one rank table per question (``—`` = not in the top k). Retrieval is
``klode --json -c <toml> search --full <q words>`` - the code path of MCP ``search_sources(full=true)``.
``--desk-gold`` lines: ``{"q": colloquial question, "gold": dimension}``, routed with ``klode diagnose``.

Kept: every table, metric and format of the original; an unparseable search answer counts as a miss.
Changed: **B3** - desk routing is read from ``klode --json diagnose`` (``desk_build.diagnose_top``) instead of
scraping hyphenated words from the prose output, so a dimension without a hyphen (``safety``) is scored right;
``klode`` is found on PATH (the original hard-coded a home-directory path) and missing is exit 40.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

from klode_kit.build.desk_build import DeskError, diagnose_top

EXIT_ENV = 40


def search(klode: str, toml: str, q: str, k: int) -> tuple[list[str], float]:
    t = time.perf_counter()
    r = subprocess.run([klode, "--json", "-c", toml, "search", "--full", *q.split()],
                       capture_output=True, text=True, timeout=120)
    dt = (time.perf_counter() - t) * 1000
    try:
        hits = json.loads(r.stdout)["value"]["hits"]
        ids = [h["id"] for h in hits]
    except (json.JSONDecodeError, KeyError, TypeError):
        return [], dt
    return ids[:k], dt


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def report(klode: str, kbs: dict[str, str], gold: list[dict], k: int, desk_gold: list[dict]) -> str:
    rows = []   # (kb, kind, q, rank or None, ms, top3)
    for name, toml in kbs.items():
        for g in gold:
            ids, ms = search(klode, toml, g["q"], k)
            rank = next((i + 1 for i, cid in enumerate(ids) if cid in g["gold"]), None)
            rows.append((name, g.get("kind", "-"), g["q"], rank, ms, ids[:3]))

    def agg(sub):
        n = len(sub)
        return (sum(1 for r in sub if r[3] == 1) / n, sum(1 for r in sub if r[3]) / n,
                sum(1 / r[3] for r in sub if r[3]) / n, sorted(r[4] for r in sub)[n // 2])

    out = [f"# 写卡模型对检索的影响 —— {len(gold)} 题，k={k}", "",
           "| 库 | Hit@1 | Hit@k | MRR | 延迟中位 ms |", "|---|---:|---:|---:|---:|"]
    for name in kbs:
        h1, hk, mrr, med = agg([r for r in rows if r[0] == name])
        out.append(f"| {name} | {h1:.2f} | {hk:.2f} | {mrr:.2f} | {med:.0f} |")
    out += ["", "## 按场景（Hit@k / MRR）", "", "| 场景 | n | " + " | ".join(kbs) + " |",
            "|---|---:|" + "---|" * len(kbs)]
    kinds = sorted({r[1] for r in rows}, key=lambda kd: [g.get("kind", "-") for g in gold].index(kd))
    for kind in kinds:
        cells = []
        for name in kbs:
            sub = [r for r in rows if r[0] == name and r[1] == kind]
            _h1, hk, mrr, _ = agg(sub)
            cells.append(f"{hk:.2f} / {mrr:.2f}")
        out.append(f"| {kind} | {len([g for g in gold if g.get('kind', '-') == kind])} | " + " | ".join(cells) + " |")
    out += ["", "## 逐题（rank；— = 未进前 k）", "", "| 题 | " + " | ".join(kbs) + " |", "|---|" + "---|" * len(kbs)]
    byq: dict = defaultdict(dict)
    for r in rows:
        byq[r[2]][r[0]] = r[3]
    for g in gold:
        out.append(f"| {g['q']} | " + " | ".join(str(byq[g['q']].get(n) or "—") for n in kbs) + " |")
    if desk_gold:
        out += ["", f"## 书桌路由（klode --json diagnose，{len(desk_gold)} 题）", "", "| 库 | 路由正确 | 准确率 |",
                "|---|---:|---:|"]
        detail: dict = defaultdict(dict)
        for name, toml in kbs.items():
            ok = 0
            for g in desk_gold:
                top = diagnose_top(klode, Path(toml), g["q"]) or "∅"
                detail[g["q"]][name] = top
                ok += top == g["gold"]
            out.append(f"| {name} | {ok}/{len(desk_gold)} | {ok / len(desk_gold):.2f} |")
        out += ["", "| 题 | 期望 | " + " | ".join(kbs) + " |", "|---|---|" + "---|" * len(kbs)]
        for g in desk_gold:
            out.append(f"| {g['q']} | {g['gold']} | " + " | ".join(
                ("✅ " if detail[g['q']][n] == g['gold'] else "❌ ") + detail[g['q']][n] for n in kbs) + " |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit eval-compare", description=__doc__.splitlines()[0])
    ap.add_argument("--gold", required=True, type=Path)
    ap.add_argument("--kb", action="append", required=True, help="NAME=library.toml path, repeatable")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--desk-gold", type=Path, help='desk routing gold: one {"q": question, "gold": dimension} per line')
    ap.add_argument("--out", type=Path)
    try:
        a = ap.parse_args(argv)
        if any("=" not in x for x in a.kb):
            ap.error("--kb 须是 NAME=library.toml")
    except SystemExit as exc:
        return 2 if exc.code else 0
    klode = shutil.which("klode")
    if not klode:
        print("eval-compare: 找不到 klode：需要 klode 0.5.x 在 PATH 上", file=sys.stderr)
        return EXIT_ENV
    kbs = dict(x.split("=", 1) for x in a.kb)
    gold = _lines(a.gold)
    desk_gold = _lines(a.desk_gold) if a.desk_gold else []
    try:
        text = report(klode, kbs, gold, a.k, desk_gold)
    except DeskError as exc:
        print(f"eval-compare: {exc}", file=sys.stderr)
        return exc.code
    print(text)
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text, encoding="utf-8")
        print(f"写出 {a.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit eval-compare``
    raise SystemExit(main())
