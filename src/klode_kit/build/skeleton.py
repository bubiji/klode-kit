"""Read-only: print each section title of a source and the first characters of its first paragraph (WI-02a).

Vendored as is from the user's klode-build skill ``scripts/skeleton.py`` (read-only source, 2026-10-24): a quick
look at a chapter's skeleton before writing its card; nothing is written.

    klode-kit skeleton <kb_dir> <id> [chars=400]
    exit 0; 1 no source library/*/<id>.txt; 2 usage

Section title heuristic (the original's, made for EPUB-extracted text): a line of its own, <= 12 words, not ending
in sentence punctuation ``.!?,;:``, after an empty line (or the first line), whose next non-empty line within the
following 5 lines is a paragraph of >= 120 characters.

Changed: the module-level script became ``sections()`` (returns the data) + ``main(argv)`` (prints it, same
format); a missing source is exit 1 with the same message instead of ``sys.exit(<text>)``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

MAX_TITLE_WORDS = 12
MIN_PARA_CHARS = 120
LOOKAHEAD = 5


def sections(kb: Path, cid: str, chars: int = 400) -> dict[str, Any]:
    kb = Path(kb).resolve()
    hits = sorted((kb / "library").glob(f"*/{cid}.txt"))
    if not hits:
        raise FileNotFoundError(f"找不到源 library/*/{cid}.txt")
    lines = hits[0].read_text(encoding="utf-8").splitlines()
    found = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or len(s.split()) > MAX_TITLE_WORDS or re.search(r"[.!?,;:]$", s):
            continue
        if i > 0 and lines[i - 1].strip():
            continue
        nxt = next((lines[j].strip() for j in range(i + 1, min(i + 1 + LOOKAHEAD, len(lines))) if lines[j].strip()),
                   "")
        if len(nxt) < MIN_PARA_CHARS:
            continue
        found.append({"title": s, "line": i + 1, "lead": nxt[:chars]})
    return {"source": hits[0].relative_to(kb).as_posix(), "lines": len(lines),
            "words": sum(len(ln.split()) for ln in lines), "sections": found}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) < 2:
        print(__doc__)
        return 2
    try:
        chars = int(argv[2]) if len(argv) > 2 else 400
    except ValueError:
        print(__doc__)
        return 2
    try:
        res = sections(Path(argv[0]), argv[1], chars)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"# {res['source']}  {res['lines']} 行 / {res['words']:,} 词\n")
    for sec in res["sections"]:
        print(f"【{sec['title']}】(L{sec['line']})\n{sec['lead']}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit skeleton``
    raise SystemExit(main())
