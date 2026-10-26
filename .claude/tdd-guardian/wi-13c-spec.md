# WI-13c: --type takes effect, per-route OCR timing, confirm-link crops (user 2026-10-26, no plan/matrix entry)

Basis: impl-deviations "WI-13b 局限 / 用户决定"; 04 U1 ("work/<batch>/ocr/receipt.json 四路各有耗时与 0 失败页") and U8.

## Findings that shape the design

- `audit/writeback._page_meta` already prefers `page["record_type"]` of the OCR batch receipt over the record's own type
  and over the page derivation (`record_meta`). `views` take the file type from the record's front matter, which the
  writeback writes. So the **writeback needs no change**: kb-ocr only has to put `record_type` on the receipt pages.
  (criticalPath coverage cannot drop: no line of `writeback.py` is touched.)
- The batch receipt is `work/<batch>/receipt.json` (`klode-kit.ocr_batch/1`, defined in the `run_batch` docstring, no
  schema file; no clock fields). The attempt receipt `ocr/<engine>/<fp>.t<n>/receipt.json` is `klode-kit.receipt/1`
  (schema file, `additionalProperties: false`, already has `started`/`ended` clocks).
- Timing must not break "equal runs give equal bytes" of the batch receipt. Decision: the measured time is stored in
  the **attempt receipt** (`elapsed_ms`, clock-bearing file, optional schema increment) and the batch receipt copies it
  into `route_stats.<engine>.elapsed_ms`. A reused attempt gives the same number; under the replay seam the clock is
  logical and every route measures 0, so replay trees stay byte-equal (crash/resume tests).

## Acceptance criteria

1. `kb-ocr --type lab|rx` (new option) writes `record_type` on every page of the batch receipt and at top level;
   unknown value -> exit 2; without `--type` and without an earlier type no `record_type` key anywhere. A rerun without
   `--type` keeps the type already in the receipt (sticky; the pipeline's second segment does not repeat `--type`);
   a rerun with another `--type` replaces it.
2. `pipeline --type` (new) passes it to the `ocr` stage and into the stage's `inputs_hash` **only when given**
   (old hashes unchanged). `med-intake --type X` passes X to both pipeline calls.
3. End to end: `med-intake --type rx` on a lab-titled batch, then `pipeline --stage writeback` (no `--type`), gives
   `records/<date>-rx.md` and no `-lab.md`. Without `--type`, behaviour is as before (`-lab.md`).
4. Batch receipt gets `route_stats: {<engine>: {elapsed_ms: int >= 0, failed_pages: int >= 0}}` for every requested
   route. U1 fixture: four routes, each `elapsed_ms` present and `failed_pages == 0`.
5. `run_batch.run(..., clock=)` injects the monotonic clock (default `time.monotonic`; logical zero clock under the
   replay seam). A route's `elapsed_ms` = round((end - start) * 1000) around its run; stored in the attempt receipt; a
   reused attempt reports the stored value; a route that did not run (disabled/unavailable) reports 0.
6. Schema `receipt.json`: optional `elapsed_ms` integer >= 0 (the only schema change).
7. Confirm links: besides `confirm/<alias>.html` (unchanged) `med-intake` keeps a REAL folder `confirm/<alias>/` holding
   the links `confirm.html` and `crops` into `../../work/<batch>/audit/a<N>/`. Opening `confirm/<alias>/confirm.html`
   resolves every `crops/...` image. (First design was a directory symlink `confirm/<alias>` -> audit dir; the recorded
   13b row `test_alias_symlink_stable` asserts the exact link set of `work/` and `confirm/`, so the symlink broke it.
   Changed to a folder of links before any 13b row was touched; the 13c-10/11 rows were adapted accordingly.)
   The sealed confirm page is not changed. The stdout line and the `.html` link are unchanged. The dir link follows the
   same rules as the html link (moves on a new revision, never overwrites foreign things, crash-idempotent), and a
   foreign `confirm/<name>` makes that alias name taken. stderr gains `confirm page (with crops): confirm/<alias>/confirm.html`.

## Schema increments (all optional)

- `src/klode_kit/schemas/receipt.json`: `elapsed_ms`.
- Batch receipt `klode-kit.ocr_batch/1` (docstring-defined): top-level `record_type`, page `record_type`, top-level
  `route_stats`.

## Matrix (law: 13c-03 / 13c-07 state invariants; others no law)

| Row | Lane | Spec | Case |
|---|---|---|---|
| 13c-01 | contract_replay | S1 | kb-ocr `--type rx` -> every page and top level carry `record_type: rx`; without `--type` no key |
| 13c-02 | unit | S3 | `kb-ocr --type scan` -> exit 2, nothing created; `pipeline --type scan` -> exit 2 |
| 13c-03 | contract_replay | S5 | sticky/replace: rerun without `--type` keeps rx; rerun with `--type lab` replaces; receipt bytes equal across the keep-rerun |
| 13c-04 | contract_replay | S1 | med-intake `--type rx` + pipeline writeback -> `records/<date>-rx.md`, no `-lab.md` (writeback/views follow the explicit type) |
| 13c-05 | contract_replay | S1 | med-intake without `--type` -> receipt has no `record_type`; views `-lab.md` as before |
| 13c-06 | unit | S2 | `_ocr_inputs`: without type identical to the old dict; with type differs; types differ from each other |
| 13c-07 | contract_replay | S1 | U1 fixture `route_stats`: four routes, `elapsed_ms` int >= 0, `failed_pages == 0` |
| 13c-08 | contract_replay | S5 | injected clock: each route 250 ms in attempt receipt and `route_stats`; rerun (reuse) with another clock keeps the same bytes; failed page counted in `failed_pages` |
| 13c-09 | unit | S2/S3 | schema `receipt`: `elapsed_ms` 0 and 5 valid; -1, 1.5, "3" invalid |
| 13c-10 | unit | S1/S3 | `link_confirm`: dir link created, moves with revision, foreign `confirm/<alias>` dir untouched -> `AliasConflict`; `allocate_alias` skips a name whose `confirm/<name>` exists |
| 13c-11 | contract_replay | S1 | med-intake: `confirm/20261022/` is a link to `../work/<batch>/audit/a1`; every `crops/` reference of `confirm/20261022/confirm.html` exists through the link; second run / crash keep it |

Decision on item 3: implemented as an additional directory link (no recorded 13b row is changed; the `.html` link,
stdout, and receipt stay as recorded). A browser opening the `.html` link still shows broken thumbnails; the openable
path is the one on stderr and in `docs/med-intake.md`. WI-31/U8 manual check: open `confirm/<alias>/confirm.html`.
