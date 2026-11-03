# WI-38 — `kb-audit --again`: a second confirm pass on a pending / disputed record (P33)

## Why (a real finding, 2026-11-03)

The confirm page of audit revision `a<N>` is rendered once with `rev_base` = each record's rev at that moment. A
first human writeback that leaves fields undecided moves the record to `r<cur+1>` with status `disputed` (or
`pending_confirm`). Every later choice exported from that page is then refused per item with
`rev_base 1 is not the record's current rev 2 (stale page)`:

- `confirm-page --batch B` keeps the existing page ("an audit revision is written once");
- `kb-audit --batch B` reuses `a<N>` when the OCR runs did not change (no `a<N+1>`);
- `kb-audit --reopen` refuses a record that is not verified.

So the owner (or a D-43 reviewer) cannot finish a record in two sittings. Real data: 18 father batches, 43 chunks,
~800 second-pass decisions are stuck.

## What

`klode-kit kb-audit --again --batch <batch_id> [--project DIR]`

For the batch's newest audit revision `a<N>`: every record whose **current rev** is a candidate of `a<N>` with status
`pending_confirm` or `disputed` (never a verified one — that is `--reopen`) gets a page that lists **only its still
undecided fields** (the fields the current revision body keeps as uncertain entries, plus its page items that are
not yet acknowledged), each with `rev_base` = the record's current rev. One page per run holding all such records of
the batch, like the ordinary page; nothing for a batch with no such record (receipt `result: "nothing"`, exit 0).

Files, inside `a<N>` (append-only, like reopen):

- `again.jsonl`: one line per run `{op: "again", again_id: "<audit_rev>/g<NNNN>", batch_id, audit_rev, records:
  [{rec_id, rev_base, fields: [field_id, ...]}], by: "human", at, page}`.
- `again/g<NNNN>.html`: the page, written once (temporary file, rename, read-only); the ordinary template and seal
  (`confirm_page.render(..., again=event)`); its data block carries `again: {again_id}`; `summary` says it is a second
  pass and how many fields remain.

**Idempotent**: a run whose `records` (rec_id, rev_base, fields) equal the newest `again.jsonl` event's is `unchanged`
and names the same page. After a writeback moved a record on, the next run is a new event `g<NNNN+1>`.

**Writeback** (`kb-audit --writeback choices.json`): `_check_page` accepts an again page exactly like a reopen page
(`confirm_sha256` of a sealed `a<N>/again/g*.html` whose `again_id` is logged). For such choices:

- `shown` for a record = the event's `fields` of that record (not `_to_confirm(cand)`);
- every item's `rev_base` must equal the record's current rev (the ordinary check, now satisfied);
- the new revision `r<cur+1>` carries the current candidate body's already decided `fields` and `dropped` and adds the
  new decisions (like reopen's `carry`, but from the **current candidate** body, not `effective`);
  status `verified` when no undecided field remains, else `disputed` keeping the remaining uncertain entries;
  decisions tagged `again_id`; body gets `again: <again_id>`;
- a choice for a field that is not in the event is rejected (`not on this page`); a record of the event with no item
  is `unchanged`; `none` without `not_a_field` leaves the field undecided (same as the first pass).

**Refused** (exit 30, nothing written): no `work/`, bad batch id, batch has no audit revision, a newer audit revision
than the records' candidates (writeback first). Lock, crash points and recovery as reopen (`again.logged`,
`again.mkdir`, `again.page_tmp`, `again.page_renamed`, `again.page_written`).

**Receipt** (stdout, one JSON line, `klode-kit.again_receipt/1`): `{schema, result: opened|unchanged|nothing,
exit_code (10 opened/unchanged, 0 nothing), batch_id, audit_rev, again_id, records: n, fields: n, page,
confirm_sha256}` or `{schema, result: "refused", exit_code, error}`.

## Where

- new `src/klode_kit/audit/again.py` (mirror `reopen.py`); `__main__.py` flag `--again` on `kb-audit`
  (mutually exclusive with `--reopen` / `--writeback`);
- `confirm_page.render(..., again=event)`: filter records to the event's, fields to the event's, `rev_base` from the
  event, label `候选 r<cur> (<status>) / 第二轮 <again_id>`;
- `writeback.py`: `AGAIN_LOG, AGAIN_DIR`, `_again_page`, `_check_page` returns `Reopen | Again | None`,
  `_check_record` / `_item_problem` / `_commit_human` take the `Again`; `_recover` unchanged.
- `src/klode_kit/prompts/*` and the docstring of `writeback.py` / `reopen.py` mention the new path.

## Tests first (unit, real tmp project through `_support.export_fx` style fixtures; no mock of the record store)

1. first writeback with half the fields decided -> record `disputed` r2; `--again` writes `again/g0001.html` whose
   data lists only the undecided fields with `rev_base 2`; receipt `opened`, exit 10.
2. a second `--again` with nothing changed -> `unchanged`, same page, no new log line.
3. export choices from the again page deciding the rest -> writeback commits r3 `verified` (human), `effective` moves,
   `fields` = first-pass values + second-pass values, decisions carry `again_id`.
4. deciding only some of the remaining fields -> r3 `disputed`, the rest still uncertain; `--again` then opens
   `g0002` listing only those.
5. choices from the stale ordinary page after the first writeback are still refused (regression guard); a choice on
   the again page for a field not in the event is rejected `not on this page`.
6. `--again` on a batch whose records are all verified -> `nothing`, exit 0, no files; on a verified record only
   `--reopen` applies (message names it).
7. page items: an unacknowledged page item appears on the again page and can be acknowledged there.
8. crash points: kill after `again.logged` -> next run completes the page (same `again_id`), no duplicate event.

## Not in scope

Changing the first pass, the reopen path, or the crops. No run against a real data project from the implementer.
