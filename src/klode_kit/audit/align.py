"""WI-07b: cell alignment of the routes of one page (design 02 r2.2-D, R2-08, R3-05; matrix 07b-*).

``align(routes, *, items=None, tau=0.6)`` takes ``{engine: [ocr_line rows of one page]}`` and returns an
``Alignment``: the page's rows, each route's monotone pairing to those rows, and the cells with every route's
candidate and provenance. It never reads files and never decides agreement (that is WI-07c).

**Base route and rows.** Token-bearing routes (lines with ``bbox_px`` and a ``tokens`` list: vision, tesseract,
pdftext) lay out the rows. The base route is the first of them in ``ENGINE_ORDER`` with at least one ``exact``
token (R2-08 + R3-05: production vision boxes are ``approx``, so tesseract's word boxes usually make it the
base); ``None`` when no route has exact geometry. The base route's lines, grouped by vertical overlap
>= ``ROW_OVERLAP`` of the smaller height, seed the rows; the other token routes attach their lines to a row
they overlap the same way, or add a row (a row the base route missed). Rows are ordered top to bottom.
A page without any token-bearing route takes its rows from the route with the most lines (ties:
``ENGINE_ORDER``), in reading order.

**Pairs.** Each route's lines become route rows: lines with a ``bbox_px`` are grouped by the same overlap rule
and joined left to right (MLX printing a table column by column, one box per cell, gets its rows back - F-3);
a line without one (cloud routes; MLX lines of a multi-line block, which keep only ``block_bbox``) is a route
row of its own. A monotone dynamic programme then pairs route rows with rows:

- a route row with a box pairs with a row it overlaps by >= ``ROW_OVERLAP`` (score 2);
- a route row without one pairs by text: similarity >= ``tau`` against the texts the token routes read on that
  row (``difflib`` ratio over the NFKC-folded text without whitespace; score 1 + similarity); an MLX block line
  only pairs with rows lying >= ``ROW_OVERLAP`` inside its ``block_bbox``;
- a text route row whose similarity to the concatenation of two consecutive rows is >= ``tau`` and higher than
  to either row alone may pair with both (score 2 x (1 + similarity)): ``merged_rows``; its fields are split
  between the two rows at the whitespace boundary that best matches each row's text;
- anything else is skipped: a row without a route row is ``unaligned:<engine>``, a route row without a row is
  listed in ``Alignment.extra[engine]`` (stamps, footers - kept out of the cells).

**Cells.** ``audit.fields.extract`` runs on each paired route row; the ``k``-th field of a kind on a row is
cell ``(row, kind, k)`` (cloud routes have no columns, so in-row order places them - R2-08). Each candidate keeps
the literal value, ``norm``, glued ``unit``, drug ``dose``, and its provenance in the route's own line:
``line_idx``, ``span`` (``line[span] == value``), ``bbox_px`` (covering tokens, else the line box, ``None`` on
cloud routes), ``der_sha256``, ``run_id``. ``col`` is the cluster of token ``x0`` values (gap >
``COL_GAP_PX``) of the token that holds the field in the first token route (``ENGINE_ORDER``) with a candidate;
``geom`` names that route. Approx tokens count only on rows that also carry an exact token (R3-05); on a row
without exact geometry ``col``/``geom`` are ``None`` and the cell is ``no_geometry``. ``ordinal``/``count``
number the cells of one kind with the same reference value (the most common candidate ``norm``) in row order,
so repeated values stay separate cells. ``item_label`` is the row's item slug: the leftmost non-numeric
whitespace piece (cut at a colon, so ``姓名：<name>`` labels as ``姓名``) by majority over the routes (a label a
single route split, like tesseract's ``血红 蛋白``, loses), resolved through ``items.yaml``; unknown items of rows
holding a number or drug cell are ``Alignment.new_items``.

Every ordering decision uses ``ENGINE_ORDER``, never the order of ``routes``: permuting the routes changes
nothing (matrix 07b-09).
"""

from __future__ import annotations

import bisect
import difflib
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from klode_kit import items as items_mod
from klode_kit.audit import fields as F

ENGINE_ORDER = ("vision", "tesseract", "pdftext", "mlx", "claude", "codex")
GEOMETRY_ORDER = ("vision", "tesseract", "pdftext")
ROW_OVERLAP = 0.5
LINE_SIM_TAU = 0.6
COL_GAP_PX = 20
NO_GEOMETRY = "no_geometry"

_WS = re.compile(r"\s+")
_COLON = re.compile(r"[:：]")
_WORD = re.compile(r"[^\W\d_]")
_GEO_SCORE = 2.0
_JOIN = "\n"   # between the member lines of a route row: whitespace to extract(), and no field can span it


@dataclass(frozen=True)
class Candidate:
    value: str
    norm: str
    line_idx: int
    span: tuple[int, int]
    bbox_px: tuple[int, ...] | None
    der_sha256: str | None
    run_id: str | None = None
    unit: str | None = None
    dose: str | None = None


@dataclass(frozen=True)
class Cell:
    row: int
    kind: str
    k: int
    col: str | None
    geom: str | None
    ordinal: int
    count: int
    item_label: str | None
    candidates: tuple[tuple[str, Candidate], ...]
    uncertain: tuple[str, ...]

    def candidate(self, engine: str) -> Candidate | None:
        return dict(self.candidates).get(engine)

    @property
    def engines(self) -> tuple[str, ...]:
        return tuple(e for e, _ in self.candidates)


@dataclass(frozen=True)
class Row:
    row: int
    bbox_px: tuple[int, int, int, int] | None
    exact: bool
    label: str | None
    item_label: str | None
    lines: tuple[tuple[str, tuple[int, ...]], ...]


@dataclass(frozen=True)
class Pair:
    rows: tuple[int, ...]
    seq: int
    lines: tuple[int, ...]


@dataclass(frozen=True)
class Alignment:
    base: str | None
    rows: tuple[Row, ...]
    pairs: Mapping[str, tuple[Pair, ...]]
    cells: tuple[Cell, ...]
    uncertain: tuple[tuple[str, int], ...]
    extra: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    new_items: tuple[str, ...] = ()


# --------------------------------------------------------------------------- helpers


def _ordered(engines) -> list[str]:
    return sorted(engines, key=lambda e: (ENGINE_ORDER.index(e) if e in ENGINE_ORDER else len(ENGINE_ORDER), e))


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Overlap of two intervals as a share of the shorter one."""
    ov = min(a[1], b[1]) - max(a[0], b[0])
    if ov <= 0:
        return 0.0
    return ov / min(a[1] - a[0], b[1] - b[0])


def _vertical(line: Mapping) -> bool:
    """A vertical CJK text column (fx-vertical): taller than twice its width, at least two characters, none of
    them ASCII (a tall box around ``g/L`` is not vertical text)."""
    b = line["bbox_px"]
    chars = [ch for ch in line["text"] if not ch.isspace()]
    return (b[3] - b[1]) > 2 * (b[2] - b[0]) and len(chars) >= 2 and all(ord(ch) > 0x7F for ch in chars)


def _same_row(a: tuple, a_vertical: bool, b: tuple, b_vertical: bool) -> float:
    """How strongly two boxes share a row: the vertical overlap share for horizontal text; vertical-text boxes
    also need horizontal overlap, and never share a row with horizontal text. 0.0 = not the same row."""
    if a_vertical != b_vertical:
        return 0.0
    ov = _overlap((a[1], a[3]), (b[1], b[3]))
    if a_vertical and _overlap((a[0], a[2]), (b[0], b[2])) < ROW_OVERLAP:
        return 0.0
    return ov


def _row_key(box: tuple, vertical: bool, tie: int) -> tuple:
    """Horizontal rows top to bottom, then vertical-text columns left to right."""
    return (1, box[0], box[1], tie) if vertical else (0, (box[1] + box[3]) / 2, tie)


def _inside(row: tuple[float, float], block: tuple[float, float]) -> bool:
    ov = min(row[1], block[1]) - max(row[0], block[0])
    return ov > 0 and ov / (row[1] - row[0]) >= ROW_OVERLAP


def _squash(text: str) -> str:
    return _WS.sub("", F.fold(text))


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _token_route(lines: Sequence[Mapping]) -> bool:
    return any(ln.get("bbox_px") is not None and isinstance(ln.get("tokens"), list) for ln in lines)


def _has_exact(lines: Sequence[Mapping]) -> bool:
    return any(t.get("geometry") == "exact" for ln in lines for t in (ln.get("tokens") or ()))


@dataclass
class _RRow:
    """One route row: member lines joined left to right; ``box`` is its seed line's box (``None``: no box)."""

    seq: int
    members: list[Mapping]
    text: str
    offsets: list[int]
    box: tuple | None
    vertical: bool
    block: tuple[float, float] | None


def _route_rows(lines: Sequence[Mapping]) -> list[_RRow]:
    """Group a route's boxed lines into rows (seed box, never grown, so a tall box cannot chain rows together);
    a line without a box is a row of its own. All boxed: rows in ``_row_key`` order; else reading order."""
    groups: list[dict[str, Any]] = []
    for ln in sorted(lines, key=lambda ln: ln["line_idx"]):
        if ln.get("bbox_px") is None:
            groups.append({"members": [ln], "box": None, "vertical": False})
            continue
        box, vertical = tuple(ln["bbox_px"]), _vertical(ln)
        best = max(((_same_row(g["box"], g["vertical"], box, vertical), i)
                    for i, g in enumerate(groups) if g["box"] is not None), default=(0.0, -1))
        if best[0] >= ROW_OVERLAP:
            groups[best[1]]["members"].append(ln)
        else:
            groups.append({"members": [ln], "box": box, "vertical": vertical})
    if groups and all(g["box"] is not None for g in groups):
        groups.sort(key=lambda g: _row_key(g["box"], g["vertical"], g["members"][0]["line_idx"]))
    out = []
    for seq, g in enumerate(groups):
        members = g["members"]
        if g["box"] is not None:
            axis = 1 if g["vertical"] else 0
            members = sorted(members, key=lambda ln: (ln["bbox_px"][axis], ln["line_idx"]))
        text, offsets = "", []
        for ln in members:
            if text:
                text += _JOIN
            offsets.append(len(text))
            text += ln["text"]
        block = members[0].get("block_bbox") if g["box"] is None else None
        out.append(_RRow(seq, members, text, offsets, g["box"], g["vertical"],
                         (block[1], block[3]) if block is not None else None))
    return out


@dataclass
class _SRow:
    """A row of the page while it is being built (``box``: its seed box, ``None`` on a text-only page)."""

    box: tuple | None
    vertical: bool = False
    lines: dict[str, list[Mapping]] = field(default_factory=dict)
    refs: list[str] = field(default_factory=list)

    @property
    def extent(self) -> tuple[float, float] | None:
        return (self.box[1], self.box[3]) if self.box is not None else None


def _skeleton(routes: Mapping[str, Sequence[Mapping]], token: list[str], base: str | None) -> list[_SRow]:
    if not token:
        seeds = sorted(_ordered(routes), key=lambda e: -len(routes[e]))[:1]
        return [_SRow(rr.box, rr.vertical, {seeds[0]: list(rr.members)}, [_squash(rr.text)])
                for rr in (_route_rows(routes[seeds[0]]) if seeds else [])]
    seeds = ([base] if base is not None else []) + [e for e in token if e != base]
    rows: list[_SRow] = []
    for engine in seeds:
        for rr in _route_rows(routes[engine]):
            if rr.box is None:
                continue
            best = max(((_same_row(r.box, r.vertical, rr.box, rr.vertical), i) for i, r in enumerate(rows)),
                       default=(0.0, -1))
            if best[0] >= ROW_OVERLAP:
                row = rows[best[1]]
            else:
                row = _SRow(rr.box, rr.vertical)
                rows.append(row)
            row.lines.setdefault(engine, []).extend(rr.members)
            row.refs.append(_squash(rr.text))
    order = sorted(range(len(rows)), key=lambda i: _row_key(rows[i].box, rows[i].vertical, i))
    return [rows[i] for i in order]


# --------------------------------------------------------------------------- monotone pairing


def _pair(rows: list[_SRow], rrows: list[_RRow], tau: float) -> list[tuple[tuple[int, ...], int]]:
    n, m = len(rows), len(rrows)
    texts = [_squash(rr.text) for rr in rrows]

    def match(i: int, j: int) -> float | None:
        rr, row = rrows[j], rows[i]
        if rr.box is not None and row.box is not None:
            return _GEO_SCORE if _same_row(row.box, row.vertical, rr.box, rr.vertical) >= ROW_OVERLAP else None
        if rr.block is not None and row.extent is not None and not _inside(row.extent, rr.block):
            return None
        s = max((_ratio(ref, texts[j]) for ref in row.refs), default=0.0)
        return 1.0 + s if s >= tau else None

    def merge(i: int, j: int) -> float | None:
        rr = rrows[j]
        if rr.box is not None:
            return None
        a, b = rows[i], rows[i + 1]
        if rr.block is not None and any(r.extent is not None and not _inside(r.extent, rr.block) for r in (a, b)):
            return None
        s = max((_ratio(x + y, texts[j]) for x in a.refs for y in b.refs), default=0.0)
        single = max((_ratio(ref, texts[j]) for ref in (*a.refs, *b.refs)), default=0.0)
        return 2.0 * (1.0 + s) if s >= tau and s > single else None

    best = [[0.0] * (m + 1) for _ in range(n + 2)]
    move: list[list[tuple[str, float]]] = [[("", 0.0)] * (m + 1) for _ in range(n + 1)]
    for i in range(n, -1, -1):
        for j in range(m, -1, -1):
            if i == n and j == m:
                continue
            options: list[tuple[float, str]] = []
            if i < n and j < m:
                s = match(i, j)
                if s is not None:
                    options.append((s + best[i + 1][j + 1], "match"))
            if i + 1 < n and j < m:
                s = merge(i, j)
                if s is not None:
                    options.append((s + best[i + 2][j + 1], "merge"))
            if j < m:
                options.append((best[i][j + 1], "skip_line"))
            if i < n:
                options.append((best[i + 1][j], "skip_row"))
            score, op = options[0]
            for cand_score, cand_op in options[1:]:
                if cand_score > score:
                    score, op = cand_score, cand_op
            best[i][j] = score
            move[i][j] = (op, score)
    out: list[tuple[tuple[int, ...], int]] = []
    i = j = 0
    while i < n or j < m:
        op = move[i][j][0]
        if op == "match":
            out.append(((i,), j))
            i, j = i + 1, j + 1
        elif op == "merge":
            out.append(((i, i + 1), j))
            i, j = i + 2, j + 1
        elif op == "skip_line":
            j += 1
        else:
            i += 1
    return out


def _split_point(rr: _RRow, a: _SRow, b: _SRow) -> int:
    """Where a merged route row's text divides between two rows (an offset in ``rr.text``)."""
    text = rr.text
    cuts = [m.start() for m in re.finditer(r"(?<=\s)\S", text)] or list(range(1, len(text)))

    def score(p: int) -> float:
        left, right = _squash(text[:p]), _squash(text[p:])
        return max((_ratio(r, left) for r in a.refs), default=0.0) + max((_ratio(r, right) for r in b.refs), default=0.0)

    return max(cuts, key=lambda p: (score(p), -p)) if cuts else len(text)


# --------------------------------------------------------------------------- candidates


def _candidate(rr: _RRow, f: F.Field) -> Candidate:
    """The field's candidate in its own member line (members are joined by ``\\n``, which no field spans)."""
    at = bisect.bisect_right(rr.offsets, f.start) - 1
    member, off = rr.members[at], rr.offsets[at]
    s, e = f.start - off, f.end - off
    box = member.get("bbox_px")
    toks = [t for t in (member.get("tokens") or ()) if t["span"][0] < e and s < t["span"][1]]
    if toks:
        bbox = (min(t["bbox_px"][0] for t in toks), min(t["bbox_px"][1] for t in toks),
                max(t["bbox_px"][2] for t in toks), max(t["bbox_px"][3] for t in toks))
    else:
        bbox = tuple(box) if box is not None else None
    return Candidate(f.value, f.norm, member["line_idx"], (s, e), bbox, member.get("der_sha256"),
                     member.get("run_id"), f.unit, f.dose)


def _place(fields: list[F.Field], rr: _RRow, row: int, engine: str, out: dict) -> None:
    seen: Counter = Counter()
    for f in fields:
        k = seen[f.kind]
        seen[f.kind] += 1
        out.setdefault((row, f.kind, k), {})[engine] = _candidate(rr, f)


def _label(text: str) -> str | None:
    """The leftmost whitespace piece (cut at a colon) with a word character and no digit."""
    for piece in text.split():
        piece = _COLON.split(piece, 1)[0]
        if _WORD.search(piece) and not any(ch.isdigit() for ch in F.fold(piece)):
            return piece
    return None


def _reference(cands: Mapping[str, Candidate]) -> str:
    """The most common candidate norm; ties go to the first engine in ``ENGINE_ORDER``."""
    norms = Counter(c.norm for c in cands.values())
    top = max(norms.values())
    return next(cands[e].norm for e in _ordered(cands) if norms[cands[e].norm] == top)


# --------------------------------------------------------------------------- columns


def _clusters(xs: list[float]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for x in sorted(set(xs)):
        if out and x - out[-1][1] <= COL_GAP_PX:
            out[-1][1] = x
        else:
            out.append([x, x])
    return [(a, b) for a, b in out]


def _col_of(x: float, clusters: list[tuple[float, float]]) -> str | None:
    return next((f"c{i}" for i, (a, b) in enumerate(clusters) if a <= x <= b), None)


# --------------------------------------------------------------------------- align


def align(routes: Mapping[str, Sequence[Mapping]], *, items: items_mod.Items | None = None,
          tau: float = LINE_SIM_TAU) -> Alignment:
    """Align the routes of one page (see the module docstring)."""
    table = items if items is not None else items_mod.default()
    engines = _ordered(routes)
    token = [e for e in engines if _token_route(routes[e])]
    base = next((e for e in token if _has_exact(routes[e])), None)
    srows = _skeleton(routes, token, base)
    exact = [any(t.get("geometry") == "exact" for lns in r.lines.values() for ln in lns for t in (ln.get("tokens") or ()))
             for r in srows]

    pairs: dict[str, tuple[Pair, ...]] = {}
    extra: dict[str, tuple[int, ...]] = {}
    found: dict[tuple[int, str, int], dict[str, Candidate]] = {}
    labels: dict[int, list[tuple[str, str]]] = {}
    uncertain: set[tuple[str, int]] = set()
    for engine in engines:
        rrows = _route_rows(routes[engine])
        got = _pair(srows, rrows, tau)
        pairs[engine] = tuple(Pair(rows, j, tuple(ln["line_idx"] for ln in rrows[j].members)) for rows, j in got)
        used = {j for _, j in got}
        unpaired = tuple(rr.seq for rr in rrows if rr.seq not in used)
        if unpaired:
            extra[engine] = unpaired
        covered = {r for rows, _ in got for r in rows}
        uncertain |= {(f"unaligned:{engine}", r) for r in range(len(srows)) if r not in covered}
        for rows, j in got:
            rr = rrows[j]
            fields = F.extract(rr.text)
            if len(rows) == 1:
                _place(fields, rr, rows[0], engine, found)
                label = _label(rr.text)
                if label is not None:
                    labels.setdefault(rows[0], []).append((engine, label))
                continue
            uncertain |= {(f"merged_rows:{engine}", r) for r in rows}
            cut = _split_point(rr, srows[rows[0]], srows[rows[1]])
            _place([f for f in fields if f.start < cut], rr, rows[0], engine, found)
            _place([f for f in fields if f.start >= cut], rr, rows[1], engine, found)

    # rows: label by majority, geometry
    row_label: list[tuple[str | None, str | None]] = []
    for r in range(len(srows)):
        votes = [(e, lab, table.resolve(lab)[0]) for e, lab in labels.get(r, [])]
        if not votes:
            row_label.append((None, None))
            continue
        tally = Counter(slug for _, _, slug in votes)
        top = max(tally.values())
        winner = next(v for v in votes if tally[v[2]] == top)
        row_label.append((winner[1], winner[2]))

    xs = [t["bbox_px"][0] for r, ok in zip(srows, exact) if ok
          for lns in r.lines.values() for ln in lns for t in (ln.get("tokens") or ())]
    clusters = _clusters(xs)
    by_idx = {e: {ln["line_idx"]: ln for ln in routes[e]} for e in token}

    row_codes: dict[int, list[str]] = {}
    for code, r in sorted(uncertain):
        row_codes.setdefault(r, []).append(code)

    keys = sorted(found)
    refs = {key: _reference(found[key]) for key in keys}
    totals = Counter((key[1], refs[key]) for key in keys)
    seen: Counter = Counter()
    cells = []
    for key in keys:
        row, kind, k = key
        cands = found[key]
        col = geom = None
        if exact[row]:
            for engine in (e for e in _ordered(cands) if e in by_idx):
                cand = cands[engine]
                line = by_idx[engine][cand.line_idx]
                tok = next((t for t in (line.get("tokens") or ()) if t["span"][0] <= cand.span[0] < t["span"][1]), None)
                col = _col_of(tok["bbox_px"][0], clusters) if tok is not None else None
                if col is not None:
                    geom = engine
                    break
        ref = (kind, refs[key])
        codes = tuple(sorted(row_codes.get(row, []) + ([] if exact[row] else [NO_GEOMETRY])))
        cells.append(Cell(row, kind, k, col, geom, seen[ref], totals[ref], row_label[row][1],
                          tuple((e, cands[e]) for e in _ordered(cands)), codes))
        seen[ref] += 1

    valued = {c.row for c in cells if c.kind in ("number", "drug")}
    new_items = table.new_items(row_label[r][0] for r in sorted(valued) if row_label[r][0] is not None)
    out_rows = []
    for r, srow in enumerate(srows):
        boxes = [ln["bbox_px"] for lns in srow.lines.values() for ln in lns if ln.get("bbox_px") is not None]
        bbox = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                max(b[3] for b in boxes)) if boxes else None
        lines = tuple((e, tuple(sorted(ln["line_idx"] for ln in srow.lines[e]))) for e in _ordered(srow.lines))
        out_rows.append(Row(r, bbox, exact[r], row_label[r][0], row_label[r][1], lines))
    return Alignment(base, tuple(out_rows), pairs, tuple(cells),
                     tuple(sorted(uncertain, key=lambda u: (u[1], u[0]))), extra, tuple(new_items))
