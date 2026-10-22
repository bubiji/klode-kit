"""Simulated route outputs with injectable errors (WI-00a).

Injectors (pure; inputs are never mutated; rows are ``{"item","value","unit","flag","ref"}``
dicts, extra keys are carried along untouched):

- ``inject_swap(rows, i)``: values of rows ``i`` and ``i+1`` exchanged (the multiset of values
  is preserved and exactly those two positions change). Equal values -> ``ValueError``.
- ``inject_colshift(rows, i)``: the row's number lands in the unit column
  (``unit = value + unit``) and the value column becomes empty.
- ``inject_missingrow(routes, route, i)``: row ``i`` dropped from one route only.
- ``inject_degenerate(lines, repeats)``: the last line repeated ``repeats`` more times (an MLX
  loop); with distinct input lines ``repeat_ratio == repeats / (len + repeats)``.

``route_lines`` turns a route's rows plus the page layout into contract lines:
``{"engine","page_id","der_sha256","line_idx","text","bbox_px","tokens"}``. Local routes carry
pixel boxes (vision: per-token ``tokens`` with ``geometry``; mlx: line box only, ``tokens: null``);
cloud routes have ``bbox_px: null`` and ``tokens: null`` (r3.1, R2-08).
"""

from __future__ import annotations

import copy

COLUMNS = ("item", "value", "unit", "flag", "ref")


def inject_swap(rows: list[dict], i: int) -> list[dict]:
    if not 0 <= i < len(rows) - 1:
        raise ValueError(f"swap needs rows {i} and {i + 1}; table has {len(rows)}")
    if rows[i]["value"] == rows[i + 1]["value"]:
        raise ValueError("swap of equal values changes nothing")
    out = copy.deepcopy(rows)
    out[i]["value"], out[i + 1]["value"] = rows[i + 1]["value"], rows[i]["value"]
    return out


def inject_colshift(rows: list[dict], i: int) -> list[dict]:
    if not 0 <= i < len(rows):
        raise ValueError(f"row {i} out of range")
    if not rows[i]["value"]:
        raise ValueError("row has no value to shift")
    out = copy.deepcopy(rows)
    out[i]["unit"] = rows[i]["value"] + rows[i]["unit"]
    out[i]["value"] = ""
    return out


def inject_missingrow(routes: dict[str, list[dict]], route: str, i: int) -> dict[str, list[dict]]:
    if route not in routes:
        raise ValueError(f"unknown route {route!r}")
    if not 0 <= i < len(routes[route]):
        raise ValueError(f"row {i} out of range for {route}")
    out = copy.deepcopy(routes)
    del out[route][i]
    return out


def inject_degenerate(lines: list[str], repeats: int) -> list[str]:
    if not lines:
        raise ValueError("nothing to repeat")
    if repeats < 0:
        raise ValueError("repeats must be >= 0")
    return list(lines) + [lines[-1]] * repeats


# --------------------------------------------------------------------------- rendering


def _join(parts: list[tuple[str, str, list[int] | None]], joiner: str):
    """parts: (col, text, bbox). Returns text, {col: span}, tokens (non-empty parts only)."""
    text, spans, tokens = "", {}, []
    for col, s, bbox in parts:
        if not s:
            spans[col] = [len(text) + (len(joiner) if text else 0)] * 2
            continue
        if text:
            text += joiner
        spans[col] = [len(text), len(text) + len(s)]
        tokens.append((col, s, bbox, spans[col]))
        text += s
    return text, spans, tokens


def _union(boxes):
    boxes = [b for b in boxes if b]
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def route_lines(engine: str, layout: list[dict], rows: list[dict], *, page_id: str, der_sha256: str,
                merged: tuple[int, int] | None = None, approx_rows: frozenset[int] = frozenset()):
    """Lines for one route and the cell index ``{(src_row, col): {"line_idx","span","value"}}``.

    ``rows`` carry ``_src`` (the drawn row they were read from). ``merged=(a, b)`` joins the
    lines of source rows a and b into one line (``merged_rows``).
    """
    by_src = {x["row"]: x for x in layout if x["kind"] == "row"}
    plain = [x for x in layout if x["kind"] not in ("row", "vertical")]
    trailing = [x for x in layout if x["kind"] == "vertical"]

    groups: list[list[dict]] = []
    for r in rows:
        if merged and r["_src"] == merged[1] and groups and groups[-1][-1]["_src"] == merged[0]:
            groups[-1].append(r)
        else:
            groups.append([r])

    out, cells = [], {}

    def emit(text, boxes, tokens, approx=False):
        line = {"engine": engine, "page_id": page_id, "der_sha256": der_sha256, "line_idx": len(out),
                "text": text, "bbox_px": None, "tokens": None}
        if engine not in ("claude", "codex"):
            line["bbox_px"] = _union(boxes)
        if engine == "vision":
            line["tokens"] = [{"text": s, "bbox_px": b, "span": sp, "geometry": "approx" if approx else "exact"}
                              for _, s, b, sp in tokens]
        out.append(line)

    for x in plain:
        parts = [(sg["col"], sg["text"], sg["bbox"]) for sg in x["segments"]]
        text, _, tokens = _join(parts, x["joiner"])
        emit(text, [sg["bbox"] for sg in x["segments"]], tokens)
    for group in groups:
        text, tokens, boxes = "", [], []
        for r in group:
            drawn = {sg["col"]: sg["bbox"] for sg in by_src[r["_src"]]["segments"]}
            parts = [(c, r.get(c, ""), drawn.get(c) or drawn.get("value")) for c in COLUMNS]
            t, spans, toks = _join(parts, " ")
            offset = len(text) + (1 if text else 0)
            text = f"{text} {t}" if text else t
            for c in COLUMNS:
                s0, s1 = spans[c]
                cells[(r["_src"], c)] = {"line_idx": len(out), "span": [s0 + offset, s1 + offset], "value": r.get(c, "")}
            tokens += [(c, s, b, [sp[0] + offset, sp[1] + offset]) for c, s, b, sp in toks]
            boxes += list(drawn.values())
        emit(text, boxes, tokens, approx=any(r["_src"] in approx_rows for r in group))
    for x in trailing:
        parts = [(sg["col"], sg["text"], sg["bbox"]) for sg in x["segments"]]
        text, _, tokens = _join(parts, x["joiner"])
        emit(text, [sg["bbox"] for sg in x["segments"]], tokens)
    return out, cells
