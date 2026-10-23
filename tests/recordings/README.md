# tests/recordings — 真实引擎的原始输出录制（WI-00c3）

**不是生成器产物**：`tests/fixtures/` 下的文件全部由 `gen_*.py` 确定性生成（`gen_all --check` 逐字节核对），
本目录则是对那些合成图**实跑**四路 OCR 得到的原始输出，放在生成树之外，生成器既不检查也不会移走它们。

- `<engine>/<der_sha256>.json`，`engine` ∈ `vision`、`mlx`、`tesseract`、`claude`、`codex`；`der_sha256` 是发送 / 识别的那张
  图（`tests/fixtures/MANIFEST.json` 中登记的合成图）的 sha256。`codex/<sha>.effort-max.json` 是 ⑤′ 的 `max` 档复核录制。
- 格式 `schema: klode-kit.replay_recording/1`，`recorded: true`；字段说明见 `docs/spikes/WI-00c-4routes.md` §7。
- 云端两路的 `stdout` / `stderr` / `exit_code` 原样保存；假 CLI（`tests/fakes/bin/_fake_cli.py`）在没有回放场景时按
  `argv_sha`（含程序名的完整 argv）从这里取录制回放。
- 内容只来自合成图（白名单占位名 / 编号）。`privacy_scan` 0 条：原始输出里的 JSON 转义、OCR 误读与浮点尾数造成的误报已逐条核查，
  登记在仓库根 `.privacy-allowlist`。
- `MANIFEST.json`：本目录每个文件的 `path / sha256 / bytes`；
  `python -m klode_kit.devtools.fixture_manifest verify --root tests/recordings` 双向校验。
- 由 `tests/model/test_hardest_pages_4routes.py`（model_integration，闸门 G2）写入；已有同参数录制时复用，不重复调用。
