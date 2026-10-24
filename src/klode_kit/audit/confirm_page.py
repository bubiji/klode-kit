"""WI-08b: the confirm page ``confirm.html`` and its ``choices.json`` export (design 02 r2.3, r3.3, r4.1;
04 U8; matrix 08b-*).

Usage::

    klode-kit confirm-page --batch <batch_id> [--project <data project>] [--rev N] [--advice <advice.json>]
    node <klode_kit>/templates/confirm_export.js <confirm.html> [<selections.json>] > choices.json

**Input.** One audit revision ``work/<batch>/audit/a<N>/`` as WI-08a leaves it: ``runs.json``,
``cells.jsonl``, ``candidates.jsonl`` (each candidate's ``fields`` carry ``verdict``, ``recommended``,
``votes``, ``item_label``, ``crop``) and the WI-07d crops; optionally ``advice.json`` (kb-verifier: a list of
``{field_id, suggestion, evidence?}``) and the records ``records/_by_id/<rec_id>.md`` (JSON front matter,
WI-08c) for the effective revision.

**Page** (written once through ``stage.write_once``, read-only afterwards; templates ``templates/confirm.html``
and ``templates/confirm_export.js``, installed with the package):

- one section per record with ``生效 r<e> (<status>) / 候选 r<c> (<status>[: <engine>:<reason>, ...])``
  (``生效 无`` before the first writeback; ``r<c>`` is the candidate of this audit revision in the record file,
  else the record's latest rev + 1);
- one item per field to confirm (verdict not ``agree``, or any uncertain code, or a missing route): the crop,
  one candidate per route of the revision in ``ENGINES`` order labelled with the route (a route without a value
  shows ``（缺）`` and has no radio), ``★推荐`` beside the routes holding the majority value (only for
  ``majority``), ``平票`` / ``全不同`` labels, and the options ``都不对``, ``不是字段`` (none +
  ``not_a_field``) and ``手输``;
- a record with a page-level uncertain entry (r3.3) shows ``缺路，不可点选`` and the entries' fix commands on
  every item and has no input at all; a record with nothing to confirm shows ``无分歧``;
- kb-verifier advice is shown as a hint on its field only; it is not part of the data block, so the export can
  never carry it, and nothing is preselected.

**Local only.** The crops are referenced by relative path ``crops/<name>.png`` (not inlined as data URIs): the
page stays small (no image bytes; a data URI would add ~1.33x each crop's PNG size), the page file alone carries
no pixels if it is copied somewhere, and the images stay where WI-07d wrote them. The trade-off is that the page
must be opened in place (``work/<batch>/audit/a<N>/confirm.html``). The page references no network resource
(CSP ``default-src 'none'``), is never written under ``_masked/`` and is never registered for sending. It holds
only the disputed cells' readings, never the documents' header lines.

**Seal.** A file cannot contain the plain sha256 of itself, so the page carries a stamp
``<meta name="klode-kit-confirm-sha256" content="<hex>">`` (the first such meta of the file) whose value is
``confirm_sha256(html)``: the sha256 of the file with the stamp's 64 hex digits replaced by ``0``. The export
puts the stamp into ``choices.confirm_sha256``; node and ``export_choices`` refuse a page whose bytes no longer
match its stamp (edited), and the writeback (WI-08c) recomputes it from ``a<N>/confirm.html``.

**Export** (``templates/confirm_export.js``; ``export_choices`` is the same exporter in Python): a list of clicks
``{field_id, kind: route|manual|none, route?, value?, reason?}``, the last click on a field winning, gives
``{schema, batch_id, audit_rev, confirm_sha256, fingerprints, items}``. ``fingerprints`` are the revision's run
ids (``runs.json``, routes with a run); each item is ``{rec_id, rev_base, field_id, cell, choice, seen}`` (+
``evidence: {reason: not_a_field}``), in page order, at most one per field (00b1-14 / F17 cannot arise).
``rev_base`` is the record's latest rev (0 without a record file); ``seen`` is every route's reading of the cell
in ``cells.jsonl`` (``null`` when it has none). A blank typed value (only white space in the ``str.isspace()`` sense) is
exported as ``none`` (r2.3). Unclicked fields give no item, so a partly clicked record is exported as is and
refused by the writeback (08c-18). Refused clicks: a field not on the page or on a blocked page, an unknown kind
(``partial`` was removed by r3.3), a route without a value, a reason other than ``not_a_field`` or keys that do
not belong to the kind.

Exit codes of ``confirm-page``: 0 written (or already written: the existing page is kept), 2 usage (bad batch id,
no audit revision), 30 the revision's files are inconsistent (crop outside the revision, unknown field, bad
advice or record file).
"""

from __future__ import annotations

import argparse
import hashlib
import html as H
import json
import os
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Mapping

from klode_kit.audit import gate as G
from klode_kit.audit import stage

TEMPLATES = Path(str(resources.files("klode_kit"))) / "templates"
HTML_TEMPLATE = TEMPLATES / "confirm.html"
EXPORT_JS = TEMPLATES / "confirm_export.js"

SCHEMA = "klode-kit.choices/1"
DATA_SCHEMA = "klode-kit.confirm_page/1"
STAMP_NAME = "klode-kit-confirm-sha256"
DATA_ID = "klode-kit-confirm-data"
USAGE_EXIT, CONTRACT_EXIT = 2, 30

_STAMP_PREFIX = f'<meta name="{STAMP_NAME}" content="'.encode("ascii")
_DATA_OPEN = f'<script type="application/json" id="{DATA_ID}">'
_BATCH = re.compile(r"[0-9a-f]{12}")
_REV_DIR = re.compile(r"a([1-9][0-9]*)")
_CROP_NAME = re.compile(r"[^/\\]+\.png")
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
# blank = only white space as str.isspace() defines it; spelled out so the JS exporter uses the very same set
_BLANK = re.compile(r"[\t\n\x0b\x0c\r\x1c-\x1f \x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*")
_KEYS = {"route": {"field_id", "kind", "route"}, "manual": {"field_id", "kind", "value"},
         "none": {"field_id", "kind", "reason"}}
_VERDICT_LABEL = {"majority": "多数", "tie": "平票", "all_differ": "全不同", "agree": "一致（待确认）"}


class ConfirmError(Exception):
    def __init__(self, message: str, exit_code: int = CONTRACT_EXIT):
        super().__init__(message)
        self.exit_code = exit_code


# --------------------------------------------------------------------------- reading the revision


def _revisions(project: Path, batch_id: str) -> list[int]:
    root = project / "work" / batch_id / "audit"
    if not root.is_dir():
        return []
    return sorted(int(m.group(1)) for p in root.iterdir() if p.is_dir() and (m := _REV_DIR.fullmatch(p.name)))


def _rev_dir(project: Path, batch_id: str, n: int | None) -> tuple[int, Path]:
    if not (isinstance(batch_id, str) and _BATCH.fullmatch(batch_id)):
        raise ConfirmError(f"batch id must be 12 hex characters: {batch_id!r}", USAGE_EXIT)
    revs = _revisions(project, batch_id)
    if not revs:
        raise ConfirmError(f"batch {batch_id} has no audit revision; run kb-audit first", USAGE_EXIT)
    if n is None:
        n = revs[-1]
    if n not in revs:
        raise ConfirmError(f"batch {batch_id} has no audit revision a{n} (has: "
                           f"{', '.join(f'a{r}' for r in revs)})", USAGE_EXIT)
    adir = project / "work" / batch_id / "audit" / f"a{n}"
    if not (adir / "candidates.jsonl").is_file() or not (adir / "cells.jsonl").is_file():
        raise ConfirmError(f"{adir} is incomplete (no candidates.jsonl / cells.jsonl); rerun kb-audit", USAGE_EXIT)
    return n, adir


def _jsonl(path: Path) -> list[dict]:
    try:
        rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfirmError(f"{path}: not readable JSON lines: {exc}") from exc
    if not all(isinstance(r, dict) for r in rows):
        raise ConfirmError(f"{path}: every line must be an object")
    return rows


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfirmError(f"{path}: not readable JSON: {exc}") from exc


def _record(project: Path, rec_id: str) -> dict | None:
    path = project / "records" / "_by_id" / f"{rec_id}.md"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 4)
    try:
        if not text.startswith("---\n") or end < 0:
            raise ValueError("no front matter block")
        front = json.loads(text[4:end])
    except ValueError as exc:
        raise ConfirmError(f"{path}: {exc}") from exc
    if not isinstance(front, dict) or front.get("rec_id") != rec_id:
        raise ConfirmError(f"{path}: front matter is not the record {rec_id}")
    return front


def _advice(path: Path | None, shown: set[str]) -> dict[str, list[dict]]:
    if path is None or not path.is_file():
        return {}
    data = _json(path)
    good = isinstance(data, list) and all(
        isinstance(a, dict) and isinstance(a.get("field_id"), str) and isinstance(a.get("suggestion"), str)
        for a in data)
    if not good:
        raise ConfirmError(f"{path}: advice must be a list of {{field_id, suggestion, evidence?}} objects")
    out: dict[str, list[dict]] = {}
    for a in data:
        if a["field_id"] in shown:
            out.setdefault(a["field_id"], []).append(a)
    return out


def _crop_src(crop: Any, batch_id: str, n: int, adir: Path) -> str | None:
    if crop is None:
        return None
    prefix = f"work/{batch_id}/audit/a{n}/crops/"
    name = crop[len(prefix):] if isinstance(crop, str) and crop.startswith(prefix) else ""
    if not _CROP_NAME.fullmatch(name) or not (adir / "crops" / name).is_file():
        raise ConfirmError(f"crop {crop!r} is not a crop of audit revision a{n}")
    return f"crops/{name}"


def _shown(f: Mapping[str, Any]) -> bool:
    return f.get("verdict") != G.AGREE or bool(f.get("uncertain")) or bool(f.get("missing"))


def _labels(cand: Mapping[str, Any], front: Mapping[str, Any] | None) -> tuple[str, int]:
    """``(生效 ... / 候选 ..., rev_base)``."""
    eff = (front or {}).get("effective")
    revs = [c.get("rev") for c in (front or {}).get("candidates", [])]
    if eff:
        revs.append(eff.get("rev"))
    revs = [r for r in revs if isinstance(r, int)]
    current = max(revs, default=0)
    cand_rev = next((c["rev"] for c in (front or {}).get("candidates", [])
                     if c.get("audit_rev") == cand["audit_rev"] and isinstance(c.get("rev"), int)), current + 1)
    eff_txt = f"r{eff['rev']} ({eff['status']})" if eff else "无"
    status = cand["status"]
    if status == G.DISPUTED:
        reasons = [f"{u.get('engine')}:{u['reason']}" for u in cand["uncertain"] if u.get("scope") == G.PAGE]
        status = f"{status}: {', '.join(reasons)}" if reasons else status
    return f"生效 {eff_txt} / 候选 r{cand_rev} ({status})", current


# --------------------------------------------------------------------------- rendering


def _e(text: Any) -> str:
    return H.escape(str(text), quote=True)


def _data_json(data: Mapping) -> str:
    text = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for ch, esc in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"), ("\u2028", "\\u2028"),
                    ("\u2029", "\\u2029")):
        text = text.replace(ch, esc)
    return text


def _item_html(f: dict, view: dict, routes: list[str], blocked: list[dict], advice: list[dict]) -> str:
    fid = f["field_id"]
    name = f"kk:{fid}"
    verdict = f.get("verdict")
    star = set()
    if verdict == "majority" and f.get("recommended") is not None:
        star = {e for v, es in f.get("votes", []) if v == f["recommended"] for e in es}
    out = [f'<fieldset class="kk-item" data-field-id="{_e(fid)}" data-row="{_e(view["cell"]["row"])}" '
           f'data-verdict="{_e(verdict)}">']
    label = f.get("item_label") or "?"
    out.append(f"<legend>{_e(label)} · <code>{_e(fid.split('/', 1)[1])}</code> · "
               f"<span class=\"kk-verdict\">{_e(_VERDICT_LABEL.get(verdict, verdict))}</span></legend>")
    if view["crop"]:
        out.append(f'<img class="kk-crop" src="{_e(view["crop"])}" alt="分歧小图">')
    else:
        out.append('<p class="kk-note">无小图</p>')
    if blocked:
        out.append('<p class="kk-blocked">缺路，不可点选。修复：</p><ul class="kk-blocked">')
        out += [f"<li>{_e(u.get('engine'))}:{_e(u['reason'])} — <code>{_e(u.get('fix_action', ''))}</code></li>"
                for u in blocked]
        out.append("</ul>")
    out.append('<ul class="kk-cands">')
    for e in routes:
        value = view["seen"].get(e)
        li = [f'<li class="kk-cand" data-route="{_e(e)}">']
        if value is None:
            li.append(f'{_e(e)}：<span class="kk-missing">（缺）</span>')
        elif blocked:
            li.append(f'{_e(e)}：<span class="kk-value">{_e(value)}</span>')
        else:
            li.append(f'<label><input type="radio" name="{_e(name)}" value="route:{_e(e)}"> {_e(e)}：'
                      f'<span class="kk-value">{_e(value)}</span></label>')
            if e in star:
                li.append('<span class="kk-star">★推荐</span>')
        li.append("</li>")
        out.append("".join(li))
    out.append("</ul>")
    codes = f.get("uncertain") or []
    if codes:
        out.append(f'<p class="kk-codes">{_e(", ".join(codes))}</p>')
    for a in advice:
        ev = a.get("evidence")
        ev_txt = f"（依据：{_e(ev if isinstance(ev, str) else json.dumps(ev, ensure_ascii=False))}）" if ev else ""
        out.append(f'<p class="kk-advice">kb-verifier 建议（仅供参考，不会自动选中）：'
                   f'<span class="kk-value">{_e(a["suggestion"])}</span>{ev_txt}</p>')
    if not blocked:
        out.append('<ul class="kk-other">'
                   f'<li><label><input type="radio" name="{_e(name)}" value="none"> 都不对</label></li>'
                   f'<li><label><input type="radio" name="{_e(name)}" value="none:not_a_field"> 不是字段（删除）'
                   '</label></li>'
                   f'<li><label><input type="radio" name="{_e(name)}" value="manual"> 手输：</label>'
                   '<input type="text" class="kk-manual" autocomplete="off"></li></ul>')
    out.append("</fieldset>")
    return "\n".join(out)


def render(project: str | os.PathLike, batch_id: str, n: int | None = None, *,
           advice_path: str | os.PathLike | None = None) -> bytes:
    """The confirm page of audit revision ``a<n>`` (default: the newest) as bytes, sealed (module docstring)."""
    project = Path(project)
    n, adir = _rev_dir(project, batch_id, n)
    audit_rev = f"{batch_id}/a{n}"
    runs = _json(adir / "runs.json") if (adir / "runs.json").is_file() else {}
    if not isinstance(runs, dict):
        raise ConfirmError(f"{adir / 'runs.json'}: not an object")
    routes = [e for e in G.ENGINES if e in runs] + sorted(e for e in runs if e not in G.ENGINES)
    fingerprints = {e: runs[e] for e in routes if isinstance(runs[e], str) and runs[e]}
    cells = {c.get("field_id"): c for c in _jsonl(adir / "cells.jsonl")}
    cands = _jsonl(adir / "candidates.jsonl")
    shown_ids = [f["field_id"] for c in cands for f in c.get("fields", []) if _shown(f)]
    if len(shown_ids) != len(set(shown_ids)):
        raise ConfirmError(f"{adir / 'candidates.jsonl'}: a field_id appears twice")
    advice = _advice(Path(advice_path) if advice_path is not None else adir / "advice.json", set(shown_ids))

    sections, records = [], []
    n_items = n_blocked = 0
    for cand in cands:
        front = _record(project, cand["rec_id"])
        label, rev_base = _labels(cand, front)
        blocked = [u for u in cand.get("uncertain", []) if u.get("scope") == G.PAGE]
        fields, items = [], []
        for f in cand.get("fields", []):
            if not _shown(f):
                continue
            cell = cells.get(f["field_id"])
            if cell is None or not isinstance(cell.get("cell"), dict):
                raise ConfirmError(f"field {f['field_id']} is not in {adir / 'cells.jsonl'}")
            seen = {e: (cell.get("candidates", {}).get(e) or {}).get("value") for e in routes}
            view = {"field_id": f["field_id"], "cell": cell["cell"], "seen": seen,
                    "item_label": f.get("item_label"), "verdict": f.get("verdict"),
                    "recommended": f.get("recommended"),
                    "crop": _crop_src(f.get("crop"), batch_id, n, adir)}
            fields.append({k: view[k] for k in ("field_id", "cell", "seen", "item_label", "verdict", "recommended")})
            items.append(_item_html(f, view, routes, blocked, advice.get(f["field_id"], [])))
        records.append({"rec_id": cand["rec_id"], "page_id": cand["page_id"], "status": cand["status"],
                        "rev_base": rev_base, "clickable": not blocked, "fields": fields})
        n_items += 0 if blocked else len(items)
        n_blocked += 1 if blocked else 0
        head = [f'<section class="kk-record" data-rec-id="{_e(cand["rec_id"])}" '
                f'data-status="{_e(cand["status"])}">',
                f'<h2>{_e(cand["page_id"])} <small><code>{_e(cand["rec_id"])}</code></small></h2>',
                f'<p class="kk-revs">{_e(label)}</p>']
        if blocked:
            head.append('<p class="kk-blocked">本页缺路：缺路，不可点选。先修复缺的路，重新 kb-audit 后再确认：</p>'
                        '<ul class="kk-blocked">'
                        + "".join(f"<li>{_e(u.get('engine'))}:{_e(u['reason'])} — "
                                  f"<code>{_e(u.get('fix_action', ''))}</code></li>" for u in blocked)
                        + "</ul>")
        if not items:
            head.append('<p class="kk-none">无分歧</p>' if not blocked else '<p class="kk-none">无可确认的字段</p>')
        sections.append("\n".join(head + items + ["</section>"]))

    data = {"schema": DATA_SCHEMA, "batch_id": batch_id, "audit_rev": audit_rev, "fingerprints": fingerprints,
            "routes": routes, "records": records}
    summary = (f"{len(cands)} 条记录；待点选 {n_items} 处；缺路不可点选的记录 {n_blocked} 条"
               if n_items or n_blocked else f"{len(cands)} 条记录；无分歧")
    script = EXPORT_JS.read_text(encoding="utf-8")
    if "</script" in script.lower() or "<!--" in script:
        raise ConfirmError(f"{EXPORT_JS} cannot be embedded in a script element")
    values = {"stamp": "0" * 64, "batch_id": _e(batch_id), "audit_rev": _e(audit_rev), "summary": _e(summary),
              "body": "\n".join(sections), "data": _data_json(data), "script": script.rstrip("\n")}
    template = HTML_TEMPLATE.read_text(encoding="utf-8")
    page = _PLACEHOLDER.sub(lambda m: values[m.group(1)], template).encode("utf-8")
    return _seal(page)


def _stamp_at(html: bytes) -> int:
    at = html.find(_STAMP_PREFIX)
    if at < 0:
        raise ConfirmError("not a klode-kit confirm page (no sha256 stamp)")
    return at + len(_STAMP_PREFIX)


def _zeroed(html: bytes, start: int) -> bytes:
    return html[:start] + b"0" * 64 + html[start + 64:]


def _seal(page: bytes) -> bytes:
    start = _stamp_at(page)
    digest = hashlib.sha256(_zeroed(page, start)).hexdigest().encode("ascii")
    return page[:start] + digest + page[start + 64:]


def confirm_sha256(html: bytes | str) -> str:
    """The page's sealed sha256 (module docstring, "Seal") computed from its bytes."""
    data = html.encode("utf-8") if isinstance(html, str) else bytes(html)
    return hashlib.sha256(_zeroed(data, _stamp_at(data))).hexdigest()


def write(project: str | os.PathLike, batch_id: str, n: int | None = None, *,
          advice_path: str | os.PathLike | None = None) -> Path:
    """Render and write ``a<n>/confirm.html`` once (``stage.WriteOnceViolation`` when it exists)."""
    project = Path(project)
    n, adir = _rev_dir(project, batch_id, n)
    if (adir / "confirm.html").exists():
        raise stage.WriteOnceViolation(f"{adir / 'confirm.html'} already exists: an audit revision is written once")
    return stage.write_once(adir, "confirm.html", render(project, batch_id, n, advice_path=advice_path))


# --------------------------------------------------------------------------- export (mirror of the JS)


def page_data(html: bytes | str) -> dict:
    """The data block of a confirm page."""
    text = html.decode("utf-8") if isinstance(html, bytes) else html
    i = text.find(_DATA_OPEN)
    j = text.find("</script>", i) if i >= 0 else -1
    if i < 0 or j < 0:
        raise ConfirmError("the confirm page has no data block")
    try:
        data = json.loads(text[i + len(_DATA_OPEN):j])
    except json.JSONDecodeError as exc:
        raise ConfirmError(f"the confirm page's data block is not JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != DATA_SCHEMA:
        raise ConfirmError("the confirm page's data block is not klode-kit.confirm_page/1")
    return data


def _choice(sel: Mapping[str, Any], field: Mapping[str, Any]) -> dict:
    kind = sel.get("kind")
    if not isinstance(kind, str) or kind not in _KEYS:
        raise ConfirmError(f"unknown choice kind {kind!r} for {sel.get('field_id')}", USAGE_EXIT)
    extra = set(sel) - _KEYS[kind]
    if extra:
        raise ConfirmError(f"keys {sorted(extra)} do not belong to a {kind} choice", USAGE_EXIT)
    fid = field["field_id"]
    if kind == "route":
        r = sel.get("route")
        if not isinstance(r, str) or not isinstance(field["seen"].get(r), str):
            raise ConfirmError(f"route {r!r} has no value for {fid}", USAGE_EXIT)
        return {"choice": {"kind": "route", "route": r}}
    if kind == "manual":
        value = sel.get("value")
        if not isinstance(value, str):
            raise ConfirmError(f"a typed value must be a string ({fid})", USAGE_EXIT)
        if _BLANK.fullmatch(value):
            return {"choice": {"kind": "none"}}
        return {"choice": {"kind": "manual", "value": value}}
    if "reason" in sel and sel["reason"] != "not_a_field":
        raise ConfirmError(f"the only reason for 'none' is not_a_field ({fid})", USAGE_EXIT)
    out: dict = {"choice": {"kind": "none"}}
    if sel.get("reason") == "not_a_field":
        out["evidence"] = {"reason": "not_a_field"}
    return out


def export_choices(html: bytes | str, selections: Iterable[Mapping[str, Any]]) -> dict:
    """``choices.json`` for the clicks ``selections`` on the page ``html`` (module docstring, "Export")."""
    raw = html.encode("utf-8") if isinstance(html, str) else bytes(html)
    start = _stamp_at(raw)
    stamp = raw[start:start + 64].decode("latin-1")
    if not re.fullmatch(r"[0-9a-f]{64}", stamp) or confirm_sha256(raw) != stamp:
        raise ConfirmError("the confirm page was edited after it was written (its bytes do not match its stamp)")
    data = page_data(raw)
    index = {f["field_id"]: (rec, f) for rec in data["records"] for f in rec["fields"]}
    picked: dict[str, dict] = {}
    for sel in selections:
        fid = sel.get("field_id") if isinstance(sel, Mapping) else None
        if not isinstance(fid, str) or fid not in index:
            raise ConfirmError(f"field {fid!r} is not on this confirm page", USAGE_EXIT)
        rec, field = index[fid]
        if not rec["clickable"]:
            raise ConfirmError(f"field {fid} belongs to a page with a missing route: not selectable", USAGE_EXIT)
        picked[fid] = _choice(sel, field)
    items = []
    for rec in data["records"]:
        for f in rec["fields"]:
            if f["field_id"] not in picked:
                continue
            item = {"rec_id": rec["rec_id"], "rev_base": rec["rev_base"], "field_id": f["field_id"],
                    "cell": f["cell"], "choice": picked[f["field_id"]]["choice"], "seen": f["seen"]}
            if "evidence" in picked[f["field_id"]]:
                item["evidence"] = picked[f["field_id"]]["evidence"]
            items.append(item)
    return {"schema": SCHEMA, "batch_id": data["batch_id"], "audit_rev": data["audit_rev"],
            "confirm_sha256": stamp, "fingerprints": data["fingerprints"], "items": items}


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit confirm-page",
                                     description="Write the local confirm page of a batch's audit revision.")
    parser.add_argument("--batch", required=True, help="batch id (12 hex characters)")
    parser.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    parser.add_argument("--rev", type=int, default=None, help="audit revision N of a<N> (default: the newest)")
    parser.add_argument("--advice", type=Path, default=None,
                        help="kb-verifier advice.json (default: a<N>/advice.json when present)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    project = Path.cwd() if args.project is None else args.project
    try:
        n, adir = _rev_dir(project, args.batch, args.rev)
        target = adir / "confirm.html"
        if target.exists():
            print(f"confirm-page: {target} already written (an audit revision is written once); kept",
                  file=sys.stderr)
            print(target)
            return 0
        path = write(project, args.batch, n, advice_path=args.advice)
    except ConfirmError as exc:
        print(f"confirm-page: {exc}", file=sys.stderr)
        return exc.exit_code
    print(path)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit confirm-page``
    sys.exit(main())
