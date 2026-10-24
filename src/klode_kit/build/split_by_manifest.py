"""Cut a whole ingested text into one source per chapter by a chapter manifest (WI-02a; vendored).

Vendored from the user's klode-build skill ``scripts/split_by_manifest.py`` (read-only source, 2026-10-24).
Kept: deterministic, verbatim cutting at the manifest's line numbers; the self-check "pieces put back == whole"
before anything is written; the first piece must start at line 1; dry run by default, ``--apply`` writes.
Changed (design 02 §3.1 A1, §3.3 B5; baseline 03 G6, B5):

- ``HEAD_RE`` lives in configuration: ``klode_kit/config/head_re.toml`` (``patterns = [...]``), the original
  English alternatives plus Chinese heads (「第一章」「一、」「检验报告」); ``--head-re FILE`` replaces the list.
  A broken file is ``ConfigError`` (exit 40).
- B5: ``--apply`` adds a BIBLIOGRAPHY row only for a ``library/<shelf>/<id>.txt`` not already listed, so a second
  apply (the whole put back) leaves BIBLIOGRAPHY unchanged.
- Byte exact: the whole is read and written without newline translation and lines end at ``\\n`` only (as
  ``grep -n`` counts them); the original's text-mode read turned CRLF/CR into LF, so the pieces of a CRLF
  whole did not put back to its bytes.
- Guards before any write: shelf and piece ids are plain names (no path), and an existing ``_raw/<whole>`` with
  different bytes is never overwritten (exit 1).

Two steps::

    # 1 scan: print candidate heads (line number + text); write src/chapters-<shelf>.json from them
    klode-kit split-chapters <kb_dir> --whole library/<shelf>/_whole-x.txt --scan [--head-re FILE]

    # 2 split: dry run (default) -> with --apply write library/<shelf>/<id>.txt, move the whole into _raw/,
    #   add one row per new source to library/BIBLIOGRAPHY.md
    klode-kit split-chapters <kb_dir> --manifest src/chapters-<shelf>.json [--apply]

chapters.json::

    {"shelf": "christensen", "whole": "library/christensen/_whole-christensen.txt",
     "citation": "Christensen et al., *Competing Against Luck* (2016)",
     "pieces": [{"id": "jtbd-cc-ch00-front", "title": "Front Matter", "start_line": 1, "notes": "..."},
                {"id": "jtbd-cc-ch01-milk-shake", "title": "Chapter 1: The Milk Shake Dilemma", "start_line": 244}]}

``start_line`` is 1-based; the first piece starts at line 1; each piece runs to the line before the next one.
Exit codes: 0 done, 1 refused (nothing written), 2 usage, 40 configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tomllib
from importlib import resources
from pathlib import Path

from klode_kit.config import ConfigError

HEAD_RE_FILE = "head_re.toml"
MAX_HEAD_LEN = 80
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
BIB_HEADER = "# Bibliography\n\n| source | file | notes |\n|---|---|---|\n"


def load_head_patterns(path: str | Path | None = None) -> list[re.Pattern]:
    """The head patterns of ``path`` (default: the packaged ``config/head_re.toml``)."""
    if path is None:
        path = Path(str(resources.files("klode_kit.config") / HEAD_RE_FILE))
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read head pattern file {path}: {exc}", "patterns") from exc
    pats = data.get("patterns")
    if not isinstance(pats, list) or not pats or not all(isinstance(p, str) for p in pats):
        raise ConfigError(f"{path}: patterns must be a non-empty list of regular expressions", "patterns")
    out = []
    for p in pats:
        try:
            out.append(re.compile(p))
        except re.error as exc:
            raise ConfigError(f"{path}: invalid pattern {p!r}: {exc}", "patterns") from exc
    return out


def lines_of(text: str) -> list[str]:
    """Lines ending at ``\\n`` (kept), the last one possibly without it; ``"".join(lines_of(t)) == t``."""
    return re.findall(r"[^\n]*\n|[^\n]+$", text)


def candidates(text: str, patterns: list[re.Pattern], maxlen: int = MAX_HEAD_LEN) -> list[tuple[int, str]]:
    out = []
    for i, line in enumerate(lines_of(text), 1):
        s = line.rstrip()
        if 0 < len(s) <= maxlen and any(p.match(s) for p in patterns):
            out.append((i, s))
    return out


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as fh:
        return fh.read()


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)


def scan(whole: Path, patterns: list[re.Pattern] | None = None) -> list[tuple[int, str]]:
    text = _read(whole)
    found = candidates(text, load_head_patterns() if patterns is None else patterns)
    print(f"# {whole}  共 {len(lines_of(text))} 行；候选章头（行号\t内容）——TOC 区会重复出现一次，正文章头看行距")
    for i, s in found:
        print(f"{i}\t{s}")
    return found


def _refuse(msg: str) -> int:
    print(msg, file=sys.stderr)
    return 1


def split(kb: Path, manifest: Path, apply: bool) -> int:
    m = json.loads(manifest.read_text(encoding="utf-8"))
    shelf, whole = m["shelf"], kb / m["whole"]
    pieces = sorted(m["pieces"], key=lambda p: p["start_line"])
    if not isinstance(shelf, str) or not NAME_RE.match(shelf):
        return _refuse(f"书架名必须是普通名字（字母数字 . _ -）：{shelf!r}")
    bad = [p["id"] for p in pieces if not isinstance(p["id"], str) or not NAME_RE.match(p["id"])]
    if bad:
        return _refuse(f"片 id 必须是普通名字（字母数字 . _ -，不含路径）：{bad[0]!r}")
    if not whole.exists():
        return _refuse(f"找不到整本源文件：{whole}")
    if pieces[0]["start_line"] != 1:
        return _refuse("第一片必须从第 1 行开始（前置页也要成为一源，否则拼回校验不通过）")
    ids = [p["id"] for p in pieces]
    if len(ids) != len(set(ids)):
        return _refuse("清单里有重复 id")

    text = _read(whole)
    lines = lines_of(text)
    out: list[tuple[dict, str]] = []
    for k, p in enumerate(pieces):
        a = p["start_line"] - 1
        b = pieces[k + 1]["start_line"] - 1 if k + 1 < len(pieces) else len(lines)
        if a >= b:
            return _refuse(f"{p['id']}: 起始行 {a+1} 不小于下一片起始行 {b+1}")
        out.append((p, "".join(lines[a:b])))

    if "".join(body for _, body in out) != text:  # pragma: no cover - lines_of is a partition by construction
        return _refuse("自校验失败：切片拼回与原文不一致，未写入。")
    print(f"自校验通过：{len(out)} 片拼回 == 原文 (sha256 {hashlib.sha256(text.encode()).hexdigest()[:16]})")

    raw = kb / "_raw"
    kept = raw / whole.name
    if apply and kept.exists() and kept.read_bytes() != whole.read_bytes():
        return _refuse(f"{kept} 已有内容不同的同名整本，拒绝覆盖（先把它移走再切）；未写入。")

    shelf_dir = kb / "library" / shelf
    for p, body in out:
        print(f"  {p['id']:<40} {len(body.split()):>7,} words  {p['title']}")
        if apply:
            _write(shelf_dir / f"{p['id']}.txt", body)

    if not apply:
        print("\n预演结束，加 --apply 才写入。")
        return 0

    raw.mkdir(exist_ok=True)
    shutil.move(str(whole), str(kept))
    print(f"整本原件已移入 {kept}")

    bib = kb / "library" / "BIBLIOGRAPHY.md"
    cite = m.get("citation", shelf)
    if not bib.exists():
        bib.write_text(BIB_HEADER, encoding="utf-8")
    listed = bib.read_text(encoding="utf-8")
    rows, skipped = [], 0
    for p, _ in out:
        cell = f"`library/{shelf}/{p['id']}.txt`"
        if cell in listed:
            skipped += 1
            continue
        rows.append(f"| {cite} — {p['title']} | {cell} | {p.get('notes', '')} |")
    if rows:
        with bib.open("a", encoding="utf-8") as f:
            f.write(("" if listed.endswith("\n") else "\n") + "\n".join(rows) + "\n")
    print(f"BIBLIOGRAPHY.md 追加 {len(rows)} 行" + (f"，已有 {skipped} 行未重复追加" if skipped else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit split-chapters", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kb", type=Path, help="库根目录（含 library.toml）")
    ap.add_argument("--whole", type=Path, help="--scan 用：整本文本（相对库根）")
    ap.add_argument("--scan", action="store_true", help="只打印候选章头")
    ap.add_argument("--head-re", type=Path, help="章头正则文件（TOML，patterns = [...]），替换内置 head_re.toml")
    ap.add_argument("--manifest", type=Path, help="chapters.json（相对库根或绝对）")
    ap.add_argument("--apply", action="store_true", help="写入文件（默认只预演）")
    try:
        a = ap.parse_args(argv)
        kb = a.kb.resolve()
        if a.scan and not a.whole:
            ap.error("--scan 需要 --whole")
        if not a.scan and not a.manifest:
            ap.error("需要 --manifest 或 --scan")
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if a.scan:
            scan(kb / a.whole, load_head_patterns(a.head_re))
            return 0
    except ConfigError as exc:
        print(f"split-chapters: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code
    mf = a.manifest if a.manifest.is_absolute() else kb / a.manifest
    return split(kb, mf, a.apply)


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit split-chapters``
    raise SystemExit(main())
