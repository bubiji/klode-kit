"""Chapter manifest generator (WI-03a; design 02 r2.4 ``gen_manifest``, r3.4; baseline 03 §6.1; SKILL.md steps 3-5).

The original klode-build steps 3-4 were manual: run ``split_by_manifest.py --scan``, read the candidate line
numbers (the table of contents lists every chapter once before the body does - take the body occurrence; a
Part/Section title page joins the chapter after it), and hand-write ``src/chapters-<shelf>.json``. This module
writes that manifest from the same scan (``split_by_manifest.candidates`` with the configured ``head_re.toml``,
Chinese heads included) and runs the same self-check before anything is written.

- ``heads(text, patterns=None)``: the scan candidates minus the table of contents - a head occurring more than
  once (compared without TOC dot leaders / tab or wide-gap page numbers) loses its first occurrence; a head whose
  piece would hold nothing but the head and blank lines is merged into the next one (earlier line number, title
  ``"<earlier> / <next>"``).
- ``gen_manifest(kb, shelf=..., ...)``:
  * fewer than 2 heads: the whole is one source (``<prefix>-whole``, from line 1) and the run goes on;
  * otherwise one piece per head, ``<prefix>-chNN[-slug]``, plus ``<prefix>-ch00-front`` from line 1 when
    non-blank text comes before the first head (leading blank lines join the first chapter);
  * an explicit ``manifest`` (U7) is used as given, no scan;
  * self-check before any write: first piece at line 1, no empty piece, no piece past the end, ids plain and
    unique, pieces put back == the whole byte for byte - otherwise ``ManifestError`` and nothing is written;
  * writes ``src/chapters-<shelf>.json`` (never overwriting a different existing one - pass it as ``--manifest``
    instead; an equal one is left untouched); ``apply=True`` then runs ``split_by_manifest.split(apply=True)``.
- ``prefix`` defaults to ``<library id>-<shelf>`` (``[library].id`` of ``library.toml``, else a slug of the
  directory name); ``citation`` to ``[library].name``, else the shelf.

    klode-kit gen-manifest KB --shelf S [--whole F] [--manifest M] [--prefix P] [--citation C] [--head-re T] [--apply]
    exit 0 done, 1 refused (nothing written), 2 usage, 40 head pattern file broken
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

from klode_kit.build import split_by_manifest as S
from klode_kit.config import ConfigError

TOC_PAGE_RE = re.compile(r"(?:\s{2,}|\t+|\s*[.·…]{2,}\s*)\d+\s*$")
SLUG_WORDS = 4
SLUG_MAX = 40
SKIP_SLUG = {"chapter", "part", "section"}


class ManifestError(Exception):
    """The manifest cannot be generated or does not put back to the whole; nothing was written."""


def _key(head: str) -> str:
    return " ".join(TOC_PAGE_RE.sub("", head).split())


def heads(text: str, patterns: list[re.Pattern] | None = None) -> list[tuple[int, str]]:
    """Chapter heads ``[(line_no, title)]`` of ``text``: scan candidates without the TOC, empty pieces merged."""
    cands = S.candidates(text, S.load_head_patterns() if patterns is None else patterns)
    count: dict[str, int] = {}
    for _, h in cands:
        count[_key(h)] = count.get(_key(h), 0) + 1
    seen: set[str] = set()
    body: list[tuple[int, str]] = []
    for n, h in cands:
        k = _key(h)
        if count[k] > 1 and k not in seen:  # first of several: the TOC entry
            seen.add(k)
            continue
        body.append((n, _key(h)))
    lines = S.lines_of(text)
    out: list[tuple[int, str]] = []
    for n, h in body:
        if out and not any(lines[i].strip() for i in range(out[-1][0], n - 1)):
            out[-1] = (out[-1][0], f"{out[-1][1]} / {h}")
        else:
            out.append((n, h))
    return out


def _slug(title: str) -> str:
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in SKIP_SLUG and not w.isdigit()]
    return "-".join(words[:SLUG_WORDS])[:SLUG_MAX].strip("-")


def _library(kb: Path) -> dict[str, Any]:
    toml = kb / "library.toml"
    if not toml.exists():
        return {}
    try:
        return tomllib.loads(toml.read_text(encoding="utf-8")).get("library", {})
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"读不了 {toml}：{exc}") from exc


def _cut(text: str, pieces: list[dict[str, Any]]) -> list[str]:
    """The pieces' bodies; raises ``ManifestError`` unless they put back to ``text`` exactly."""
    if not pieces:
        raise ManifestError("清单里没有任何片")
    for p in pieces:
        if not isinstance(p, dict) or not isinstance(p.get("id"), str) or isinstance(p.get("start_line"), bool) \
                or not isinstance(p.get("start_line"), int):
            raise ManifestError(f"片必须有字符串 id 与整数 start_line：{p!r}")
        if not S.NAME_RE.match(p["id"]):
            raise ManifestError(f"片 id 必须是普通名字（字母数字 . _ -，不含路径）：{p['id']!r}")
    ids = [p["id"] for p in pieces]
    if len(ids) != len(set(ids)):
        raise ManifestError("清单里有重复 id")
    lines = S.lines_of(text)
    if not lines:
        raise ManifestError("整本源文件是空的")
    if pieces[0]["start_line"] != 1:
        raise ManifestError("自校验失败：第一片必须从第 1 行开始（前面的行会丢失），未写入")
    bodies = []
    for k, p in enumerate(pieces):
        a = p["start_line"] - 1
        b = pieces[k + 1]["start_line"] - 1 if k + 1 < len(pieces) else len(lines)
        if a >= len(lines):
            raise ManifestError(f"自校验失败：{p['id']} 起始行 {a + 1} 超出整本 {len(lines)} 行，未写入")
        if a >= b:
            raise ManifestError(f"自校验失败：{p['id']} 是空片（起始行 {a + 1}，下一片 {b + 1}），未写入")
        bodies.append("".join(lines[a:b]))
    if "".join(bodies) != text:  # pragma: no cover - the checks above make the cut a partition
        raise ManifestError("自校验失败：切片拼回与原文不一致，未写入")
    return bodies


def _auto_pieces(text: str, hs: list[tuple[int, str]], prefix: str) -> tuple[str, list[dict[str, Any]]]:
    lines = S.lines_of(text)
    if len(hs) < 2:
        title = next((ln.strip() for ln in lines if ln.strip()), "Whole text")[:S.MAX_HEAD_LEN]
        return "single", [{"id": f"{prefix}-whole", "title": title, "start_line": 1}]
    pieces: list[dict[str, Any]] = []
    first = hs[0][0]
    if first > 1 and any(lines[i].strip() for i in range(first - 1)):
        pieces.append({"id": f"{prefix}-ch00-front", "title": "Front Matter", "start_line": 1})
    else:
        hs = [(1, hs[0][1]), *hs[1:]]
    for k, (n, title) in enumerate(hs, 1):  # the chNN number alone makes every id unique
        pid = f"{prefix}-ch{k:02d}" + (f"-{s}" if (s := _slug(title)) else "")
        pieces.append({"id": pid, "title": title, "start_line": n})
    return "auto", pieces


def _inside(kb: Path, rel: str) -> Path:
    p = (kb / rel).resolve()
    try:
        p.relative_to(kb)
    except ValueError:
        raise ManifestError(f"整本路径必须在库内：{rel}") from None
    return p


def gen_manifest(kb: str | Path, *, shelf: str | None = None, whole: str | None = None,
                 manifest: str | Path | None = None, prefix: str | None = None, citation: str | None = None,
                 head_re: str | Path | None = None, apply: bool = False) -> dict[str, Any]:
    """Write ``src/chapters-<shelf>.json`` (and split with ``apply``); returns ``{"mode", "path", "manifest"}``."""
    kb = Path(kb).resolve()
    explicit_path: Path | None = None
    explicit_text = ""
    if manifest is not None:
        explicit_path = Path(manifest) if Path(manifest).is_absolute() else kb / manifest
        try:
            explicit_text = explicit_path.read_text(encoding="utf-8")
            m = json.loads(explicit_text)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestError(f"读不了显式清单 {manifest}：{exc}") from exc
        if not isinstance(m, dict) or not isinstance(m.get("shelf"), str) or not isinstance(m.get("whole"), str) \
                or not isinstance(m.get("pieces"), list):
            raise ManifestError("显式清单必须有 shelf、whole（字符串）与 pieces（列表）")
        if shelf is not None and m["shelf"] != shelf:
            raise ManifestError(f"显式清单的书架 {m['shelf']!r} 与 --shelf {shelf!r} 不一致")
        shelf, whole_rel = m["shelf"], m["whole"]
    elif shelf is None:
        raise ValueError("需要 --shelf 或 --manifest")
    else:
        whole_rel = whole or f"library/{shelf}/_whole-{shelf}.txt"
    if not S.NAME_RE.match(shelf):
        raise ManifestError(f"书架名必须是普通名字（字母数字 . _ -）：{shelf!r}")
    whole_path = _inside(kb, whole_rel)
    if not whole_path.is_file():
        raise ManifestError(f"找不到整本源文件：{whole_path}（先 klode ingest --id _whole-{shelf}）")
    try:
        text = S._read(whole_path)
    except UnicodeDecodeError as exc:
        raise ManifestError(f"整本源文件不是 UTF-8：{exc}") from exc

    if explicit_path is not None:
        mode = "explicit"
        pieces = sorted(m["pieces"], key=lambda p: p.get("start_line", 0) if isinstance(p, dict) else 0)
        _cut(text, pieces)
        content = explicit_text
    else:
        lib = _library(kb)
        if prefix is None:
            kb_id = lib.get("id") if isinstance(lib.get("id"), str) else None
            kb_id = kb_id or "-".join(re.findall(r"[a-z0-9]+", kb.name.lower())) or "kb"
            prefix = f"{kb_id}-{shelf}"
        if not S.NAME_RE.match(prefix):
            raise ManifestError(f"id 前缀必须是普通名字（字母数字 . _ -）：{prefix!r}")
        hs = heads(text, None if head_re is None else S.load_head_patterns(head_re))
        mode, pieces = _auto_pieces(text, hs, prefix)
        _cut(text, pieces)
        name = lib.get("name") if isinstance(lib.get("name"), str) else ""
        m = {"shelf": shelf, "whole": whole_rel, "citation": citation or name or shelf, "pieces": pieces,
             "generated_by": {"tool": "klode-kit gen-manifest", "mode": mode, "heads": len(hs)}}
        content = json.dumps(m, ensure_ascii=False, indent=2) + "\n"

    out = kb / "src" / f"chapters-{shelf}.json"
    same_file = explicit_path is not None and explicit_path.resolve() == out.resolve()
    if not same_file and out.exists() and out.read_text(encoding="utf-8") != content:
        raise ManifestError(f"{out} 已存在且与本次结果不同，不覆盖：要用它就加 --manifest {out.relative_to(kb)}，"
                            "否则先把它移走（_trash/）")
    kept = kb / "_raw" / whole_path.name
    if apply and kept.exists() and kept.read_bytes() != whole_path.read_bytes():
        raise ManifestError(f"{kept} 已有内容不同的同名整本，拒绝覆盖；未写入")

    if not same_file and not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="") as fh:
            fh.write(content)
    if apply:
        rc = S.split(kb, out, apply=True)
        if rc != 0:  # pragma: no cover - every refusal of split is checked above
            raise ManifestError(f"切章失败（exit {rc}）")
    return {"mode": mode, "path": out, "manifest": m}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit gen-manifest", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kb", type=Path, help="库根目录（含 library.toml）")
    ap.add_argument("--shelf", help="书架名")
    ap.add_argument("--whole", help="整本文本（相对库根；默认 library/<架>/_whole-<架>.txt）")
    ap.add_argument("--manifest", type=Path, help="显式清单（U7；相对库根或绝对），不扫描")
    ap.add_argument("--prefix", help="片 id 前缀（默认 <库id>-<架>）")
    ap.add_argument("--citation", help="BIBLIOGRAPHY 引用（默认库名）")
    ap.add_argument("--head-re", type=Path, help="章头正则文件（TOML，patterns = [...]），替换内置 head_re.toml")
    ap.add_argument("--apply", action="store_true", help="写清单后切章")
    try:
        a = ap.parse_args(argv)
        if a.shelf is None and a.manifest is None:
            ap.error("需要 --shelf 或 --manifest")
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        res = gen_manifest(a.kb, shelf=a.shelf, whole=a.whole, manifest=a.manifest, prefix=a.prefix,
                           citation=a.citation, head_re=a.head_re, apply=a.apply)
    except ConfigError as exc:
        print(f"gen-manifest: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code
    except ManifestError as exc:
        print(f"gen-manifest: 拒绝（未写入）：{exc}", file=sys.stderr)
        return 1
    pieces = res["manifest"]["pieces"]
    print(f"gen-manifest: {res['mode']}  {len(pieces)} 片 -> {res['path']}")
    for p in pieces:
        print(f"  {p['start_line']:>6}  {p['id']:<40} {p.get('title', '')}")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit gen-manifest``
    raise SystemExit(main())
