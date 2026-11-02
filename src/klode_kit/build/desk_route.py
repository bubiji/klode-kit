"""Desk acceptance by model routing (WI-35, decision D-46; a real finding of the first real-library migration).

Keyword routing (``klode --json diagnose`` over the cues of ``src/desk.json``) hit 1/12 of a frozen holdout of new
phrasings while a model routing the same questions zero-shot hit 12/12; iron rule 2 forbids tuning cues on the
holdout, so an honest library can never pass the keyword desk gate. With a *routing file* the gate is the hit rate of
a model's routing instead; keyword ``diagnose`` is still computed and reported, it just no longer gates.

    klode-kit desk-route-tasks --build BUILD_DIR --out DIR [--project DIR]
    exit 0 planned; 2 usage (no such build / project, build outside the project, --out inside the build);
    30 contract (no or bad ``src/desk.json``, no or bad ``src/holdout.jsonl``)

writes ``DIR/routing_prompt.txt`` for ONE worker and prints a JSON document ``klode-kit.desk_route_tasks/1``: ``build``
(project-relative), ``prompt_file``, ``output`` (``DIR/routing.json``: where the worker must write its answer),
``prompt_sha256``, ``dimensions`` and ``questions``. The prompt holds the dimensions (id, title, core question) and the
holdout QUESTIONS ONLY, each under a ``qid`` (``q01``... in the order of ``src/holdout.jsonl``). The expected
dimensions are never part of it (separation law: the prompt is a function of the dimensions and the questions).

The worker writes ``{"schema": "klode-kit.desk_routing/1", "prompt_sha256": ..., "answers": [{"qid", "dimension"}]}``
(``dimension`` null = none fits). ``desk-build --verify --routing FILE`` (and ``build --desk-routing FILE``) then takes
the hit rate over the frozen holdout, computed here from the frozen labels, as the desk gate. The file is refused
(contract error) unless its ``prompt_sha256`` is the sha of the prompt body regenerated from this library's dimensions
and holdout, every holdout qid is answered exactly once, no other qid appears and each ``dimension`` is an existing
dimension id or null. Nothing is invented and nothing is called: no model, no ``klode``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from klode_kit.build import desk_build
from klode_kit.build.desk_build import EXIT_CONTRACT, EXIT_USAGE, DeskError

TASKS_SCHEMA = "klode-kit.desk_route_tasks/1"
ROUTING_SCHEMA = "klode-kit.desk_routing/1"
PROMPT_NAME = "routing_prompt.txt"
OUTPUT_NAME = "routing.json"


def qid(n: int) -> str:
    """The id of the ``n``-th (1-based) holdout question."""
    return f"q{n:02d}"


def _one_line(text: str) -> str:
    return " ".join(text.split())


def prompt_body(spec: dict, items: list[dict]) -> str:
    """The part of the prompt the sha binds: the dimensions and the questions. Gold labels are not an input."""
    lines = [
        "任务：书桌路由。下面是一个知识库的若干「维度」，和一组用户问题。请只凭每个维度的描述，",
        "给每个问题选出最该去查的那一个维度；没有任何维度合适就答 null。",
        "不要读库里的别的东西，不要猜测评分方式，每题独立判断。",
        "",
        "## 维度",
    ]
    for d in spec["dimensions"]:
        lines += [f"- id: {d['dimension']}", f"  标题: {_one_line(d['title'])}", f"  核心问题: {_one_line(d['question'])}"]
    lines += ["", "## 问题"]
    lines += [f"{qid(n)}: {_one_line(it['q'])}" for n, it in enumerate(items, start=1)]
    return "\n".join(lines) + "\n"


def prompt_sha(spec: dict, items: list[dict]) -> str:
    return hashlib.sha256(prompt_body(spec, items).encode("utf-8")).hexdigest()


def render_prompt(spec: dict, items: list[dict], output: Path) -> str:
    sha = prompt_sha(spec, items)
    example = {"schema": ROUTING_SCHEMA, "prompt_sha256": sha,
               "answers": [{"qid": qid(1), "dimension": "<维度 id 或 null>"}, {"qid": qid(2), "dimension": None}]}
    return (prompt_body(spec, items)
            + "\n## 怎么交答案\n"
            + f"只写一个文件：{output}（JSON，UTF-8）。每个问题恰好一条，qid 不多不少；dimension 只能是上面的维度 id 或 null。\n"
            + "格式（prompt_sha256 原样抄下面这个，不要改）：\n"
            + json.dumps(example, ensure_ascii=False, indent=1) + "\n"
            + "写完回报一行：`desk-routing  已写  N 题`。除这个文件外不写任何文件，不碰 git。\n")


def _fail(message: str) -> DeskError:
    return DeskError(EXIT_CONTRACT, f"routing 文件：{message}")


def load_routing(path: Path, spec: dict, items: list[dict]) -> dict[str, str | None]:
    """``{qid: dimension or None}`` of a valid routing file; DeskError(30) on anything else."""
    if not Path(path).is_file():
        raise _fail(f"找不到 {path}")
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _fail(f"不是合法 JSON：{path}：{exc}") from None
    if not isinstance(doc, dict) or set(doc) != {"schema", "prompt_sha256", "answers"}:
        raise _fail("须是 {schema, prompt_sha256, answers}")
    if doc["schema"] != ROUTING_SCHEMA:
        raise _fail(f"schema 须是 {ROUTING_SCHEMA}，不是 {doc['schema']!r}")
    if doc["prompt_sha256"] != prompt_sha(spec, items):
        raise _fail("prompt_sha256 与本库当前维度 / 留出题生成的路由 prompt 不一致（别的库、维度或留出集变过的 prompt 作出的答案不收）；"
                    "重跑 desk-route-tasks 再派 worker")
    answers = doc["answers"]
    if not isinstance(answers, list):
        raise _fail("answers 须是列表")
    dims = {d["dimension"] for d in spec["dimensions"]}
    got: dict[str, str | None] = {}
    for a in answers:
        if not isinstance(a, dict) or set(a) != {"qid", "dimension"} or not isinstance(a["qid"], str):
            raise _fail(f"每条答案须是 {{qid, dimension}}：{a!r}")
        dim = a["dimension"]
        if dim is not None and (not isinstance(dim, str) or dim not in dims):
            raise _fail(f"{a['qid']} 的 dimension {dim!r} 不是本库的维度 id（也不是 null）")
        if a["qid"] in got:
            raise _fail(f"qid 重复：{a['qid']}")
        got[a["qid"]] = dim
    want = [qid(n) for n in range(1, len(items) + 1)]
    unknown = sorted(set(got) - set(want))
    missing = [q for q in want if q not in got]
    if unknown:
        raise _fail(f"不属于留出集的 qid：{', '.join(unknown)}")
    if missing:
        raise _fail(f"留出题没答完，缺：{', '.join(missing)}")
    return got


def score(path: Path, spec: dict, items: list[dict]) -> list[dict]:
    """``[{"q", "expect", "got", "ok"}]`` in holdout order: the routing file against the frozen labels."""
    got = load_routing(path, spec, items)
    return [{"q": it["q"], "expect": it["gold"], "got": got[qid(n)], "ok": got[qid(n)] == it["gold"]}
            for n, it in enumerate(items, start=1)]


# --------------------------------------------------------------------------- the command


def _usage(message: str) -> int:
    print(f"desk-route-tasks: {message}", file=sys.stderr)
    return EXIT_USAGE


def plan(project: Path, build: Path, out: Path) -> dict[str, Any]:
    spec = desk_build.load(build)
    holdout = build / "src" / "holdout.jsonl"
    items, raw = desk_build.load_holdout(holdout, spec)
    if raw is None:
        raise DeskError(EXIT_CONTRACT, f"找不到留出集：{holdout}")
    out.mkdir(parents=True, exist_ok=True)
    output = out / OUTPUT_NAME
    prompt_file = out / PROMPT_NAME
    desk_build._write_if_changed(prompt_file, render_prompt(spec, items, output))
    return {"schema": TASKS_SCHEMA, "build": build.relative_to(project).as_posix(), "prompt_file": str(prompt_file),
            "output": str(output), "prompt_sha256": prompt_sha(spec, items),
            "dimensions": [d["dimension"] for d in spec["dimensions"]], "questions": len(items)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit desk-route-tasks", description=__doc__.splitlines()[0])
    ap.add_argument("--build", type=Path, required=True, help="a finished (or failed) build directory")
    ap.add_argument("--out", type=Path, required=True, help="where the prompt goes and the worker writes routing.json")
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
    except DeskError as exc:
        print(f"desk-route-tasks: {exc}", file=sys.stderr)
        return exc.code
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit desk-route-tasks``
    raise SystemExit(main())
