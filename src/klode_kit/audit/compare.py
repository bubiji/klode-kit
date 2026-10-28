"""WI-07c: N-route vote per cell (design 02 r2.2-D, r2.3 reason table; plan WI-07c; matrix 07c-*; K1).

``compare(alignment, *, routes, routes_enabled=None, whitelist=None)`` takes the WI-07b ``Alignment`` of one page
and returns a ``Comparison``: one ``CellVote`` per aligned cell. It never reads files and never changes a reading.

**Voting routes.** ``routes`` (the configured routes, e.g. ``vision mlx claude codex``) minus those switched off in
``routes_enabled``. Candidates of any other engine (tesseract, which only supplies geometry; a disabled route's
stale output) are ignored: they neither vote nor count as present.

**Vote.** Voting candidates are grouped by their comparison key - the WI-07a ``norm``, folded by the engine
weakness whitelist on text cells only (``whitelist.Whitelist.key``; numbers and dates are never folded).
``votes`` is ``{value: [engines]}`` as ``((value, engines), ...)``, largest group first (ties: the group whose
first engine comes first in ``align.ENGINE_ORDER``); a group's ``value`` is its most common ``norm`` (ties: the
one equal to the key, then ``ENGINE_ORDER``), so it is always one of the candidates.

- ``agree``: exactly one group, every voting route present, and no *blocking* uncertain code (below). WI-32b: the
  advisory codes ``no_geometry`` and ``ordinal_shift:<e>`` do not block - they are display / placement doubts, and
  every route reads the same value (``CellVote.notes`` keeps them, ``uncertain`` is empty).
- ``majority``: one strictly largest group that does not ``agree``; its value is ``recommended``.
- ``tie``: two or more groups share the largest size >= 2; ``all_differ``: every present route reads a different
  value (>= 2 present); ``none``: no voting candidate. These three never recommend a value.

``recommended`` is only a recommendation: ``readings`` keep every voting candidate's ``norm`` as read.

**Uncertain codes** (``CellVote.uncertain``, sorted; any code blocks ``agree`` except the advisory ones):

- the cell's own codes from the alignment, passed through unchanged (``unaligned:<e>``, ``merged_rows:<e>``,
  ``no_geometry``) - design v2 gap 2: alignment doubt is never swallowed;
- ``missing_cell:<e>`` for each voting route without a candidate (a disabled route included);
- ``ordinal_shift:<e>``: WI-07b places the k-th field of a kind on a row in cell ``(row, kind, k)``, so one
  misread moves a route's later fields of that kind one place forward. When the cells of one ``(row, kind)`` do
  not all hold the same set of voting routes, every cell of that ``(row, kind)`` gets ``ordinal_shift:<e>`` for
  each route present in some but not all of them ("all routes present" is judged per row and kind);
- ``swap_suspect:<e>``: for two consecutive cells of the same ``(kind, k)`` (rows in order), route ``e`` reads in
  each the value the other routes' unique majority reads in the other, and those majorities differ;
- ``tie`` / ``all_differ`` for those verdicts;
- ``extra_values:<e>`` on every cell of the page when voting route ``e`` has a line that paired with no row and
  holds a number, date or drug (``Alignment.extra_values``): a value nobody placed could belong to any cell, so
  nothing on the page is auto-agreed. These are also listed page-level in ``Comparison.uncertain`` as
  ``(code, route row seq)``.

**Advisory codes (WI-32b, user decision 2026-10-27).** ``no_geometry`` (no exact box to crop with; and, from
WI-07d, ``no_bbox_crop`` which only exists for cells that are cropped, i.e. not ``agree``) and ``ordinal_shift:<e>``
only affect how the cell is displayed or where a route's k-th value of a row sits. When every voting route is
present and they all read one value, the cell agrees anyway: the codes are moved from ``uncertain`` to ``notes``
(trace; the audit data keeps saying the doubt existed) and the confirm page does not list the cell. They stay
ordinary ``uncertain`` codes on every cell that does not agree, so a real dispute still lists all its doubts.
Never advisory: ``unaligned`` / ``merged_rows`` (the row pairing itself is in doubt), ``swap_suspect``,
``extra_values`` (a value nobody placed), ``missing_cell`` and the tie verdicts. Why ``ordinal_shift`` with equal
readings is safe: a route that lost a field of a kind on a row has no value in the last cell of that
``(row, kind)``; that cell lacks the route, so it is a ``missing_cell`` dispute and is still listed (the row's
crop shows the user the whole row). A cell where the shifted route still has a value and equals the three others
means that either its value is its own k-th one, or it is the next field's value, which then equals this one -
in both cases the value every route reports for the cell is the value of this cell, and no wrong value can pass
that three independent routes did not already agree on.

Every ordering uses ``align.ENGINE_ORDER`` and cell keys, never the order of ``routes`` or of the candidates:
permuting them changes nothing (matrix 07c-06).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Sequence

from klode_kit.audit import align as A
from klode_kit.audit import whitelist as W

AGREE = "agree"
MAJORITY = "majority"
TIE = "tie"
ALL_DIFFER = "all_differ"
NONE = "none"


@dataclass(frozen=True)
class CellVote:
    row: int
    kind: str
    k: int
    col: str | None
    item_label: str | None
    ordinal: int
    count: int
    readings: tuple[tuple[str, str], ...]
    votes: tuple[tuple[str, tuple[str, ...]], ...]
    missing: tuple[str, ...]
    verdict: str
    recommended: str | None
    uncertain: tuple[str, ...]
    notes: tuple[str, ...] = ()          # advisory doubts of an agreeing cell (trace, never a reason to confirm)

    @property
    def agree(self) -> bool:
        return self.verdict == AGREE

    def reading(self, engine: str) -> str | None:
        return dict(self.readings).get(engine)


@dataclass(frozen=True)
class Comparison:
    routes: tuple[str, ...]
    cells: tuple[CellVote, ...]
    uncertain: tuple[tuple[str, int], ...]

    def cell(self, row: int, kind: str, k: int) -> CellVote | None:
        return next((c for c in self.cells if (c.row, c.kind, c.k) == (row, kind, k)), None)


def is_advisory(code: str) -> bool:
    """True for a doubt that never blocks an all-routes-equal cell (module docstring, "Advisory codes")."""
    return code in ("no_geometry", "no_bbox_crop") or code.startswith("ordinal_shift:")


def _rank(engine: str) -> int:
    return A.ENGINE_ORDER.index(engine) if engine in A.ENGINE_ORDER else len(A.ENGINE_ORDER)


def _ordered(engines) -> tuple[str, ...]:
    return tuple(sorted(engines, key=lambda e: (_rank(e), e)))


def _groups(kind: str, reads: Mapping[str, str], wl: W.Whitelist) -> list[tuple[str, str, tuple[str, ...]]]:
    """``[(key, value, engines)]`` biggest first; see the module docstring for the tie rules."""
    keyed: dict[str, list[str]] = {}
    for e in _ordered(reads):
        keyed.setdefault(wl.key(kind, reads[e], reads), []).append(e)
    out = []
    for key, engines in keyed.items():
        norms = Counter(reads[e] for e in engines)
        top = max(norms.values())
        best = [n for n in (reads[e] for e in engines) if norms[n] == top]
        value = key if key in best else best[0]
        out.append((key, value, tuple(engines)))
    out.sort(key=lambda g: (-len(g[2]), _rank(g[2][0]), g[2][0]))
    return out


def _majority(keys: Mapping[str, str], skip: str) -> str | None:
    """The unique most common key among the routes other than ``skip``."""
    tally = Counter(v for e, v in keys.items() if e != skip)
    if not tally:
        return None
    top = max(tally.values())
    winners = [v for v, n in tally.items() if n == top]
    return winners[0] if len(winners) == 1 else None


def compare(al: A.Alignment, *, routes: Sequence[str], routes_enabled: Mapping[str, bool] | None = None,
            whitelist: W.Whitelist | None = None) -> Comparison:
    """Vote every cell of one aligned page (see the module docstring)."""
    if not routes:
        raise ValueError("compare needs at least one voting route")
    if len(set(routes)) != len(routes):
        raise ValueError(f"route named twice in {list(routes)}")
    wl = whitelist if whitelist is not None else W.default()
    enabled = routes_enabled or {}
    voters = _ordered(e for e in routes if enabled.get(e, True))
    configured = _ordered(routes)

    reads: dict[tuple[int, str, int], dict[str, str]] = {}
    for c in al.cells:
        reads[(c.row, c.kind, c.k)] = {e: cand.norm for e, cand in c.candidates if e in voters}
    keys = {key: {e: wl.key(key[1], n, r) for e, n in r.items()} for key, r in reads.items()}

    codes: dict[tuple[int, str, int], set[str]] = {key: set() for key in reads}

    # ordinal shift per (row, kind)
    groups: dict[tuple[int, str], list[tuple[int, str, int]]] = {}
    for key in reads:
        groups.setdefault(key[:2], []).append(key)
    for members in groups.values():
        sets = [frozenset(reads[m]) for m in members]
        shifted = frozenset.union(*sets) - frozenset.intersection(*sets)
        for m in members:
            codes[m] |= {f"ordinal_shift:{e}" for e in shifted}

    # swap between consecutive cells of one (kind, k)
    lanes: dict[tuple[str, int], list[tuple[int, str, int]]] = {}
    for key in sorted(reads):
        lanes.setdefault((key[1], key[2]), []).append(key)
    for members in lanes.values():
        for a, b in zip(members, members[1:]):
            for e in set(keys[a]) & set(keys[b]):
                maj_a, maj_b = _majority(keys[a], e), _majority(keys[b], e)
                if maj_a is None or maj_b is None or maj_a == maj_b:
                    continue
                if keys[a][e] == maj_b and keys[b][e] == maj_a:
                    codes[a].add(f"swap_suspect:{e}")
                    codes[b].add(f"swap_suspect:{e}")

    page = tuple(sorted(((f"extra_values:{e}", seq) for e in voters for seq in al.extra_values.get(e, ())),
                        key=lambda u: (u[1], u[0])))
    page_codes = {code for code, _ in page}

    out = []
    for c in al.cells:
        key = (c.row, c.kind, c.k)
        r = reads[key]
        missing = tuple(e for e in configured if e not in r)
        grouped = _groups(c.kind, r, wl)
        cell_codes = set(c.uncertain) | codes[key] | page_codes | {f"missing_cell:{e}" for e in missing}
        recommended = None
        notes: tuple[str, ...] = ()
        if not grouped:
            verdict = NONE
        elif len(grouped) > 1 and len(grouped[0][2]) == len(grouped[1][2]):
            verdict = ALL_DIFFER if len(grouped[0][2]) == 1 else TIE
            cell_codes.add(verdict)
        else:
            recommended = grouped[0][1]
            blocking = [code for code in cell_codes if not is_advisory(code)]
            verdict = AGREE if len(grouped) == 1 and not blocking else MAJORITY
            if verdict == AGREE:
                notes, cell_codes = tuple(sorted(cell_codes)), set()
        out.append(CellVote(c.row, c.kind, c.k, c.col, c.item_label, c.ordinal, c.count,
                            tuple((e, r[e]) for e in _ordered(r)),
                            tuple((value, engines) for _, value, engines in grouped),
                            missing, verdict, recommended, tuple(sorted(cell_codes)), notes))
    out.sort(key=lambda v: (v.row, v.kind, v.k))
    return Comparison(configured, tuple(out), page)
