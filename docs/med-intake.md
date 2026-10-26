# `/med-intake`: register an inbox, OCR it, stop at the confirm page

Implementation: `src/klode_kit/med_intake.py` (command `klode-kit med-intake`). WI-13b. The skill text that calls it is
WI-13a (`skills/med-intake/SKILL.md`); the skill holds no business logic.

```
klode-kit med-intake --inbox _inbox/2026-10-22/ [--project DIR] [--type lab|rx] [--routes a,b] [--launcher L]
```

The data project (`--project`, default: the current directory) holds the inbox, `work/`, `records/` and `kb/`. The plugin
holds no data.

## What one run does

1. **Register** the inbox: the document type (`lab` | `rx`), the date and the source.
   - *Type*: `--type` wins. Otherwise the title keyword read by the OCR routes decides, deterministically and with the
     same rule the record views use (`audit/record_meta.py`: `处方` -> `rx`; `检验` / `化验` -> `lab`; the most common
     type across routes wins). A page without a keyword, or pages that disagree (some `lab`, some `rx`), stop the run
     with exit 2 and the message to give `--type`; nothing is aliased and the audit does not start.
   - *Date*: the nearest directory of the inbox path named like a date (`2026-10-22` or `20261022`, a real calendar
     day). Without one: the date the routes read on the pages (`日期` line, most common), else today.
   - *Source*: the inbox directory's name and the file names. No absolute path is written into the receipt.
2. **Run** the pipeline kernel (`klode_kit.pipeline`) up to the audit: `preflight` (doctor), `ocr`, `audit`. The kernel is
   reused as it is: stage receipts, the three-hash reuse rule, the confirm page written after `kb-audit`. Nothing is
   written back and nothing is built.
3. **Stop** at `awaiting_confirm` (exit 10) when there is something for the human to click, or finish with exit 0
   (`nothing_to_confirm`) when every field was agreed by all routes.

Terminal output (stdout; the progress of the children goes to stderr):

```
ocr: 3 页 × 4 路 OK
audit: 5 条分歧 → confirm/20261022.html
status: awaiting_confirm
```

The numbers are measured: pages and routes from the OCR batch receipt, disputes = the fields on the confirm page. A route
that failed on a page is named instead of `OK`: `ocr: 3 页 × 4 路，1 处失败（codex:p002）`. Nothing to confirm prints
`audit: 0 条分歧，无需确认`.

## Date aliases (risk R-3)

The batch id is a hash; the paths people expect are dates. `med-intake` keeps two links in the data project:

| Link | Points to |
|---|---|
| `work/<yyyymmdd>` | `<batch_id>` (relative: the batch directory beside it) |
| `confirm/<yyyymmdd>.html` | `../work/<batch_id>/audit/a<N>/confirm.html` |

- An alias is **stable**: a rerun, a crash and a rerun, or the next batch of the same day never changes where an existing
  alias points. The confirm link is the only one that moves, and only when a new audit revision `a<N+1>` of the same
  batch exists (the page of the newest revision is the one to click).
- **Same date, different images** (a second batch on the day): the first free name is used, `<yyyymmdd>-2`, then `-3` ...
  An existing alias, a real directory or a stray link with the wanted name is never overwritten. Rerunning a batch keeps
  the name it already has.
- The confirm page is a sealed file whose small images are referenced relative to its own directory. Open it by its real
  path (printed on stderr as `confirm page: ...`) if the browser does not show the crops through the link: the `file:`
  URL of a link resolves `crops/...` beside the link, not beside the target. The link is for finding the page, not
  for rendering it.

## Receipt

`work/<batch_id>/med_intake.json` (`klode-kit.med_intake/1`, defined in code; no wall-clock field, so equal runs give
equal bytes): `batch_id`, `alias`, `confirm`, `record_type` + `type_source` (`header` | `explicit`), `date` +
`date_source` (`inbox_dir` | `page` | `today`), `source` (`inbox`, `files`), `pages`, `routes`, `ocr_failures`, `disputes`,
`status`, `exit_code`, `stages` (name, status, exit code of each pipeline stage). Times are in the stage receipts
`work/<batch_id>/pipeline/receipts/<stage>.json`.

## Exit codes

0 nothing to confirm; 10 awaiting the human's confirm page; 2 usage (no images, unknown or missing type, no such
directory); 30 contract problem; 40 environment, configuration or another `med-intake` / `pipeline` holding the project
lock (`work/.med-intake.lock`); 50, 60, 61 as the pipeline kernel (budget, privacy, isolation or the real-call gate).
Cloud routes only ever leave the machine through the existing sender (masked, registered images, gate G2); this command
adds no second path.

## After the click

The human exports `choices.json` from the confirm page. The second segment is the pipeline kernel's:
`klode-kit pipeline --batch <inbox> --choices choices.json` (writeback, views, then build, check, publish). `med-intake`
does not repeat it.

## Backup plan (documentation only; the plugin implements no backup action)

What is worth keeping is the data project, not the plugin. The agreed plan:

1. **Local git in the data project.** Commit after every confirmed writeback: `records/`, `kb/` (the published builds'
   pointers and `PUBLISH.log`) and the project's configuration files. `work/`, `_raw/`, `_masked/`, `_mask_review/` and
   `_private/` stay out (they are in `.gitignore`; `klode-kit doctor` checks that).
2. **A bare repository on an external drive**: `git init --bare <drive>/<project>.git` once, `git remote add backup
   <drive>/<project>.git`, `git push backup --all` after each commit. Never a remote that is a cloud service: the project
   holds medical records.
3. **A byte-checked copy of what git does not hold** (`_inbox/` originals and `work/`): `rsync -a --checksum _inbox/ work/
   <drive>/<project>-files/` and compare with `rsync -nc` (dry run, checksum) after the copy; a difference list that is not
   empty means the copy is not a backup.
4. Restoring is the reverse; a restored `work/` keeps the batch ids and therefore the date aliases (the links are
   relative).

Nothing above is run by the plugin. Commands are the user's, run in the data project.
