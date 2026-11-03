# WI-40 — a record's date is never a date of birth; re-date records that got one (P36)

## Why (real finding, 2026-11-03)

`audit.record_meta.page_date` takes, per route, the earliest date on a line holding `日期`, else **the earliest date
anywhere on the page**. Japanese lab reports label their dates `採取日` / `検査日` / `受付日` / `報告日`, not `日期`, so the
fallback runs — and the earliest date on a patient's report is the date of birth (`生年月日`). Four real records were
dated decades before the visit and their views were named after it. A record's date, once set, is kept by every
later writeback ("a redo never renames its views"), so fixing the rule alone does not repair them.

## What

### 1. `record_meta` rules

- `DATE_LABELS` (replaces the single `DATE_LABEL`): `日期`, `采样`, `采集`, `送检`, `报告时间`, `检验时间`, `検査日`, `採取日`,
  `採血日`, `受付日`, `報告日`, `依頼日`, `実施日`, `診療日`, `処方日`, `交付年月日`, `調剤日` (matched on the NFKC fold, white space
  removed, like today). A line holding any of them is a labelled line.
- `BIRTH_LABELS`: `生年月日`, `出生日期`, `出生`, `生日`, `誕生日`, `DOB`, `Birth` (case-insensitive for the Latin ones). A date on
  a line holding one of them is **never** a candidate (neither labelled nor anywhere), and neither is a date on the line
  right after a line that holds only a birth label (label and value split over two lines).
- Order of preference per route: labelled dates, else anywhere dates (both without birth dates); the rest of
  `page_date` (votes, ties) unchanged. Module docstring updated.

### 2. `kb-audit --redate --batch B [--apply] [--project DIR]`

For every record of batch B (any status), re-derive the date with the rules of section 1 from the newest audit
revision's routes (same inputs `writeback._page_meta` / `record_meta.derive` use). Report records whose stored date
(the `_by_id` front `date`) differs.

- Without `--apply`: nothing written; receipt lists them (`result: "planned"`).
- With `--apply`: append one line per changed record to `work/B/audit/redate.jsonl`
  `{op: "redate", rec_id, old, new, source: "page"|"inbox_dir"|"batch", at}`; set the `_by_id` front `date` to `new`;
  rebuild the views (`records/`, `verified/` names follow the new date, via the existing views code). Revision bodies
  under `_rev/` are **not** rewritten (append-only store).
- The date a later writeback writes into a new revision body is the front date (now the new one) — check that the
  existing precedence already gives that; adjust if not.
- `records_to_txt`: the exported record date is the `_by_id` front date when the record has a `redate` event,
  otherwise as today (the bound body's date). Deterministic.
- Receipt (one JSON line, `klode-kit.redate_receipt/1`): `{schema, result: planned|applied|nothing|refused,
  exit_code (0 / 30), batch_id, records: [{rec_id, old, new, source}]}`. Refused (exit 30, nothing written): bad batch
  id, no `work/`, batch has no audit revision. Mutually exclusive with `--again/--reopen/--writeback/--attribute`
  (usage exit 2). Under the same work lock as writeback.

## Tests first (unit, real tmp fixtures; no mock of the record store; no real personal data — invent dates)

1. `page_date`: a page with a `生年月日` line (earliest date) and a `採取日` line -> the 採取日 date; same with the birth date
   on the line after a bare `生年月日` line; a page with only a birth date -> `None` (then the inbox dir date wins in
   `derive`); `日期` pages unchanged (regression on the existing tests).
2. `--redate` plan: a record dated with a birth date -> planned with the new date; a correct record not listed; nothing
   written (tree byte-identical).
3. `--redate --apply`: `redate.jsonl` line, `_by_id` date updated, views renamed, `_rev/` bodies byte-identical; second
   run -> `nothing`.
4. after apply, `records_to_txt` exports the new date; a record without a redate event exports the body date as before.
5. a writeback after apply writes the new date into the new revision body.
6. refusals and usage errors as listed.

## Not in scope

The type rule, the gate, the confirm page, other date fields inside a record. No run against a real data project by
the implementer. Do not touch `tests/unit/test_wua.py` (6 pre-existing failures).
