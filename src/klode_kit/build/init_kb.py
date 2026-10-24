"""Library skeleton on top of ``klode init`` (WI-03a; baseline 03 G11, G14; original klode-build SKILL.md step 1).

The original step 1 was manual: copy a sample ``library.toml``, edit ``id/name/description/shelves``, copy its
``.gitignore`` and ``mkdir library/<shelf> library/cards cards_content src _raw``; ``klode init`` alone writes no
``id``/``name``/``description`` (G11) and nothing ignores ``_work/`` or ``_raw/`` (G14). ``init_kb`` does that step:

- ``library.toml``: absent -> klode 0.5.0's own ``init`` template plus ``id``/``name``/``description``; present
  (normally just written by ``klode init``) -> only the missing lines are inserted, every existing line is kept.
  Afterwards ``[library]`` holds ``id`` (a klode slug), ``name``, ``description`` and every requested shelf, and
  ``[copyright] guard = true``.
- directories ``library/<shelf>``, ``library/cards``, ``cards_content``, ``src``, ``_raw``; a
  ``library/BIBLIOGRAPHY.md`` header (klode's) when missing.
- ``.gitignore``: the user's lines are kept byte for byte; appended once each: klode's managed block
  (``library/<shelf>/*.txt`` / ``*.pdf`` - the sources stay local, as in the original template; a block that is
  already there only gains the missing shelf lines) and ``_raw/``, ``_work/``, ``_trash/``.
- Idempotent: a file is written only when its content changes, so a second run writes nothing.

Refusals never overwrite the user's values: an existing ``id``/``name``/``description`` that differs from the
request, or ``[copyright] guard = false``, stops the run before any write (exit 1). Invalid input (id not a slug,
shelf not a plain name, a line break in name/description) is exit 2.

    klode-kit init-kb KB --id ID --shelf S [--shelf S ...] [--name N] [--description D]
    exit 0 done (or nothing to do), 1 refused (nothing written), 2 usage
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Sequence

SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")  # klode 0.5.0 config._SLUG_RE
SHELF_RE = re.compile(r"[A-Za-z0-9._-]+")          # klode init's shelf rule (one path component)
DIRS = ("library/cards", "cards_content", "src", "_raw")
GI_BEGIN = "# >>> klode managed: the corpus is copyrighted — sources stay local, cards are tracked"
GI_END = "# <<< klode managed"
KIT_COMMENT = "# klode-kit: whole-book extracts, desk worker output and moved-away files stay local"
KIT_IGNORES = ("_raw/", "_work/", "_trash/")
BIB_HEADER = "# Bibliography\n\n| source | file | notes |\n|---|---|---|\n"

# klode 0.5.0 ``klode init`` template (lib/cli.py ``_TOML_TEMPLATE``) with the identity lines added.
TOML_TEMPLATE = """\
# library.toml — a klode knowledge library.
# One card per source, exposed at four Levels of Zoom (L0 meta / L1 thin / L2 full / L3 content).
# See SPEC.md for the card format and the two disciplines that keep it from rotting.

[library]
{identity}dir = "library"          # library root, relative to this file
cards = "cards"          # cards subdir, relative to the library dir
shelves = [{shelves}]    # the taxonomy: subdirs of the library dir holding source .txt files

[bibliography]
enabled = true
path = "BIBLIOGRAPHY.md"  # relative to the library dir

[frameworks]              # optional per-dimension distillation layer
enabled = false
dir = "frameworks"
syntheses = "_syntheses"

[copyright]
guard = true              # fail `klode check` if any guarded .txt/.pdf is git-tracked
extra_guard_dirs = []     # extra dirs (relative to the library dir) to also guard

[normalize]
backup_dir = ""           # empty => system temp dir (kept OUTSIDE the tree)
backup_keep = 3
dict_path = "/usr/share/dict/words"
"""


class InitRefused(Exception):
    """The existing library disagrees with the request; nothing was written."""


def _q(value: str) -> str:
    """A TOML basic string (JSON escaping is a subset TOML accepts)."""
    return json.dumps(value, ensure_ascii=False)


def _validate(kb_id: str, shelves: Sequence[str], name: str, description: str) -> list[str]:
    if not isinstance(kb_id, str) or not SLUG_RE.fullmatch(kb_id):
        raise ValueError(f"库 id 必须是 slug（小写字母、数字、单个连字符）：{kb_id!r}")
    shelves = list(shelves)
    if not shelves:
        raise ValueError("至少要一个书架（--shelf）")
    for s in shelves:
        if not isinstance(s, str) or s in (".", "..") or not SHELF_RE.fullmatch(s):
            raise ValueError(f"书架名必须是普通名字（字母数字 . _ -，不含路径）：{s!r}")
    for label, v in (("name", name), ("description", description)):
        if not isinstance(v, str) or any(ord(c) < 32 or ord(c) == 127 for c in v):
            raise ValueError(f"{label} 必须是单行文字（不含换行或控制字符）")
    return list(dict.fromkeys(shelves))


def _section_span(lines: list[str], name: str) -> tuple[int, int] | None:
    """``(header index, end index)`` of ``[name]`` in ``lines`` (end = next table header or len)."""
    head = re.compile(rf"^\s*\[\s*{re.escape(name)}\s*\]\s*(#.*)?$")
    start = next((i for i, ln in enumerate(lines) if head.match(ln)), None)
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if re.match(r"^\s*\[", lines[j])), len(lines))
    return start, end


def _patched_toml(text: str, kb_id: str, shelves: list[str], name: str, description: str) -> str:
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise InitRefused(f"library.toml 不是合法 TOML：{exc}") from exc
    lib = cfg.get("library", {})
    want = {"id": kb_id, "name": name, "description": description}
    for key, value in want.items():
        if key in lib and lib[key] != value:
            raise InitRefused(f"library.toml 已有 [library].{key} = {lib[key]!r}，与请求的 {value!r} 不同；"
                              "不覆盖（先手动改 toml 或换参数）")
    guard = cfg.get("copyright", {}).get("guard", None)
    if guard is False:
        raise InitRefused("library.toml 的 [copyright] guard = false；源文本有版权/隐私，需要 guard = true（手动改回后再跑）")
    have = lib.get("shelves", [])
    if not isinstance(have, list):
        raise InitRefused("library.toml 的 [library].shelves 不是列表")

    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    missing = [s for s in shelves if s not in have]
    span = _section_span([ln.rstrip("\n") for ln in lines], "library")
    if span is None:
        if have or "library" in cfg:
            raise InitRefused("library.toml 的 [library] 表无法定位（内联写法？）；请手动补字段")
        lines = [f"[library]\n", 'dir = "library"\ncards = "cards"\n',
                 f"shelves = [{', '.join(_q(s) for s in shelves)}]\n", "\n"] + lines
        span = (0, 4)
        missing = []
    elif missing:
        idx = next((i for i in range(span[0] + 1, span[1]) if re.match(r"^\s*shelves\s*=", lines[i])), None)
        if idx is None:
            lines.insert(span[1], f"shelves = [{', '.join(_q(s) for s in shelves)}]\n")
        else:
            m = re.match(r"^(\s*shelves\s*=\s*)\[([^\]\n]*)\](.*)$", lines[idx].rstrip("\n"))
            if not m:
                raise InitRefused("library.toml 的 shelves 不是单行数组，无法安全追加书架；请手动补上 "
                                  + ", ".join(missing))
            merged = ", ".join(_q(s) for s in [*have, *missing])
            lines[idx] = f"{m.group(1)}[{merged}]{m.group(3)}\n"
    identity = [f"{k} = {_q(v)}\n" for k, v in want.items() if k not in lib]
    lines[span[0] + 1:span[0] + 1] = identity
    if guard is None:
        cspan = _section_span([ln.rstrip("\n") for ln in lines], "copyright")
        if cspan is None:
            lines += ["\n", "[copyright]\n", "guard = true\n", "extra_guard_dirs = []\n"]
        else:
            lines.insert(cspan[0] + 1, "guard = true\n")
    out = "".join(lines)
    check = tomllib.loads(out)  # self-check: what klode will read
    assert all(check["library"].get(k) == v for k, v in want.items())
    assert all(s in check["library"]["shelves"] for s in shelves) and check["copyright"]["guard"] is True
    return out


def _norm_ignore(line: str) -> str:
    s = line.strip()
    return s.lstrip("/").rstrip("/") if s and not s.startswith("#") else s


def _patched_gitignore(text: str, shelves: list[str]) -> str:
    managed = [f"library/{s}/*{ext}" for s in shelves for ext in (".txt", ".pdf")]
    if GI_BEGIN in text and GI_END in text:
        head, rest = text.split(GI_BEGIN, 1)
        block, tail = rest.split(GI_END, 1)
        present = {_norm_ignore(ln) for ln in text.splitlines()}
        extra = [ln for ln in managed if _norm_ignore(ln) not in present]
        if extra:
            body = block.rstrip("\n")
            text = head + GI_BEGIN + body + "\n" + "\n".join(extra) + "\n" + GI_END + tail
        managed_add: list[str] = []
    else:
        managed_add = managed
    present = {_norm_ignore(ln) for ln in text.splitlines()}
    kit_add = [ln for ln in KIT_IGNORES if _norm_ignore(ln) not in present]
    managed_add = [ln for ln in managed_add if _norm_ignore(ln) not in present]
    chunks = []
    if managed_add:
        chunks.append("\n".join([GI_BEGIN, *managed_add, GI_END]) + "\n")
    if kit_add:
        chunks.append("\n".join([KIT_COMMENT, *kit_add]) + "\n")
    if not chunks:
        return text
    if text and not text.endswith("\n"):
        text += "\n"
    sep = "\n" if text.strip() else ""
    return text + sep + "\n".join(chunks)


def _write_if_changed(path: Path, content: str, changed: list[str], kb: Path) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    changed.append(path.relative_to(kb).as_posix())
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(content)


def init_kb(kb: str | Path, kb_id: str, *, shelves: Sequence[str], name: str = "",
            description: str = "") -> dict[str, Any]:
    """Make ``kb`` a library the build can use; returns ``{"changed": [relative paths written or created]}``."""
    shelves = _validate(kb_id, shelves, name, description)
    kb = Path(kb)
    toml_path, gi_path = kb / "library.toml", kb / ".gitignore"
    for p in (kb, toml_path, gi_path, kb / "library", *(kb / d for d in DIRS)):
        if p.is_symlink():
            raise InitRefused(f"拒绝经由符号链接写入：{p}")

    # Compute everything first: a refusal leaves the tree untouched.
    if toml_path.exists():
        toml_text = _patched_toml(toml_path.read_text(encoding="utf-8"), kb_id, shelves, name, description)
    else:
        identity = "".join(f"{k} = {_q(v)}\n" for k, v in (("id", kb_id), ("name", name),
                                                           ("description", description)))
        toml_text = TOML_TEMPLATE.format(identity=identity, shelves=", ".join(_q(s) for s in shelves))
    gi_old = gi_path.read_text(encoding="utf-8") if gi_path.exists() else ""
    gi_text = _patched_gitignore(gi_old, shelves)

    changed: list[str] = []
    if not kb.exists():
        kb.mkdir(parents=True)
        changed.append(".")
    for d in (*DIRS, *(f"library/{s}" for s in shelves)):
        p = kb / d
        if not p.is_dir():
            p.mkdir(parents=True)
            changed.append(d + "/")
    _write_if_changed(toml_path, toml_text, changed, kb)
    if gi_text != gi_old or not gi_path.exists():
        _write_if_changed(gi_path, gi_text, changed, kb)
    bib = kb / "library" / "BIBLIOGRAPHY.md"
    if not bib.exists():
        _write_if_changed(bib, BIB_HEADER, changed, kb)
    return {"changed": changed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit init-kb", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kb", type=Path, help="库根目录（可先用 klode init 建好，也可以是空目录）")
    ap.add_argument("--id", required=True, dest="kb_id", help="库 id（slug，klode --kb 用）")
    ap.add_argument("--shelf", action="append", required=True, help="书架名（可重复）")
    ap.add_argument("--name", default="", help="库名（一行）")
    ap.add_argument("--description", default="", help="库说明（一行）")
    try:
        a = ap.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        res = init_kb(a.kb, a.kb_id, shelves=a.shelf, name=a.name, description=a.description)
    except ValueError as exc:
        print(f"init-kb: {exc}", file=sys.stderr)
        return 2
    except InitRefused as exc:
        print(f"init-kb: 拒绝（未写入）：{exc}", file=sys.stderr)
        return 1
    if res["changed"]:
        print(f"init-kb: {a.kb}  写入/新建 {len(res['changed'])} 项：" + " ".join(res["changed"]))
    else:
        print(f"init-kb: {a.kb}  已是完整骨架，未改动")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit init-kb``
    raise SystemExit(main())
