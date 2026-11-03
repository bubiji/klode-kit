# WI-39 — reviewer attribution: tell an AI reviewer's decisions from the owner's (D-43)

## Why (2026-11-03)

D-43 lets a reviewer agent (sonnet/opus) make the confirm-page choices for the owner. Every such choice is written
back today as `by: "human"` and the record as `verified_by: "human"`, so the library cannot tell "the owner looked"
from "an AI reviewer looked". The owner asked that AI-reviewed records be marked as such. All non-auto commits made
so far in the real data project were made by reviewers, never by the owner, so the earlier commits need the mark too.

## What

### 1. `choices.json`: optional top-level `reviewer`

`"reviewer": {"model": "<model id, 1..64 chars>", "rule": "<short rule name, 1..64 chars>"}` (schema
`schemas/choices.json`, `additionalProperties: false` inside). Absent = the owner, as today.

When present, `kb-audit --writeback` (first pass, reopen page, again page alike):

- every decision written from this file has `by: "reviewer"` and `evidence.reviewer = {model, rule}`;
- the new revision's `verified_by` (body, candidate entry, `effective`) is `"reviewer"` when the revision is verified
  and **any** human-made decision it rests on (new in this commit, or carried from earlier commits on this audit
  revision / from `effective` on reopen) is a reviewer's; `"human"` when all of them are the owner's; `"auto"` stays
  as it is for revisions no one decided;
- everywhere the code now treats `by == "human"` as "a person decided this" (`writeback._earlier`, the decided set at
  ~l.302 and ~l.792, `stage.py` ~l.224, `provenance.py`), `"reviewer"` counts the same way. A reviewer decision is a
  decided field exactly like an owner decision; nothing else about the gate changes.
- `schemas/record.json`: `verified_by` enum adds `"reviewer"`; `schemas/fixes.json` `by` likewise if it lists the
  values.

### 2. Backfill: `kb-audit --attribute --batch B --reviewer-model M --reviewer-rule R [--project DIR]`

For a batch whose earlier commits were made by a reviewer but written as `human`. Append-only; never rewrites
`fixes.jsonl`, `commits.jsonl`, record bodies or `_rev/` files.

- Appends one line to `work/<B>/audit/attributions.jsonl`:
  `{op: "attribute", batch_id, commit_ids: [every ended commit of every a<N> of the batch with at least one
  by=="human" decision, not already attributed], by: "reviewer", reviewer: {model, rule}, at}`.
  Nothing to attribute -> receipt `nothing`, no line. Receipt (one JSON line, schema
  `klode-kit.attribute_receipt/1`): `{schema, result: attributed|nothing|refused, exit_code (0 / 30), batch_id,
  commits: n, records: n}`.
- One reader, `audit.attribution.decided_by(project, batch_id, decision) -> "auto"|"human"|"reviewer"`, returns
  `"reviewer"` for a decision whose `commit_id` is attributed, else the decision's own `by`. A matching
  `record_verified_by(project, rec_id) -> "auto"|"human"|"reviewer"|None`: for the record's **effective** revision
  (verified only, else None) the WI-39 rule of section 1 applied over the decisions that revision rests on, read
  through `decided_by`.
- `kb-audit --writeback` after an attribution uses `decided_by` too, so a new owner commit on top of an attributed
  reviewer commit still yields `verified_by: "reviewer"`.

### 3. Shown in the library

`build/records_to_txt.py`: every exported record gets one more line, after its other lines and anchored like them:
`verified_by: auto` | `verified_by: human` | `verified_by: reviewer (<model>)`, value from
`record_verified_by` (model from the newest reviewer decision of that revision). Deterministic; `export_map`
entries for the line have no field_id. Check that the strict anchor checks of `kb-check` still pass on the export
(run the records build tests).

## Tests first (unit, real tmp project fixtures like `test_again_wi38.py`; no mock of the record store)

1. writeback of a choices file with `reviewer` -> decisions `by: reviewer` with `evidence.reviewer`; record verified
   `verified_by: reviewer` (body, candidate, effective). Without `reviewer` -> `human` exactly as before
   (regression).
2. schema: `reviewer` with a missing or extra key, or an empty / 65-char model, is refused before any write.
3. mixed: first pass by the owner leaves the record disputed, `--again` pass by a reviewer finishes it ->
   `verified_by: reviewer`; the reverse order -> `reviewer` too; owner both times -> `human`.
4. a reviewer's decision counts as decided for `--again` (not listed again) and for the page-item rule.
5. `--attribute`: lists exactly the ended commits with human decisions; second run -> `nothing`; files other than
   `attributions.jsonl` byte-identical before/after; `record_verified_by` then says `reviewer` for a record verified
   by those commits, `auto` for an auto-verified one, None for a disputed one.
6. `records_to_txt`: the `verified_by:` line for auto, human, reviewer (with model); attributed record shows
   `reviewer`.
7. refused: bad batch id, no `work/`, `--attribute` without model/rule -> exit 30, nothing written.

## Not in scope

The confirm page template, the gate, crops, reopen semantics. No run against a real data project by the
implementer. Do not touch `tests/unit/test_wua.py` (6 pre-existing failures: renderer not installed).
