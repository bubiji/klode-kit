# src/klode_kit/templates

Templates installed with the `klode_kit` package (package data: the wheel ships every file under
`src/klode_kit/`, so these are found through `importlib.resources.files("klode_kit") / "templates"`, never
through a path relative to the repository).

| File | Used by | Purpose |
|---|---|---|
| `confirm.html` | `klode_kit.audit.confirm_page.render` | Skeleton of the local confirm page `work/<batch>/audit/a<N>/confirm.html`. Placeholders `{{name}}` are filled in one pass (filled text is never scanned again); `{{stamp}}` becomes the page's sealed sha256. |
| `confirm_export.js` | embedded verbatim in `confirm.html`; also `node confirm_export.js <confirm.html> [<selections.json>]` | Turns the clicks on the page into `choices.json` (schema `klode-kit.choices/1`). The same exporter exists in Python (`confirm_page.export_choices`); the cli_integration lane checks both agree. |

Rules for editing them:

- the page must reference nothing outside itself (no `http(s)://`, no external script, style, font or image;
  crops are relative `crops/<name>.png`);
- `confirm_export.js` must not contain `</script` or `<!--` (it is embedded in a `<script>` element; rendering
  refuses it otherwise);
- the stamp `<meta name="klode-kit-confirm-sha256" content="...">` must stay the first such meta of the page.
