# WI-41 — field extraction fixes from the third confirm pass (P37) + `kb-audit --reextract`

## Why (real findings, 2026-11-03)

Eleven AI reviewers on ~400 undecided fields of real lab reports, prescriptions and bills reported the same splitting
faults again and again. They make two routes disagree on text both read correctly (so the record never auto-verifies)
or create slots that are not data. All fixes are in `audit/fields.py` (WI-07a); every one keeps `line[start:end] ==
value` (nothing corrected in place; only `norm` changes).

## What

### A. `fields.extract` rules (each with the real symptom)

1. **Ranges with a doubled or spaced separator are one number.** `72--182`, `3.5 - 5.3`, `(9.40--15.50)`, `4.5～ 8`
   -> one `number`, `norm` `72-182` etc. Today `72--182` gives `72` and `182` on one route while the other route keeps
   `72-182`: slot k means different text on the two routes. Rule: `NUM \s* SEP{1,2} \s* NUM` (SEP = `-~–—～－`) when the
   left number is not itself a date part. Parentheses around a range are not part of the value.
2. **Thousands separators.** `3,200`, `¥31,168`, `10,065` -> one `number`, `norm` without commas (`3200`). Pattern:
   1–3 digits then one or more `,ddd` groups (also full-width `，`), optional decimals; `¥`/`￥` before it is not part
   of the value. A comma followed by other than exactly three digits is not a separator (`1,2` stays two numbers).
3. **Spaced decimal point.** `10. 1`, `90. 0` (vision reading a spaced/full-width point) -> one `number`, `norm`
   `10.1`, only when the point is followed by **one** space and 1–2 digits and then a non-digit or end, and the left
   part has 1–3 digits. (`1. 2` at a line start followed by a letter list stays as is — a list marker is followed by a
   non-digit word; keep it simple: a line-initial `\d+\.\s` followed by a non-digit is never joined.)
4. **Letter flags never inside a unit.** `L`/`H` touching any letter (Unicode `isalpha`, so `μL`, `µL`, `㎕`-folded),
   digit, `/` or `^` is not a flag. Today the `L` of `/μL` and `x10^9/L` becomes a low flag because `μ` is not in
   `[A-Za-z]`.
5. **Powers of ten with a letter X or a μL denominator.** `X10^4/μL`, `x10^9/L`, `*10^12/L`, `×10^3/μL` -> one `unit`
   (`norm` `10^4/μL`, `10^9/L`); today the `4` of `X10^4/μL` becomes a number and the `L` a flag. Keep the deliberate
   rule that `10~9/L` (a vision misread) is not a unit.
6. **`*` and `※` inside a word are not flags.** A symbol flag counts only when the character before it is whitespace,
   line start, a digit or `)` and the character after it is whitespace, line end or `)`. So `*[基]`, `22.5mg*4`,
   `※注` are not flags; `5.2 *` and `5.2*` still are.
7. **A digit glued to a CJK item name is not a number.** `物価対応料1`, `評価料(I)2`, `(3+N联动)` — a number whose
   previous character is CJK (or `(I)`-style roman numeral in parentheses directly after CJK) and whose next character is
   whitespace/end/`)` is part of the label, not a field. (Today `_NUMBER` only refuses ASCII letters before a number.)
   Real values printed right after a CJK label with no space are rare in these sources; when the next character is a
   unit (`血红蛋白128g/L`) the number still counts.

Add the cases above (and their negatives) to the existing `tests/unit/test_fields*.py` style; every existing
`fields` test must still pass unchanged — if one contradicts a rule here, stop and report it instead of changing it.

### B. `FIELDS_RULES = 2` and `kb-audit --batch B --reextract`

- `fields.FIELDS_RULES` (int; this WI makes it 2; absent history = 1).
- A new audit revision records it: `a<N>/rules.json` `{"fields_rules": 2}` (write-once like the other files); an older
  revision without the file is rules 1.
- Default `kb-audit --batch B` behaviour is **unchanged** (reuse `a<N>` when the run set is equal — rules are not
  compared), so fully verified batches never churn.
- `--reextract`: reuse is refused when `a<N>`'s rules differ from `FIELDS_RULES`; a new `a<N+1>` is written from the
  **same run set** (no OCR call), with fresh cells / candidates / crops / confirm page. Same rules -> exactly as
  without the flag (reuse, `unchanged`). Receipt / stdout line says `new (reextract: rules 1 -> 2)`.
- Records: as for any new audit revision today (candidates only; `effective` untouched until writeback). Check and
  test what happens to a record already **verified** at `a<N>` whose `a<N+1>` candidate is auto-verified, pending, or
  disputed: the existing writeback/gate rules apply unchanged; document the observed behaviour in the module
  docstring. `--again` / `--attribute` / `--redate` keep working on `a<N+1>`.
- `--reextract` only with `--batch`; not with `--writeback/--reopen/--again/--attribute/--redate` (usage exit 2).

## Tests first (unit; invented values only; real tmp project fixtures for B like test_again_wi38.py)

1. One test per rule A1–A7 with the real-shaped strings above, plus the negatives named in each rule.
2. Cross-route: two route rows of the same printed line, one with `72--182` and one with `72-182`, align to the same
   cell and agree (align + vote, WI-07b/c).
3. B: `--reextract` on a batch audited under rules 1 opens `a2` with `rules.json`, same `runs.json` content, no OCR
   call; a second `--reextract` reuses `a2`; plain `kb-audit --batch` on a rules-1 batch still reuses `a1`.
4. B: a record verified at `a1` keeps `effective` after `--reextract`; a writeback of `a2` choices works; `--again`
   works on `a2`.
5. Usage errors.

## Not in scope

The gate, the confirm page template, crops, reviewers' rules, running anything on a real data project.
Do not touch `tests/unit/test_wua.py` / `tests/docs/test_wua.py` (6 pre-existing renderer failures).
