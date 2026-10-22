# klode-kit

A Claude Code plugin that turns scanned documents into a citable klode knowledge base:
multi-engine OCR → cross-engine field audit with a local confirmation page → klode shelf/desk build → strict citation gate.

Status: planning. Implementation follows the build-pipeline (design → Codex review → TDD).

## Layout

| Path | Purpose |
|---|---|
| `docs/input/` | Pipeline inputs: overview plan, design notes, workflow diagrams (de-identified) |
| `src/` | Implementation code (empty until TDD stage) |
| `tests/` | Tests and synthetic fixtures — no real personal or medical data |

## Privacy rule

This repository is public. Fixtures are synthetic; real documents stay in the private data project that consumes this plugin.
