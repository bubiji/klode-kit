"""Desk synthesis tasks of a book build (WI-13a; closes the design gap of impl-deviations "WI-09b": nobody owned the
desk workers' syntheses of the book path).

    klode-kit desk-tasks --build BUILD_DIR --out SYN_DIR [--project DIR]
    exit 0 planned; 2 usage (no such build / project, build outside the project, --out inside the build);
    30 contract (no or bad ``src/desk.json`` in the build)

The book path of ``klode-kit build`` takes the desk syntheses as an input (``--syntheses DIR``, ``<dimension>.md``): a
model writes each one from the cards of the build. The first run without them fails ``desk_apply`` (exit 20) naming the
dimensions; the finished build is sealed read-only but holds everything a worker needs - the cards. This command turns
that build into work for the /kb-build skill to dispatch:

- ``SYN_DIR/_tasks/<dimension>.txt`` - the worker prompt, one for every dimension of the build's ``src/desk.json`` that
  has no ``SYN_DIR/<dimension>.md`` yet: the rules file (``prompts/synthesis.md``), the absolute paths of the cards to
  cite, the one file to write, and the gate to run (``check-synthesis BUILD --syntheses SYN_DIR <dimension>``) until
  PASS. The build is only ever read; the worker writes outside it.
- ``SYN_DIR/repair.json`` - a ``klode-kit.repair/1`` document (``from_build`` relative to the project, nothing to
  redispatch, nothing fixed): ``build --repair SYN_DIR/repair.json --syntheses SYN_DIR`` carries the build's cards over
  byte for byte, so the anchors the workers copied are still in the cards of the next build (a model run for the cards
  would write them differently).
- stdout - one JSON document ``klode-kit.desk_tasks/1``: ``build`` (project-relative), ``syntheses``, ``repair``,
  ``present`` (the dimensions that already have a file, spec order) and ``tasks`` (``dimension``, ``title``, ``cards``,
  ``prompt_file``, ``output``; spec order).

Nothing is invented and nothing is called: no model, no ``klode``. Running it again prints the same document and
rewrites nothing (a file is only written when its text changed). Whether a worker is *done* is the worker's
handback, never the file's existence (iron rule 1).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from klode_kit.build import desk_build
from klode_kit.build.run_build import REPAIR_SCHEMA
from klode_kit.pipeline import default_launcher

SCHEMA = "klode-kit.desk_tasks/1"
EXIT_USAGE = 2


def _usage(message: str) -> int:
    print(f"desk-tasks: {message}", file=sys.stderr)
    return EXIT_USAGE


def prompt(build: Path, out: Path, dim: dict[str, Any], launcher: Path) -> str:
    """The worker prompt of one dimension (absolute paths; the build is named only as the place of the cards)."""
    cards = "\n".join(f"- {build / 'library' / 'cards' / (cid + '.md')}" for cid in dim["cards"])
    return (
        f"任务类型变更：这次写的是**书桌维度 synthesis**，不是卡片。dimension={dim['dimension']}\n"
        f"规范：Read {desk_build.SYNTHESIS_PROMPT}（格式与规则，照做）。\n"
        f"本维度标题：{dim['title']}；回答：「{dim['question']}」。\n"
        f"cards:（只读下面这些卡片，锚点只能从里面逐字复制；不读源 .txt）\n{cards}\n"
        f"只写一个文件：{out / (dim['dimension'] + '.md')}（文件不存在时，Edit 的 old_string 留空来新建）。"
        f"{build} 下的一切都是已封存的只读构建，只读、不写；不改卡片；不碰 git。\n"
        f"写完跑 {launcher} check-synthesis {build} --syntheses {out} {dim['dimension']} 改到 PASS，"
        f"然后回报一行：`<dimension>  PASS  锚点 N  动作 M 条`。\n"
        f"跨书时如实并列各书说法，不调和。你交回（handback）之前，没有人会碰这个文件。\n"
    )


def plan(project: Path, build: Path, out: Path) -> dict[str, Any]:
    """Write the prompts and ``repair.json`` and return the printed document (everything already validated)."""
    spec = desk_build.load(build)
    out.mkdir(parents=True, exist_ok=True)
    launcher = default_launcher()
    present: list[str] = []
    tasks: list[dict[str, Any]] = []
    for dim in spec["dimensions"]:
        name = dim["dimension"]
        target = out / f"{name}.md"
        if target.is_file():
            present.append(name)
            continue
        prompt_file = out / "_tasks" / f"{name}.txt"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        desk_build._write_if_changed(prompt_file, prompt(build, out, dim, launcher))
        tasks.append({"dimension": name, "title": dim["title"], "cards": list(dim["cards"]),
                      "prompt_file": str(prompt_file), "output": str(target)})
    rel = build.relative_to(project).as_posix()
    repair = {"schema": REPAIR_SCHEMA, "round": 1, "from_build": rel, "redispatch": {}, "fixed": {},
              "invalidated_dimensions": []}
    desk_build._write_if_changed(out / "repair.json", json.dumps(repair, ensure_ascii=False, indent=1,
                                                                 sort_keys=True) + "\n")
    return {"schema": SCHEMA, "build": rel, "syntheses": str(out), "repair": str(out / "repair.json"),
            "present": present, "tasks": tasks}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit desk-tasks", description=__doc__.splitlines()[0])
    ap.add_argument("--build", type=Path, required=True, help="a finished build directory of the book path")
    ap.add_argument("--out", type=Path, required=True, help="where the workers write <dimension>.md (outside the build)")
    ap.add_argument("--project", type=Path, default=Path("."), help="data project directory (default: cwd)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else 0
    if not a.project.is_dir():
        return _usage(f"no such project directory: {a.project}")
    project = a.project.resolve()
    if not a.build.is_dir():
        return _usage(f"no such build directory: {a.build}")
    build = a.build.resolve()
    try:
        build.relative_to(project)
    except ValueError:
        return _usage(f"the build is not inside the project: {build}")
    out = a.out.resolve()
    if out == build or build in out.parents:
        return _usage(f"--out is inside the build (a finished build is read-only): {out}")
    try:
        doc = plan(project, build, out)
    except desk_build.DeskError as exc:
        print(f"desk-tasks: {exc}", file=sys.stderr)
        return exc.code
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit desk-tasks``
    raise SystemExit(main())
