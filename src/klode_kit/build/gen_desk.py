"""Desk spec + holdout generator, generic and medical (WI-03b; design 02 r2.4 ``gen_desk``, r3.4 "2.4 剩余").

The original klode-build desk step ① was manual: a person wrote ``src/desk.json`` (dimensions, source cards, cues,
tuning questions) and a holdout file. This module writes both from what the library already holds, with no model:

    klode-kit gen-desk KB [--desk F] [--mode auto|generic|records] [--items F] [--holdout-templates F] [--project DIR]
    exit 0 written / unchanged; 1 refused (an existing different file is never overwritten); 2 usage;
    20 holdout_insufficient (nothing written); 30 input contract (no chapters / items, missing card, invalid explicit
    desk / holdout / template file); 40 configuration

Output: ``src/desk.json`` in the WI-02c ``desk-build`` structure (``threshold``; per dimension ``dimension / title /
question / cards / cues / tests``) and ``src/holdout.jsonl`` (one ``{"q", "gold"}`` per line). Both are checked with
``desk_build.load`` / ``desk_build.load_holdout`` before anything is written, so ``desk-build --apply / --verify``
consume them as they are. A file already there with the same bytes is ``unchanged``; one with other bytes refuses
the whole run (the holdout is frozen by ``desk-build --verify``; a changed spec belongs in ``--desk``).

**Mode** (``auto``): ``explicit`` with ``--desk``; ``records`` when ``src/items_index.json`` exists; else ``generic``.

- **generic** (U4, a guide or book without a spec): dimensions = the pieces of ``src/chapters-*.json`` (gen_manifest;
  files in name order, pieces in manifest order). Up to ``MAX_DIMENSIONS`` (8) pieces: one dimension each (one piece
  - fewer than 2 chapter heads - is the whole book as one dimension); more: adjacent pieces merged into 8 groups of
  near-equal size (earlier groups one larger), never reordered. Name ``dNN[-<slug of the first title>]``.
- **records** (medical): dimensions only from what exists - the item cards named by ``items_index`` grouped by
  their ``items.yaml`` category (``--items`` table, else the packaged one; an item the table does not know, or one
  without a category, goes to ``uncategorized``), in the template's category order, unknown categories after by
  name; then one ``records`` dimension for the cards of ``library/records/*.txt``. Names and titles come from
  ``templates/desk_records.json``; an unknown category is ``cat-<ascii slug>`` (``cat-<sha8>`` when the category has
  no ASCII slug) titled with the category itself. More than 8 dimensions are merged like chapters.
- **explicit** (U7): ``src/desk.json`` = ``--desk`` byte for byte (a relative path is relative to KB). An existing
  ``src/holdout.jsonl`` is kept when it is valid for that desk; without one, a holdout is generated from the desk's
  own dimensions and cues.

**Cues** = each card's own ``aliases`` (``library/cards/<id>.md`` front matter), the first ``CUES_PER_CARD`` (5) of
each card, minus those a cue cannot hold (shorter than 2 characters, or ``,`` ``|`` newline) and minus repeats across
the whole desk (case-insensitive; the earlier dimension keeps it). Nothing else becomes a cue - in particular never
holdout wording (klode-kb SKILL.md iron law 2). **Tuning** (``tests``): one question per dimension, the tuning
template filled with the dimension's first cue (none without cues).

**Holdout**: question templates (``templates/holdout_generic.jsonl`` / ``holdout_records.jsonl``, or
``--holdout-templates``; lines ``{"category", "q"}`` with exactly one ``{a}``) filled with the dimension's cues.
Generic and explicit dimensions use the ``*`` templates; a medical item dimension its own category's templates first,
then the ``*`` ones (the per-item pad); the records dimension the ``records`` ones. Terms rotate across the cards
first, then across templates, so different cards and phrasings come before repeats; the rotation starts on the
second term (the tuning question uses the first). A question is dropped when it
repeats one already taken or a tuning question, holds a cue of another dimension (it would route ambiguously), or
is itself a substring of a cue. Questions are taken round-robin over the dimensions up to
``max(desk.holdout_min, number of dimensions)``; fewer than ``desk.holdout_min`` -> ``holdout_insufficient``, exit 20,
nothing written. The desk never depends on the templates (only the holdout does).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any

from klode_kit import config as config_mod
from klode_kit import items as items_mod
from klode_kit.build import desk_build as D
from klode_kit.build.cue_suggest import card_aliases
from klode_kit.build.gen_manifest import _slug

EXIT_REFUSED, EXIT_USAGE, EXIT_INSUFFICIENT, EXIT_CONTRACT = 1, 2, 20, 30
MAX_DIMENSIONS = 8
CUES_PER_CARD = 5
SLOT = "{a}"
ANY = "*"
GENERIC_TUNE = "{a} 是什么？"
GENERIC_THRESHOLD = 0.8
DESK_REL, HOLDOUT_REL, INDEX_REL = "src/desk.json", "src/holdout.jsonl", "src/items_index.json"
ASCII_SLUG = re.compile(r"[a-z0-9]+")


class DeskGenError(Exception):
    """A refusal with its exit code and a short machine-readable reason (``holdout_insufficient`` ...)."""

    def __init__(self, code: int, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.code = code
        self.reason = reason


# --------------------------------------------------------------------------- inputs


def _template_file(name: str) -> str:
    return (resources.files("klode_kit") / "templates" / name).read_text(encoding="utf-8")


def load_templates(path: str | os.PathLike | None, default: str) -> list[tuple[str, str]]:
    """``[(category, template)]`` from a JSONL template file (``None``: the packaged ``default``)."""
    try:
        text = _template_file(default) if path is None else Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DeskGenError(EXIT_CONTRACT, "bad_template", f"读不了留出题模板 {path}：{exc}") from None
    out: list[tuple[str, str]] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            raise DeskGenError(EXIT_CONTRACT, "bad_template", f"留出题模板第 {n} 行不是 JSON") from None
        q = obj.get("q") if isinstance(obj, dict) else None
        cat = obj.get("category", ANY) if isinstance(obj, dict) else None
        if not isinstance(q, str) or not isinstance(cat, str) or q.count(SLOT) != 1:
            raise DeskGenError(EXIT_CONTRACT, "bad_template",
                               f"留出题模板第 {n} 行须是 {{\"category\", \"q\"}}，q 里恰好一个 {SLOT}")
        out.append((cat, q))
    return out


def _aliases(kb: Path, cid: str) -> list[str]:
    if not (kb / "library" / "cards" / f"{cid}.md").is_file():
        raise DeskGenError(EXIT_CONTRACT, "missing_card", f"卡片不存在：library/cards/{cid}.md（先 write-card）")
    return card_aliases(kb, cid)


def _library_id(kb: Path) -> str | None:
    try:
        lib = tomllib.loads((kb / "library.toml").read_text(encoding="utf-8")).get("library", {})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None
    return lib.get("id") if isinstance(lib.get("id"), str) else None


def _groups(n: int, limit: int = MAX_DIMENSIONS) -> list[list[int]]:
    """Adjacent groups of ``range(n)``: one each up to ``limit``, else ``limit`` near-equal runs."""
    if n <= limit:
        return [[i] for i in range(n)]
    size, extra = divmod(n, limit)
    out, start = [], 0
    for g in range(limit):
        end = start + size + (1 if g < extra else 0)
        out.append(list(range(start, end)))
        start = end
    return out


# A planned dimension before cues: {"dimension", "title", "question", "cards", "kinds", "tune"}.


def _generic_plan(kb: Path) -> list[dict[str, Any]]:
    pieces: list[dict[str, Any]] = []
    for mf in sorted((kb / "src").glob("chapters-*.json")):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
            got = m["pieces"]
            if not isinstance(got, list) or not all(isinstance(p, dict) and isinstance(p.get("id"), str)
                                                    for p in got):
                raise TypeError("pieces")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise DeskGenError(EXIT_CONTRACT, "bad_manifest", f"章节清单读不了：{mf.name}（{exc}）") from None
        pieces += got
    if not pieces:
        raise DeskGenError(EXIT_CONTRACT, "no_source",
                           "没有可生成书桌的来源：既没有 src/chapters-*.json（gen-manifest），也没有 src/items_index.json")
    plan = []
    for k, group in enumerate(_groups(len(pieces)), start=1):
        titles = [str(pieces[i].get("title") or pieces[i]["id"]) for i in group]
        slug = _slug(titles[0])
        title = " / ".join(titles)
        plan.append({"dimension": f"d{k:02d}" + (f"-{slug}" if slug else ""), "title": title,
                     "question": f"「{title}」讲了什么、要点是什么？", "cards": [pieces[i]["id"] for i in group],
                     "kinds": [ANY], "tune": GENERIC_TUNE})
    return plan


def _unknown_dim_name(prefix: str, category: str) -> str:
    words = ASCII_SLUG.findall(category.lower())
    return prefix + ("-".join(words) if words else hashlib.sha256(category.encode("utf-8")).hexdigest()[:8])


def _records_plan(kb: Path, items_table: str | os.PathLike | None) -> tuple[list[dict[str, Any]], float]:
    tpl = json.loads(_template_file("desk_records.json"))
    try:
        index = json.loads((kb / INDEX_REL).read_text(encoding="utf-8"))
        if not isinstance(index, dict):
            raise TypeError("not an object")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise DeskGenError(EXIT_CONTRACT, "bad_items_index", f"{INDEX_REL} 读不了：{exc}") from None
    table = items_mod.load(items_table) if items_table is not None else items_mod.default()
    known = {c["category"]: c for c in tpl["categories"]}
    by_cat: dict[str, list[str]] = {}
    for slug in sorted(index):
        by_cat.setdefault(table.category(slug) or tpl["uncategorized"], []).append(slug)
    order = [c["category"] for c in tpl["categories"] if c["category"] in by_cat]
    order += sorted(c for c in by_cat if c not in known)
    plan = []
    unk = tpl["unknown_category"]
    for cat in order:
        spec = known.get(cat) or {"dimension": _unknown_dim_name(unk["dimension_prefix"], cat),
                                  "title": unk["title"].replace("{category}", cat),
                                  "question": unk["question"].replace("{category}", cat), "tune": unk["tune"]}
        plan.append({"dimension": spec["dimension"], "title": spec["title"], "question": spec["question"],
                     "cards": by_cat[cat], "kinds": [cat, ANY], "tune": spec["tune"]})
    shelf = kb / "library" / "records"
    recs = sorted(p.stem for p in shelf.glob("*.txt") if not p.name.startswith("_")) if shelf.is_dir() else []
    if recs:
        r = tpl["records"]
        plan.append({"dimension": r["dimension"], "title": r["title"], "question": r["question"], "cards": recs,
                     "kinds": [r["category"]], "tune": r["tune"]})
    if not plan:
        raise DeskGenError(EXIT_CONTRACT, "no_source", f"{INDEX_REL} 里没有任何项目，也没有 library/records 单据")
    if len(plan) > MAX_DIMENSIONS:
        merged = []
        for group in _groups(len(plan)):
            first = plan[group[0]]
            merged.append({"dimension": first["dimension"], "title": " / ".join(plan[i]["title"] for i in group),
                           "question": first["question"], "cards": [c for i in group for c in plan[i]["cards"]],
                           "kinds": list(dict.fromkeys(k for i in group for k in plan[i]["kinds"])),
                           "tune": first["tune"]})
        plan = merged
    return plan, float(tpl["threshold"])


# --------------------------------------------------------------------------- cues, tuning, holdout


def _usable(alias: str) -> bool:
    return len(alias.strip()) >= 2 and not any(ch in alias for ch in D.CUE_FORBIDDEN)


def _with_cues(kb: Path, plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each planned dimension with ``cues`` and ``terms`` (the cues interleaved card by card)."""
    taken: set[str] = set()
    out = []
    for p in plan:
        per_card: list[list[str]] = []
        for cid in p["cards"]:
            mine = []
            for a in _aliases(kb, cid)[:CUES_PER_CARD]:
                a = a.strip()
                if _usable(a) and a.lower() not in taken:
                    taken.add(a.lower())
                    mine.append(a)
            per_card.append(mine)
        cues = [c for mine in per_card for c in mine]
        terms = [mine[k] for k in range(max((len(m) for m in per_card), default=0)) for mine in per_card
                 if k < len(mine)]
        out.append({**p, "cues": cues, "terms": terms})
    return out


def _candidates(dim: dict[str, Any], others: list[str], templates: list[tuple[str, str]], blocked: set[str],
                all_cues: list[str]) -> list[str]:
    tpls = list(dict.fromkeys(q for kind in dim["kinds"] for cat, q in templates if cat == kind))
    terms = dim["terms"]
    shift = 1 if len(terms) > 1 else 0  # the tuning question uses terms[0]: start the holdout on another term
    out: list[str] = []
    for r in range(len(terms)):
        for i, t in enumerate(tpls):
            q = t.replace(SLOT, terms[(i + r + shift) % len(terms)])
            ql = q.lower()
            if q in blocked or q in out or any(o in ql for o in others) or any(q in c for c in all_cues):
                continue
            out.append(q)
    return out


def make_holdout(dims: list[dict[str, Any]], templates: list[tuple[str, str]], *, holdout_min: int) -> list[dict]:
    """Round-robin holdout over ``dims`` (each with ``dimension``, ``cues``, ``terms``, ``kinds``, ``tests``)."""
    tune = {q for d in dims for q in d["tests"]}
    all_cues = [c for d in dims for c in d["cues"]]
    pools = []
    for d in dims:
        others = [c.lower() for o in dims if o is not d for c in o["cues"]]
        pools.append((d["dimension"], _candidates(d, others, templates, tune, all_cues)))
    target = max(holdout_min, len(dims))
    taken: list[dict] = []
    seen: set[str] = set()
    depth = 0
    while len(taken) < target and any(depth < len(pool) for _, pool in pools):
        for name, pool in pools:
            if depth < len(pool) and pool[depth] not in seen and len(taken) < target:
                seen.add(pool[depth])
                taken.append({"q": pool[depth], "gold": name})
        depth += 1
    if len(taken) < holdout_min:
        detail = "，".join(f"{name} {len(pool)} 道" for name, pool in pools)
        raise DeskGenError(EXIT_INSUFFICIENT, "holdout_insufficient",
                           f"只能从现有维度生成 {len(taken)} 道互不相同的留出题，需 ≥ {holdout_min}（{detail}）；"
                           "补卡片别名或给出 --holdout-templates / 显式 --desk 与留出集，不会静默通过")
    return taken


# --------------------------------------------------------------------------- write


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)


def _holdout_bytes(holdout: list[dict]) -> bytes:
    return "".join(json.dumps({"q": h["q"], "gold": h["gold"]}, ensure_ascii=False) + "\n"
                   for h in holdout).encode("utf-8")


def _validate(kb: Path, desk_bytes: bytes, holdout_bytes: bytes) -> tuple[dict, list[dict]]:
    """``desk_build``'s own checks on the would-be files (in a scratch directory outside KB)."""
    with tempfile.TemporaryDirectory() as tmp:
        spec, hold = Path(tmp) / "desk.json", Path(tmp) / "holdout.jsonl"
        spec.write_bytes(desk_bytes)
        hold.write_bytes(holdout_bytes)
        try:
            d = D.load(kb, spec)
            items, _ = D.load_holdout(hold, d)
        except D.DeskError as exc:  # pragma: no cover - generated files are built to pass; a bug here, not input
            raise DeskGenError(EXIT_CONTRACT, "bad_desk", str(exc)) from None
    return d, items


def _commit(kb: Path, files: dict[str, bytes], keep: set[str]) -> tuple[list[str], list[str]]:
    """Write ``files`` (relative to KB) unless present with other bytes: all or nothing."""
    for rel, data in files.items():
        p = kb / rel
        if rel not in keep and p.exists() and p.read_bytes() != data:
            raise DeskGenError(EXIT_REFUSED, "exists_different",
                               f"{rel} 已存在且与本次结果不同，不覆盖（留出集由 desk-build 冻结；要用现有书桌请加 "
                               f"--desk {DESK_REL}，否则先把它移走到 _trash/）")
    written, unchanged = [], []
    for rel, data in files.items():
        p = kb / rel
        if rel in keep or (p.exists() and p.read_bytes() == data):
            unchanged.append(rel)
        else:
            _atomic_write(p, data)
            written.append(rel)
    return written, unchanged


def gen_desk(kb: str | Path, *, desk: str | Path | None = None, mode: str = "auto",
             items: str | os.PathLike | None = None, holdout_templates: str | os.PathLike | None = None,
             project: str | Path | None = None, holdout_min: int | None = None) -> dict[str, Any]:
    """Write ``src/desk.json`` and ``src/holdout.jsonl``; returns ``{"mode", "desk", "holdout", "written",
    "unchanged"}``. ``DeskGenError`` (nothing written) on refusal; ``ConfigError`` on a broken configuration."""
    kb = Path(kb).resolve()
    if holdout_min is None:
        holdout_min = config_mod.load(project)["desk.holdout_min"]
    if mode not in ("auto", "generic", "records", "explicit"):
        raise ValueError(f"unknown mode {mode!r}")
    if desk is not None or mode == "explicit":
        if desk is None:
            raise ValueError("explicit mode needs desk")
        return _explicit(kb, Path(desk) if Path(desk).is_absolute() else kb / desk, holdout_templates, holdout_min)
    if mode == "auto":
        mode = "records" if (kb / INDEX_REL).is_file() else "generic"
    if mode == "records":
        plan, threshold = _records_plan(kb, items)
        templates = load_templates(holdout_templates, "holdout_records.jsonl")
    else:
        plan, threshold = _generic_plan(kb), GENERIC_THRESHOLD
        templates = load_templates(holdout_templates, "holdout_generic.jsonl")
    dims = _with_cues(kb, plan)
    for d in dims:
        d["tests"] = [d["tune"].replace(SLOT, d["cues"][0])] if d["cues"] else []
    holdout = make_holdout(dims, templates, holdout_min=holdout_min)
    spec: dict[str, Any] = {"threshold": threshold, "dimensions": [
        {k: d[k] for k in D.SPEC_FIELDS} for d in dims]}
    if (kid := _library_id(kb)) is not None:
        spec["kb"] = kid
    spec["generated_by"] = {"tool": "klode-kit gen-desk", "mode": mode}
    desk_bytes = (json.dumps(spec, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    hold_bytes = _holdout_bytes(holdout)
    d, items_out = _validate(kb, desk_bytes, hold_bytes)
    written, unchanged = _commit(kb, {DESK_REL: desk_bytes, HOLDOUT_REL: hold_bytes}, set())
    return {"mode": mode, "desk": d, "holdout": items_out, "written": written, "unchanged": unchanged}


def _explicit(kb: Path, path: Path, holdout_templates: str | os.PathLike | None,
              holdout_min: int) -> dict[str, Any]:
    try:
        d = D.load(kb, path)
    except D.DeskError as exc:
        raise DeskGenError(EXIT_CONTRACT, "bad_desk", str(exc)) from None
    desk_bytes = path.read_bytes()
    keep = {DESK_REL} if path.resolve() == (kb / DESK_REL).resolve() else set()
    existing = kb / HOLDOUT_REL
    if existing.is_file():
        try:
            holdout, _ = D.load_holdout(existing, d)
        except D.DeskError as exc:
            raise DeskGenError(EXIT_CONTRACT, "bad_holdout", f"现有 {HOLDOUT_REL} 与显式书桌不符：{exc}") from None
        hold_bytes = existing.read_bytes()
    else:
        dims = [{"dimension": x["dimension"], "cues": x["cues"], "terms": x["cues"], "kinds": [ANY],
                 "tests": x["tests"]} for x in d["dimensions"]]
        holdout = make_holdout(dims, load_templates(holdout_templates, "holdout_generic.jsonl"),
                               holdout_min=holdout_min)
        hold_bytes = _holdout_bytes(holdout)
        _validate(kb, desk_bytes, hold_bytes)
    written, unchanged = _commit(kb, {DESK_REL: desk_bytes, HOLDOUT_REL: hold_bytes}, keep)
    return {"mode": "explicit", "desk": d, "holdout": holdout, "written": written, "unchanged": unchanged}


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit gen-desk", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kb", type=Path, help="库根目录（含 library.toml）")
    ap.add_argument("--desk", type=Path, help="显式书桌 spec（U7；相对库根或绝对），按字节采用")
    ap.add_argument("--mode", choices=["auto", "generic", "records"], default="auto")
    ap.add_argument("--items", type=Path, help="items.yaml 项目表（默认插件自带）")
    ap.add_argument("--holdout-templates", type=Path, help="留出题模板 JSONL（默认插件自带）")
    ap.add_argument("--project", type=Path, help="数据项目（klode-kit.toml 的 desk.holdout_min）")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else 0
    try:
        res = gen_desk(a.kb, desk=a.desk, mode=a.mode, items=a.items, holdout_templates=a.holdout_templates,
                       project=a.project)
    except DeskGenError as exc:
        print(f"gen-desk: {exc.reason}: {exc}", file=sys.stderr)
        return exc.code
    except config_mod.ConfigError as exc:
        print(f"gen-desk: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code
    dims = res["desk"]["dimensions"]
    print(f"gen-desk: {res['mode']}  {len(dims)} 个维度，留出集 {len(res['holdout'])} 题"
          f"（写入 {', '.join(res['written']) or '无'}；未变 {', '.join(res['unchanged']) or '无'}）")
    for x in dims:
        print(f"  {x['dimension']:<28} cards {len(x['cards']):>3}  cues {len(x['cues']):>3}  {x['title']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit gen-desk``
    raise SystemExit(main())
