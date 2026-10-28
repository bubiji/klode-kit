"""WI-08a: the uncertain reason table and the candidate gate (design 02 r2.3, r3.3, r4.1; matrix 08a-05..07;
K1, K2).

**Codes and entries.** WI-07b/07c/07d attach codes to cells (``missing_cell:<e>``, ``unaligned:<e>``,
``merged_rows:<e>``, ``ordinal_shift:<e>``, ``extra_values:<e>``, ``swap_suspect:<e>``, ``no_geometry``,
``tie``, ``all_differ``, ``no_bbox_crop``); WI-04a/04b/06 leave page states in the kb-ocr batch receipt
(``route_failed``, ``route_missing_page``, ``parse_error`` and, read from the error text, ``degenerate_output``,
``route_disabled``, ``route_unavailable``, ``effort_locked``). ``entry(code, ...)`` turns one code into an
uncertain entry of the record contract: ``{id, reason, scope, engine?, field_id?, fix_action, clear_evidence}``.
``REASONS`` is the table: scope, fix action and clearing evidence per reason. Ids are stable for one problem
(``<page_id>:<engine>:<reason>`` for a page, ``<page_id>:r<row>:<reason>:<engine>`` for a row,
``<field_id>:<code>`` for a cell), so a problem raised again by a later audit revision is the same entry.

**Gate** (``judge``): one record per page (``record_idx`` 0, as kb-ocr pages are records). Its uncertain list is
the page entries (uncleared, scope ``page``) plus an entry for every code on every cell of the WI-07c comparison,
and for every extra cell code (``no_bbox_crop`` from the crop index). Status:

- ``disputed`` when any page entry is present (r3.3: a missing or failed route blocks the page; there is no
  "continue with N-1 routes");
- ``verified`` (``verified_by: auto``) when the page has at least one cell, every cell is WI-07c ``agree`` with no
  missing route, and the uncertain list is empty (K1, r2.3 source 1: decided by the vote, never relaxed here);
- ``pending_confirm`` otherwise.

WI-32b: the advisory doubts of an agreeing cell (``no_geometry``, ``ordinal_shift:<e>``; see ``compare``) are
not uncertain entries: they ride on the field as ``notes`` (trace), so a page whose cells all agree is still
``verified`` with an empty uncertain list and the confirm page does not list those cells.

The gate decides a **candidate** only; ``effective`` is advanced by WI-08c writeback alone (r4.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

VERIFIED, PENDING, DISPUTED = "verified", "pending_confirm", "disputed"
PAGE, ROW, CELL = "page", "row", "cell"
AGREE = "agree"
ENGINES = ("vision", "mlx", "claude", "codex", "pdftext", "tesseract")


class UnknownReason(ValueError):
    """A code that is not in the reason table, or an engine reason without (or with a bad) engine."""


@dataclass(frozen=True)
class Reason:
    scope: str
    engine: bool
    fix_action: str
    clear_evidence: str


_RERUN = ("rerun kb-ocr for route {engine} (klode-kit kb-ocr --batch <inbox> --routes {engine} --redo), then "
          "kb-audit: the new run gives a new audit revision and the page is compared again")
_ROUTE_BACK = "route {engine} has lines for this page in a newer run (new run_id -> new audit_rev)"
_PICK = "no automatic repair: choose the value on the confirm page"
_PICKED = "a human choice for the cell written back by kb-audit --writeback"

REASONS: dict[str, Reason] = {
    "route_failed": Reason(PAGE, True, _RERUN, _ROUTE_BACK),
    "route_missing_page": Reason(PAGE, True, _RERUN, _ROUTE_BACK),
    "parse_error": Reason(PAGE, True, "the answer broke the output schema twice; " + _RERUN, _ROUTE_BACK),
    "degenerate_output": Reason(PAGE, True, "MLX degenerated (alt prompt -> tesseract fallback); " + _RERUN,
                                "a replacement reading of route {engine} is recorded in the kb-ocr receipt"),
    "route_disabled": Reason(PAGE, True, "enable {engine} in config/routes_enabled.json (after its isolation "
                                         "check passes), then " + _RERUN, _ROUTE_BACK),
    "route_unavailable": Reason(PAGE, True, "install the {engine} engine, or for a cloud route mask the batch "
                                            "first (cloud routes send only _masked/ images), then " + _RERUN,
                                _ROUTE_BACK),
    "effort_locked": Reason(PAGE, True, "remove the {engine}.effort override from klode-kit.toml (the effort is "
                                        "locked), then " + _RERUN, _ROUTE_BACK),
    "unaligned": Reason(ROW, True, _PICK + " (route {engine} has no line for this row)", _PICKED),
    "merged_rows": Reason(ROW, True, _PICK + " (route {engine} read two rows as one)", _PICKED),
    "missing_cell": Reason(CELL, True, _PICK + " (route {engine} has no value for this cell)", _PICKED),
    "ordinal_shift": Reason(CELL, True, _PICK + " (route {engine} has a different number of values on this "
                                               "row, so its values may sit one place off)", _PICKED),
    "extra_values": Reason(CELL, True, _PICK + " (route {engine} read a value on this page that no row "
                                              "holds)", _PICKED),
    "swap_suspect": Reason(CELL, True, _PICK + " (route {engine} reads this value and the next row's swapped)",
                           _PICKED),
    "tie": Reason(CELL, False, _PICK + " (the routes split evenly; no recommendation)", _PICKED),
    "all_differ": Reason(CELL, False, _PICK + " (every route reads a different value)", _PICKED),
    "no_geometry": Reason(CELL, False, _PICK + " (the row has no exact geometry; the crop is the whole row)",
                          _PICKED),
    "no_bbox_crop": Reason(CELL, False, _PICK + " (no box for the cell: the crop is the whole row or half the "
                                         "page)", _PICKED),
}
PAGE_REASONS = frozenset(r for r, spec in REASONS.items() if spec.scope == PAGE)


@dataclass(frozen=True)
class Verdict:
    rec_id: str
    page_id: str
    status: str
    verified_by: str | None
    uncertain: tuple[dict, ...]
    fields: tuple[dict, ...]


def slot(row: int, kind: str, k: int) -> str:
    """The field slot of WI-07b cell ``(row, kind, k)``: ``r<row:03d>-<kind>-<k>``."""
    return f"r{row:03d}-{kind}-{k}"


def split(code: str) -> tuple[str, str | None]:
    """``"missing_cell:codex"`` -> ``("missing_cell", "codex")``; checks the code against the table."""
    if not isinstance(code, str):
        raise UnknownReason(f"uncertain code must be a string: {code!r}")
    reason, sep, engine = code.partition(":")
    spec = REASONS.get(reason)
    if spec is None:
        raise UnknownReason(f"unknown uncertain reason {reason!r} (code {code!r})")
    if spec.engine:
        if not sep or engine not in ENGINES:
            raise UnknownReason(f"uncertain code {code!r} needs one engine of {ENGINES}")
        return reason, engine
    if sep:
        raise UnknownReason(f"uncertain reason {reason!r} takes no engine (code {code!r})")
    return reason, None


def entry(code: str, *, page_id: str, field_id: str | None = None, row: int | None = None) -> dict:
    """The uncertain entry of one code (module docstring)."""
    reason, engine = split(code)
    spec = REASONS[reason]
    if spec.scope == PAGE:
        uid = f"{page_id}:{engine}:{reason}"
    elif spec.scope == ROW:
        if row is None:
            raise UnknownReason(f"row-scope reason {reason!r} needs the row")
        uid = f"{page_id}:r{row:03d}:{reason}:{engine}"
    else:
        if field_id is None:
            raise UnknownReason(f"cell-scope reason {reason!r} needs the field_id")
        uid = f"{field_id}:{code}"
    out: dict[str, Any] = {"id": uid, "reason": reason, "scope": spec.scope}
    if engine is not None:
        out["engine"] = engine
    if spec.scope == CELL:
        out["field_id"] = field_id
    out["fix_action"] = spec.fix_action.format(engine=engine)
    out["clear_evidence"] = spec.clear_evidence.format(engine=engine)
    return out


def _page_reason(route: str, status: str, error: str) -> str:
    if status != "route_failed":
        return status
    if error.startswith("route_disabled"):
        return "route_disabled"
    if error.startswith("route_unavailable") or "send only _masked/ images" in error:
        return "route_unavailable"
    if error.startswith("effort_locked"):
        return "effort_locked"
    if route == "mlx" and "degenerate_output" in error:
        return "degenerate_output"
    return "route_failed"


def page_entries(page: Mapping[str, Any]) -> list[dict]:
    """Page entries of one ``pages[]`` item of the kb-ocr batch receipt: one per route that is neither ``done``
    nor ``skipped`` (text layer), in the receipt's route order."""
    out = []
    for route, res in page.get("routes", {}).items():
        status = res.get("status")
        if status in ("done", "skipped"):
            continue
        reason = _page_reason(route, str(status), str(res.get("error") or ""))
        out.append(entry(f"{reason}:{route}", page_id=page["page_id"]))
    return out


def judge(comparison: Any, *, rec_id: str, page_id: str, page_uncertain: Iterable[Mapping] = (),
          cell_codes: Mapping[tuple[int, str, int], Iterable[str]] | None = None) -> Verdict:
    """Decide the candidate of the page's record (module docstring)."""
    extra = cell_codes or {}
    entries: dict[str, dict] = {}
    for e in page_uncertain:
        if e.get("cleared_by"):
            continue
        if e.get("scope") != PAGE or e.get("reason") not in PAGE_REASONS:
            raise UnknownReason(f"not a page-level uncertain entry: {dict(e)!r}")
        entries.setdefault(e["id"], dict(e))
    fields = []
    for v in comparison.cells:
        key = (v.row, v.kind, v.k)
        fid = f"{rec_id}/{slot(*key)}"
        codes = sorted(set(v.uncertain) | set(extra.get(key, ())))
        for code in codes:
            e = entry(code, page_id=page_id, field_id=fid, row=v.row)
            entries.setdefault(e["id"], e)
        fields.append({"field_id": fid, "verdict": v.verdict, "recommended": v.recommended,
                       "missing": list(v.missing), "uncertain": codes,
                       "notes": list(v.notes)})
    uncertain = tuple(entries.values())
    if any(e["scope"] == PAGE for e in uncertain):
        status, by = DISPUTED, None
    elif (comparison.cells and not uncertain
          and all(v.verdict == AGREE and not v.missing and not v.uncertain for v in comparison.cells)):
        status, by = VERIFIED, "auto"
    else:
        status, by = PENDING, None
    return Verdict(rec_id, page_id, status, by, uncertain, tuple(fields))
