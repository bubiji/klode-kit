# tests/acceptance/ - U1..U8 user-perceived acceptance suite (WI-31)

One file per user case of `docs/pipeline/20261022-klode-kit/04-需求摘要与usercase.md` section 6. Every assertion is on
something the user can observe: stdout and the exit code of the user entry (`med-intake`, `kb-ocr`, `pipeline`,
`kb-audit`, `build`, `kb-publish`, `klode verify`), files that come out (receipts, views, `.mcp.json`), and the data
block of the confirm page.

| file | lane | gate |
|---|---|---|
| `test_u1_med_intake.py` | contract_replay | |
| `test_u1_med_intake_real.py` | model_integration | G2 |
| `test_u2_kb_ocr_rotated.py` | contract_replay (+ cli_integration macos) | |
| `test_u3_pipeline_first_stage.py` | contract_replay | |
| `test_u3_workflows_host.py` | host_acceptance | G2 G5 |
| `test_u4_guide_pdf.py` | cli_integration | |
| `test_u5_verify_cli.py` | cli_integration | |
| `test_u5_expert_qa.py` | model_integration | G2 G5 |
| `test_u6_redo_acceptance.py` | contract_replay | |
| `test_u7_book_build.py` | cli_integration | |
| `test_u8_confirm_writeback.py` | contract_replay (+ cli_integration needs_node) | |
| `test_provenance_acceptance.py` | contract_replay + cli_integration | |

Real (G2) files need `KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd>` and book every call in
`work/host-spike/costs.jsonl` (`_support.host.Ledger`); synthetic images registered in `tests/fixtures/MANIFEST.json` only.
