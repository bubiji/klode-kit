"""WI-01b: ``records_to_l1`` - deterministic cards for the ``items`` and ``records`` shelves (design 02 r2.2-C 卡 /
索引, r3.4 R2-02; plan WI-01b; matrix 01b-*; impl-deviations WI-02a -> 01b F1). No model is called.

``generate(build, *, min_aliases=None, table=None) -> {"cards": {id: text}, "aliases": {id: [alias]}}`` reads the
WI-01a export under ``build`` - ``library/items/*.txt``, ``library/records/*.txt``, ``src/export_map.jsonl``,
``src/items_index.json`` - and gives one card per source with the source's id (``cards_content/<id>.md``, the form
``write-card`` publishes; ``check_cards.find_source`` pairs them by name). Every non-empty source line must be the
``text`` of its ``export_map`` row (same file, same line number), and ``items_index`` must name exactly the item
sources; otherwise the export is stale or foreign and the run is an ``L1Error``.

**Card** (everything is derived from the export; same export -> same bytes)::

    aliases: [<a>, <b>, ...]
    kind: deterministic

    # <title>

    ## Thin
    <summary in words> (grep: `<whole line>`)

    ## Full
    ### 逐次观测 | 逐行原文
    - <what the line says> (grep: `<whole line>`)      <- one bullet per source line, in source order

- item card: Thin = the number of observations and their date span, the latest observation (the last line: the
  export orders an item's lines by date) with its whole line as the anchor, and the trend against the observation
  before it: ``上升`` / ``下降`` / ``持平`` when both values are plain decimal numbers in the same unit, otherwise
  ``无法比较``; a single observation says ``仅 1 次观测``. Words only describe the numbers - no clinical reading.
- record card: Thin = the record's date and the items it holds, anchored on its first line.
- Full: one whole-line anchor per source line - anchors == lines, no line twice (gate 1 ``COV``). A line whose
  text also occurs earlier in the source gets ``#N`` (its own occurrence); one that is a substring of a *later*
  line cannot be anchored unambiguously and is an ``L1Error``, as is a line holding a backtick.

**Aliases** come only from deterministic sources, in this order, de-duplicated after NFKC + case + whitespace
folding: an item card - the item's display name (as exported), its ``items.yaml`` aliases and slug, the names of
``config/card_aliases.toml`` ``[items.<slug>]``, then each unit written in the source (one with no letter or digit,
such as ``%``, is skipped) followed by its ``[units]`` spellings; a record card - its rec_id, its date in five
spellings (``2026-09-18 2026/09/18 2026.09.18 20260918 2026年9月18日``), then for each item in it, the item's
display name, ``items.yaml`` aliases, slug and table names. An alias holding ``,`` ``[`` ``]`` or a backtick is
dropped (it would break the aliases line). A card with fewer than ``min_aliases`` (default
``check_cards.MIN_ALIASES_DETERMINISTIC``, 1 - gate 1's F1 floor for deterministic shelves; user decision
2026-10-24) is an ``AliasShortage`` naming every short card and its count; nothing is generated. Aliases are
never padded: a card has exactly what its deterministic sources give.

The table (``table`` overrides the packaged file) must not give an item a name that is another item's
``items.yaml`` alias, nor a name with ``,`` ``[`` ``]`` or a backtick (``ConfigError``).

``write(build, ...) -> {"written", "unchanged", "bibliography_added"}`` writes the cards to ``cards_content/``.
A card already there with the same bytes is ``unchanged``; one with other bytes refuses the run (``L1Error``)
before anything is written. When ``library/BIBLIOGRAPHY.md`` exists (``klode init``), one row per source not yet
listed is appended, so ``klode check --strict`` has no bibliography warning; a rerun appends nothing.

    klode-kit records-to-l1 KB [--json]
    exit 0 written / unchanged; 1 alias shortage or refused; 2 usage; 40 configuration
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
import unicodedata
from decimal import Decimal, InvalidOperation
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

from klode_kit import items as items_mod
from klode_kit.build.check_cards import MIN_ALIASES_DETERMINISTIC
from klode_kit.build.records_to_txt import EXPORT_MAP, ITEMS_INDEX, ITEMS_SHELF, RECORDS_SHELF, _slot
from klode_kit.config import ConfigError

KIND = "deterministic"
TABLE_FILE = "card_aliases.toml"
CARDS_DIR = "cards_content"
BIBLIOGRAPHY = "library/BIBLIOGRAPHY.md"
_BAD = re.compile(r"[,\[\]`\n\r]")
_DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


class L1Error(RuntimeError):
    """records_to_l1 cannot run (no / stale export, inexpressible anchor, a differing card already written)."""


class AliasShortage(L1Error):
    """Some card would have fewer aliases than gate 1 wants (``short``: ``{card id: count}``)."""

    def __init__(self, short: Mapping[str, int], need: int):
        self.short = dict(sorted(short.items()))
        self.need = need
        detail = ", ".join(f"{cid} ({n})" for cid, n in self.short.items())
        super().__init__(f"aliases below {need} (deterministic sources exhausted): {detail}")


# --------------------------------------------------------------------------- alias table


def _key(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s)).casefold()


def load_table(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """``{"items": {slug: [name]}, "units": {unit: [spelling]}}`` from the table at ``path`` (None: packaged)."""
    if path is None:
        text, where = (resources.files("klode_kit.config") / TABLE_FILE).read_text(encoding="utf-8"), TABLE_FILE
    else:
        try:
            text, where = Path(path).read_text(encoding="utf-8"), str(path)
        except OSError as exc:
            raise ConfigError(f"{path}: cannot read alias table: {exc}", key="card_aliases") from exc
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{where}: {exc}", key="card_aliases") from exc
    table = items_mod.default()
    items: dict[str, list[str]] = {}
    for slug, entry in (data.get("items") or {}).items():
        names = entry.get("names", []) if isinstance(entry, dict) else None
        if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
            raise ConfigError(f"{where}: [items.{slug}] names must be a list of non-empty strings", key="card_aliases")
        for n in names:
            if _BAD.search(n):
                raise ConfigError(f"{where}: [items.{slug}] name {n!r} holds , [ ] or a backtick", key="card_aliases")
            owner, known = table.resolve(n)
            if known and owner != slug:
                raise ConfigError(f"{where}: [items.{slug}] name {n!r} is an alias of item {owner!r}",
                                  key="card_aliases")
        items[slug] = list(names)
    units: dict[str, list[str]] = {}
    for unit, spellings in (data.get("units") or {}).items():
        if not isinstance(spellings, list) or not all(isinstance(s, str) and s.strip() for s in spellings):
            raise ConfigError(f"{where}: [units] {unit!r} must be a list of non-empty strings", key="card_aliases")
        if any(_BAD.search(s) for s in [unit, *spellings]):
            raise ConfigError(f"{where}: [units] {unit!r} holds , [ ] or a backtick", key="card_aliases")
        units[unit] = list(spellings)
    return {"items": items, "units": units}


class _Aliases:
    def __init__(self) -> None:
        self.out: list[str] = []
        self._seen: set[str] = set()

    def add(self, *names: str) -> None:
        for n in names:
            n = n.strip()
            if not n or _BAD.search(n) or _key(n) in self._seen:
                continue
            self._seen.add(_key(n))
            self.out.append(n)


def _item_names(slug: str, display: str, table: Mapping[str, Any]) -> list[str]:
    """``slug`` is the file stem (D-38; a table slug is ASCII, so its stem is itself)."""
    item = next((it for it in items_mod.default().items if items_mod.file_stem(it.slug) == slug), None)
    named = next((v for k, v in table["items"].items() if items_mod.file_stem(k) == slug), [])
    return [display, *(item.aliases if item else ()), slug, *named]


def _date_forms(date: str) -> list[str]:
    m = _ISO.match(date)
    if not m:
        return [date]
    y, mo, d = m.groups()
    return [date, f"{y}/{mo}/{d}", f"{y}.{mo}.{d}", f"{y}{mo}{d}", f"{y}年{int(mo)}月{int(d)}日"]


# --------------------------------------------------------------------------- export reading


class _Line:
    def __init__(self, row: Mapping[str, Any], offset: int):
        self.offset = offset
        self.text: str = row["text"]
        self.kind: str = row.get("line_kind") or "source"
        self.rec_id: str = row["rec_id"]
        self.value: str = row.get("value_audited") or ""
        roles: dict[str, str] = {}
        for fid, (s, e) in row.get("positions", {}).items():
            role = _slot(fid)[1]
            roles.setdefault(role, self.text[s:e])
        self.unit = roles.get("unit", "")
        self.date = roles.get("date", "")
        self.flag = roles.get("flag", "")
        vpos = next((p for f, p in row.get("positions", {}).items() if _slot(f)[1] == "value"), None)
        self.display = self.text[:vpos[0]].strip() if vpos and self.kind == "observation" else ""
        self.base = self.text[:self.text.rfind(" ⟨")] if " ⟨" in self.text else self.text


def _read_export(build: Path) -> tuple[dict[str, tuple[str, str, list[_Line]]], dict]:
    """``({id: (shelf, source text, lines)}, items_index)``."""
    emap, index = build / EXPORT_MAP, build / ITEMS_INDEX
    if not emap.is_file() or not index.is_file():
        raise L1Error(f"{build}: no WI-01a export ({EXPORT_MAP} / {ITEMS_INDEX} missing)")
    try:
        rows = [json.loads(raw) for raw in emap.read_text(encoding="utf-8").splitlines() if raw.strip()]
        items_index = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise L1Error(f"{build}: unreadable export: {exc}") from exc
    by_file: dict[str, dict[int, Mapping[str, Any]]] = {}
    for r in rows:
        by_file.setdefault(r["source_file"], {})[r["line_no"]] = r
    out: dict[str, tuple[str, str, list[_Line]]] = {}
    for shelf in (ITEMS_SHELF, RECORDS_SHELF):
        for path in sorted((build / shelf).glob("*.txt")):
            rel = f"{shelf}/{path.name}"
            text = path.read_text(encoding="utf-8")
            mapped = by_file.pop(rel, {})
            lines: list[_Line] = []
            offset = 0
            for n, raw in enumerate(text.split("\n"), start=1):
                start, offset = offset, offset + len(raw) + 1
                if not raw.strip():
                    continue
                row = mapped.pop(n, None)
                if row is None or row.get("text") != raw:
                    raise L1Error(f"{rel}:{n}: the line is not the export map's (stale or foreign export)")
                lines.append(_Line(row, start))
            if mapped:
                raise L1Error(f"{rel}: export map rows without a source line: {sorted(mapped)}")
            if path.stem in out:
                raise L1Error(f"{path.stem}: one id on two shelves")
            if not lines:
                raise L1Error(f"{rel}: empty source")
            out[path.stem] = (shelf, text, lines)
    if by_file:
        raise L1Error(f"export map names missing sources: {sorted(by_file)}")
    item_ids = {cid for cid, (shelf, _, _) in out.items() if shelf == ITEMS_SHELF}
    if set(items_index) != item_ids:
        raise L1Error(f"{ITEMS_INDEX} does not name the item sources: {sorted(set(items_index) ^ item_ids)}")
    return out, items_index


# --------------------------------------------------------------------------- card text


def _anchor(text: str, offset: int, line: str) -> str:
    if "`" in line:
        raise L1Error(f"line holds a backtick, no anchor can quote it: {line!r}")
    starts, pos = [], text.find(line)
    while pos >= 0:
        starts.append(pos)
        pos = text.find(line, pos + 1)
    nth = starts.index(offset) + 1
    if nth == 1 and len(starts) > 1:
        raise L1Error(f"line also occurs inside a later line, no unambiguous anchor: {line!r}")
    return f"(grep: `{line}`)" if nth == 1 else f"(grep: `{line}` #{nth})"


def _anchors(text: str, lines: list[_Line]) -> list[str]:
    return [_anchor(text, ln.offset, ln.text) for ln in lines]


def _measure(ln: _Line) -> str:
    return " ".join(p for p in (ln.value, ln.unit) if p)


def _trend(last: _Line, prev: _Line) -> str:
    head = f"上一次（{prev.date}）为 {_measure(prev)}，"
    if last.unit == prev.unit and _DECIMAL.match(last.value) and _DECIMAL.match(prev.value):
        try:
            a, b = Decimal(last.value), Decimal(prev.value)
        except InvalidOperation:  # pragma: no cover - the pattern admits only decimals
            return head + "数值无法比较。"
        word = "上升" if a > b else "下降" if a < b else "持平"
        return head + f"本次较上一次{word}。"
    return head + "两者无法比较（不是同一单位下的纯数字）。"


def _item_card(cid: str, text: str, lines: list[_Line], aliases: list[str]) -> str:
    anchors = _anchors(text, lines)
    last = lines[-1]
    name = last.display or cid
    flag = f"，单据标注 {last.flag}" if last.flag else ""
    thin = [f"共 {len(lines)} 次观测（{lines[0].date} 至 {last.date}）。" if len(lines) > 1 else "仅 1 次观测。",
            f"最新一次 {last.date}：{_measure(last)}{flag} {anchors[-1]}。"]
    if len(lines) > 1:
        thin.append(_trend(last, lines[-2]))
    else:
        thin.append("无趋势可述。")
    thin.append("（只描述数值，不作临床解读。）")
    full = [f"- {ln.date}：{_measure(ln)}{(' ' + ln.flag) if ln.flag else ''} {a}" for ln, a in zip(lines, anchors)]
    return _render(aliases, f"{name}（{cid}）", "".join(thin), "逐次观测", full)


def _record_card(cid: str, text: str, lines: list[_Line], aliases: list[str]) -> str:
    anchors = _anchors(text, lines)
    date = next((ln.date for ln in lines if ln.date), "")
    obs = [ln for ln in lines if ln.kind == "observation"]
    names = "、".join(dict.fromkeys(ln.display for ln in obs))
    held = f"共 {len(obs)} 项观测：{names}。" if obs else "没有观测行。"
    thin = f"单据 {cid}，日期 {date}，{held} {anchors[0]}"
    full = [f"- {ln.base} {a}" for ln, a in zip(lines, anchors)]
    return _render(aliases, f"单据 {cid}（{date}）", thin, "逐行原文", full)


def _render(aliases: list[str], title: str, thin: str, section: str, full: list[str]) -> str:
    return (f"aliases: [{', '.join(aliases)}]\nkind: {KIND}\n\n# {title}\n\n## Thin\n{thin}\n\n## Full\n"
            f"### {section}\n" + "".join(b + "\n" for b in full))


# --------------------------------------------------------------------------- public


def _min_aliases(min_aliases: int | None) -> int:
    return MIN_ALIASES_DETERMINISTIC if min_aliases is None else min_aliases


def generate(build: str | os.PathLike, *, min_aliases: int | None = None,
             table: str | os.PathLike | None = None) -> dict:
    """The cards of the export under ``build`` (see the module docstring)."""
    build = Path(build)
    need = _min_aliases(min_aliases)
    tab = load_table(table)
    sources, _index = _read_export(build)
    resolve = items_mod.default().resolve
    cards: dict[str, str] = {}
    aliases: dict[str, list[str]] = {}
    for cid, (shelf, text, lines) in sources.items():
        al = _Aliases()
        if shelf == ITEMS_SHELF:
            al.add(*_item_names(cid, lines[0].display or cid, tab))
            for unit in dict.fromkeys(ln.unit for ln in lines if ln.unit):
                if any(ch.isalnum() for ch in unit):
                    al.add(unit, *tab["units"].get(unit, []))
        else:
            al.add(cid, *_date_forms(next((ln.date for ln in lines if ln.date), "")))
            for ln in lines:
                if ln.kind == "observation":
                    al.add(*_item_names(items_mod.file_stem(resolve(ln.display)[0]), ln.display, tab))
        aliases[cid] = al.out
    short = {cid: len(a) for cid, a in aliases.items() if len(a) < need}
    if short:
        raise AliasShortage(short, need)
    for cid, (shelf, text, lines) in sources.items():
        render = _item_card if shelf == ITEMS_SHELF else _record_card
        cards[cid] = render(cid, text, lines, aliases[cid])
    return {"cards": dict(sorted(cards.items())), "aliases": dict(sorted(aliases.items()))}


def write(build: str | os.PathLike, *, min_aliases: int | None = None,
          table: str | os.PathLike | None = None) -> dict:
    """Generate, then write ``cards_content/<id>.md`` (see the module docstring)."""
    build = Path(build)
    cards = generate(build, min_aliases=min_aliases, table=table)["cards"]
    out_dir = build / CARDS_DIR
    written, unchanged, refused = [], [], []
    for cid, text in cards.items():
        path = out_dir / f"{cid}.md"
        if path.is_file() or path.is_symlink():
            (unchanged if path.is_file() and path.read_bytes() == text.encode("utf-8") else refused).append(cid)
        else:
            written.append(cid)
    if refused:
        raise L1Error(f"{out_dir}: refusing to overwrite differing card(s): {', '.join(refused)}")
    for cid in written:
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / f"{cid}.md").open("x", encoding="utf-8") as fh:
            fh.write(cards[cid])
    return {"written": written, "unchanged": unchanged, "bibliography_added": _bibliography(build, cards)}


def _bibliography(build: Path, cards: Mapping[str, str]) -> list[str]:
    bib = build / BIBLIOGRAPHY
    if not bib.is_file():
        return []
    listed = bib.read_text(encoding="utf-8")
    rows, added = [], []
    for shelf, label in ((ITEMS_SHELF, "检验项目时间序列"), (RECORDS_SHELF, "已核对单据")):
        for path in sorted((build / shelf).glob("*.txt")):
            cell = f"`{shelf}/{path.name}`"
            if path.stem in cards and cell not in listed:
                rows.append(f"| {label} — {path.stem} | {cell} | records_to_txt 确定性导出 |")
                added.append(path.stem)
    if rows:
        with bib.open("a", encoding="utf-8") as fh:
            fh.write(("" if listed.endswith("\n") else "\n") + "\n".join(rows) + "\n")
    return added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit records-to-l1", description=__doc__.splitlines()[0])
    parser.add_argument("kb", type=Path, help="build directory holding the WI-01a export")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        res = write(args.kb)
    except AliasShortage as exc:
        print(f"records-to-l1: {exc} - nothing written; add deterministic aliases (items.yaml / "
              f"card_aliases.toml) or decide otherwise", file=sys.stderr)
        return 1
    except L1Error as exc:
        print(f"records-to-l1: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    else:
        for cid in res["written"]:
            print(f"  wrote {CARDS_DIR}/{cid}.md")
        print(f"records-to-l1: {len(res['written'])} written, {len(res['unchanged'])} unchanged, "
              f"{len(res['bibliography_added'])} bibliography row(s) added")
    return 0
