"""Whole-document text with a page map (WI-04c; design 02 r2.4 "文字层 PDF": ``_whole.txt`` + ``page_map``).

``text_to_whole(pages)``: ``pages`` is a sequence of mappings, each with a unique ``page_id``, its ``lines``
(strings without any line break) and any further metadata (``key``, ``dpi``, ``size_px``, ...). It returns

- ``whole``: every line of every page in order, each followed by ``"\\n"`` (``""`` when there are no lines);
- ``page_map``: one row per page, in order - the page's metadata without ``lines``, plus ``first_line``
  (1-based line number of its first line in ``whole``; for a page without lines, where its next line would be)
  and ``n_lines``.

``locate(page_map, line_no)`` maps a 1-based line number of ``whole`` back to ``(page_id, line index)``.

Law (round trip): ``pages[locate(m, n).page]["lines"][locate(m, n).idx] == whole.split("\\n")[n - 1]`` for
every line ``n``, and ``locate`` is a bijection onto the (page, line) pairs. A line that would itself break into
several lines (``str.splitlines``) would break that law, so it is rejected (``ValueError``), never split.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence


def _check_line(page_id: str, idx: int, line: Any) -> str:
    if not isinstance(line, str):
        raise ValueError(f"{page_id} line {idx} is not a string")
    if line and line.splitlines() != [line]:
        raise ValueError(f"{page_id} line {idx} contains a line break")
    return line


def text_to_whole(pages: Sequence[Mapping[str, Any]]) -> tuple[str, list[dict]]:
    seen: set[str] = set()
    out: list[str] = []
    page_map: list[dict] = []
    for page in pages:
        pid = page["page_id"]
        if pid in seen:
            raise ValueError(f"duplicate page_id {pid}")
        seen.add(pid)
        lines = [_check_line(pid, i, line) for i, line in enumerate(page["lines"])]
        row = {k: v for k, v in page.items() if k != "lines"}
        row["first_line"] = len(out) + 1
        row["n_lines"] = len(lines)
        page_map.append(row)
        out.extend(lines)
    return "".join(line + "\n" for line in out), page_map


def locate(page_map: Sequence[Mapping[str, Any]], line_no: int) -> tuple[str, int]:
    """``(page_id, line index within the page)`` of 1-based line ``line_no`` of the whole text."""
    for row in page_map:
        first, n = row["first_line"], row["n_lines"]
        if first <= line_no < first + n:
            return row["page_id"], line_no - first
    raise LookupError(f"line {line_no} is not in the page map")
