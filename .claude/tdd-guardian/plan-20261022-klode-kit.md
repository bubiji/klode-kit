# TDD Plan: klode-kit（扫描件/截图 → 可逐字引用的 klode 库；Claude Code 插件）

> 依据：`docs/pipeline/20261022-klode-kit/` 下的 02（正文 + r2/r3/r4，后段覆盖前段；WI 以 r2.10 为底，r3.6、r4.6 的替换行为准）、04（U1–U8、F1–F14，已确认，不改）、06 第 4 轮处置表（含「实施时注意」）、两份 03 基线（B1–B6；189 OCR 可复用模块）。没有读私有项目 002。
> 本文只做计划，没有改任何文件。

## 概览

- **WI 总数：53**，超过 30，所以分成四个里程碑：
  - **M0 spike + 契约**：11 个（WI-00s、00a、00b1–b3、00c1–c3、00d1–d2、00e）
  - **M1 OCR + 审计**：15 个（WI-04a–c、05a–b、06a–b、07a–d、08a–d）
  - **M2 build / check / 发布 / MCP**：17 个（WI-01a–c、02a–d、03a–b、10b、09a–d、10a、11a–b）
  - **M3 med-intake、workflow、插件打包、文档**：10 个（WI-12、13a–b、14、15a–b、17、19、30、31）
- **编号规则**：沿用 r2.10 的编号，拆开的工作项加字母后缀。r2.10 的 WI-16（回修用户 skill）按你的指示和 02§3.3 的原编号记为 **WI-19**。新增两个：WI-30（文档）、WI-31（U1–U8 验收套件）。原 §10 的 WI-21（CER 基线）已经并入 WI-00c3（见 r2.11 ⑦）。
- **硬前提**：只有 WI-00a–00e 全部过了 exit criteria，才开始 M1。r2.10 规定的「详细计划在 00a–e 通过后开始」，在这里体现为 M1 的入场条件。

### Lane 清单

| lane | pytest 标记 | 内容 | 真实模型调用 | 缺依赖时 | CI |
|---|---|---|---|---|---|
| unit | `unit` | 纯函数、schema、HTML 结构、hook 脚本 | 0 | — | ✅ |
| contract-replay | `contract_replay` | 契约样例、来源链 roundtrip，以及用回放文件跑的两段式全链（四路 OCR、writer、klode 输出全部回放） | 0 | — | ✅ |
| cli-integration | `cli_integration`（子标记 `macos`、`needs_node`） | 真 `klode`/`klode-mcp` 0.5.0、`pdftotext`、ocrmac/tesseract/MLX 本机引擎、wfviz、node | 0 | **FAIL**，不是 skip | 条件跑（见下） |
| model-integration | `model_integration` + `gate_cloud` | 合成图走四路真实调用、隔离探针、真实写卡、expert 中文查询、宿主 MCP 探针 | 有 | FAIL | ❌ 只在本地跑 |
| host-acceptance | `host_acceptance` + `gate_cloud` | `claude -p --plugin-dir .` 跑 `/klode-kit:*`、Workflow 两段式 | 有 | FAIL | ❌ 只在本地跑 |

---

## 0. 全局约定

### 0.1 测试目录与 lane 命令

| 目录 | 放什么 |
|---|---|
| `tests/unit/` | unit lane |
| `tests/contract/`、`tests/replay/` | contract-replay lane |
| `tests/cli/` | cli-integration lane |
| `tests/model/` | model-integration lane |
| `tests/host/` | host-acceptance lane |
| `tests/acceptance/` | U1–U8，每个文件自带 lane 标记 |
| `tests/docs/` | WUA 校验（标为 cli_integration） |
| `tests/fakes/bin/{claude,codex,klode}` | 回放用的假可执行文件 |
| `tests/fixtures/` | 合成样本 |

lane 命令（统一入口是 `bin/klode-kit dev lane <lane>`。它包一层 pytest，写 `work/lanes/<lane>.json` receipt，并把 0 个用例当作 FAIL）：

```
uv run pytest -m unit             --cov=klode_kit --cov-branch -p pytest_socket --disable-socket
uv run pytest -m contract_replay  --cov=klode_kit --cov-branch --cov-append -p pytest_socket --disable-socket
uv run pytest -m cli_integration  --cov=klode_kit --cov-branch --cov-append
KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> uv run pytest -m model_integration
KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> uv run pytest -m host_acceptance
```

- `conftest.py` 在 unit 和 contract_replay 两条 lane 里强制 `KLODE_KIT_REAL=0`，并禁用 socket。它还把 `tests/fakes/bin` 放到 PATH 最前面：没有配置回放场景就调用 `claude`/`codex`，测试会直接 error。
- 带 `gate_cloud` 标记的用例，缺 `KLODE_KIT_GATE_ACK` 时整个 session 报用法错误（exit 2），不是 skip。
- pytest 退出码 5（没收集到用例）记为 FAIL。唯一例外是 bootstrap：某条 lane 历史上从来没有过用例时，只大声警告不阻断；这条 lane 一旦出现过第一个用例，例外就永久失效。

### 0.2 CI（只跑零真实模型调用的 lane）

- `.github/workflows/ci.yml`，ubuntu job：先跑 `privacy_scan`，再跑 unit 和 contract_replay，最后算覆盖率门槛（只对这两条 lane 合并后的结果判定），receipt 标 `not-release`。
- macOS job（条件跑）：只有当 `klode==0.5.0` 能从公开源安装时，才跑 cli_integration（不含 `macos_mlx` 子集）。装不上就**不建这个 job**（不能用 skip 冒充 PASS）；cli receipt 改为本地产出，由 `release_check` 校验。
- model-integration 和 host-acceptance 永远不进 CI。

### 0.3 覆盖率与 mutation

- 全局（unit + contract_replay + cli_integration 合并）：**branch ≥ 85%**、line ≥ 90%。报告为 0 行时判 FAIL。
- criticalPaths（branch ≥ 95%，并要求 spec level）：

| 路径 | 要求 |
|---|---|
| `src/klode_kit/audit/writeback.py`、`contract/ids.py`、`contract/provenance.py`、`publish.py` | S5 |
| `audit/compare.py`、`audit/align.py` | S6 |
| `ocr/send.py`、`ocr/isolation_check.py`、`ocr/isolation_guard.py` | S4 |
| `build/records_to_txt.py`、`build/deident.py`、`mcp_wrap.py`、`build/fix_anchors.py`、`build/check_cards.py` | S4 |

  `devtools/coverage_gate.py` 按 glob 逐条检查；某个 glob 一个文件都匹配不到就 FAIL。
- **mutmut：要求跑**，范围限于上面的 criticalPaths。发布前 mutation score ≥ 80%；等价变异要在 `docs/mutation/<release>.md`（新文件）里逐条注明。mutmut 不阻断 PR，在本地或 nightly 的 macOS 上跑，结果计入 `release_check`。
- property 测试库用 **hypothesis**（dev 依赖）。

### 0.4 mock 边界与配对

| 被 mock 的边界 | 替身 | 配对的真实 lane 测试 |
|---|---|---|
| `claude`/`codex` 子进程 | `tests/fakes/bin/*` 读 `tests/fixtures/replay/` | WI-00c2、00c3、00d2、06a/06b（model）、11b/12（model） |
| Vision、MLX、tesseract | 原始引擎输出回放 | WI-04a（cli，`macos`） |
| `klode` CLI（只在 replay 全链里） | 录制的 stdout/JSON | WI-02a–d、09b、10a（cli） |
| 时钟 | 注入 `now()` | 不需要配对 |

自己写的模块一律不 mock。

---

## 1. 手动闸门（实现者走到这里必须停下）

| 闸门 | 触发动作 | 所在 WI | 停下后要做什么 |
|---|---|---|---|
| **G1 写 `~/.claude`** | 回修用户 skill 的 B1–B5；任何 `claude plugin install`（user 作用域）；写 `~/.claude/settings*.json` | WI-19 全部；所有宿主测试一律改用 `--plugin-dir .`，不安装插件 | 先列出 diff 和影响文件交给用户。按全局规则，先备份或移入 `_trash/` 再改，用户确认后才执行 |
| **G2 真实云端外发 / 真实模型调用** | 第一次带 `KLODE_KIT_REAL=1` 运行；录制回放；四路最难页实跑；隔离探针；真实写卡；宿主 MCP 探针；Workflow 实跑；U5 问答 | WI-00c2、00c3（实跑那一步）、00d2、06a/06b（录制）、11b（宿主探针）、12、13a（宿主）、14（宿主）、31（model/host 用例） | 前面的确定性 TDD 部分照常做完；第一次真实调用前停下，等用户给出 `KLODE_KIT_GATE_ACK`。**只允许 `tests/fixtures/MANIFEST.json` 里登记过 sha 的合成图**。本仓库永远不开放真实数据路径，真实数据只在阶段 6 的私有项目里跑 |
| **G3 登记到用户或真实数据项目的 MCP** | 写真实数据项目的 `.mcp.json` 或 `.claude/settings.local.json`；写真实的 `~/.klode/registry.toml`；写 `~/.claude.json` | WI-11b（自动测试只碰 tmp 合成数据项目，`KLODE_REGISTRY` 指向 tmp）、阶段 6 | 自动化测试**一律不碰**上述真实路径。对真实项目执行 `publish` 前停下，由用户来执行 |
| G4（来源文档要求，补充）安装依赖 / 下载权重 | `pip/uv` 安装 ocrmac、mlx-vlm；下载约 3.9 GB 的 MLX 权重；`brew install` | WI-04a、15a | OCR§8 要求「装之前先问」：先问用户 |
| G5（补充）宿主信任 | 交互接受目录信任（R2-07 运行前提） | WI-00d2、11b、13a、14 | 用固定实验目录 `tests/host/_lab/`（gitignore），请用户交互运行一次 `claude` 接受信任 |

---

## 2. Work Items

> 每个 WI 都包含：目标、涉及文件、依赖、lane、验收标准、必需测试（标注断言级别 L1–L7 和规格级别 S1–S6）、风险。

### M0 · spike + 契约

#### WI-00s：仓库与测试脚手架
- **目标**：建立包结构、lane 标记与 runner、真实调用防护、隐私扫描和 CI 骨架。
- **文件**：
  - `pyproject.toml`（core 依赖，以及 dev 依赖：pytest、hypothesis、pytest-cov、pytest-socket、mutmut、jsonschema、reportlab）
  - `src/klode_kit/{__init__,__main__}.py`（子命令注册表）
  - `bin/klode-kit`（启动器：`exec "$(dirname "$0")/../.venv/bin/python" -m klode_kit "$@"`，可用 `KLODE_KIT_PYTHON` 覆盖）
  - `src/klode_kit/devtools/{run_lane,coverage_gate,privacy_scan}.py`
  - `tests/conftest.py`、`tests/fakes/bin/{claude,codex}`
  - `.github/workflows/ci.yml`
  - `.gitignore`（追加 `work/ .venv/ tests/host/_lab/`）
- **依赖**：无。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] `run_lane unit` 在 0 个用例时退出非 0，并打印 `0 tests discovered`。只有 lane 历史为空时才走 bootstrap 警告。
  - [ ] unit/contract_replay lane 里任何 socket 连接都抛异常；没配置场景就调 `claude` 会让测试 error。
  - [ ] `privacy_scan` 能命中 18 位身份证、手机号、`姓名：<非白名单>`；仓库里不在 fixtures MANIFEST、也不在 `docs/wua/*.svg` 的图片会被标出。退出码非 0。
  - [ ] `bin/klode-kit --version` 输出包版本。
- **必需测试**：
  - `test_run_lane_zero_tests_fails` — L1·S3
  - `test_socket_blocked` — L1·S3
  - `test_fake_claude_unconfigured_errors` — L3·S3
  - `test_privacy_scan_boundaries`（17/18/19 位、白名单假名）— L1·S2
  - `test_privacy_scan_unlisted_image` — L2·S3
  - 无 law：这些是守卫与配置，没有守恒量或往返关系可测。
- **风险**：02 原写 `scripts/privacy_scan.py`，按你的全局规则实现代码放 `src/`，`scripts/` 只保留 `install.sh`。

#### WI-00a：合成 fixtures 生成器
- **目标**：提供全部 lane 共用的合成样本、结构化真值、模拟路输出（可注入错误），以及 MANIFEST。
- **文件**：
  - `tests/fixtures/gen_{images,truth,engine_outputs,pdf,book,all}.py`
  - `tests/fixtures/MANIFEST.json`
  - `tests/fixtures/README.md`（声明全部为合成数据）
  - 场景目录：
    - `fx-labs-1x`、`fx-labs-3x`（同一项目 3 次观测，其中 2 次数值相同）
    - `fx-dup10`、`fx-colshift`、`fx-swap`、`fx-missingrow`
    - `fx-manualfix`（四路都读成 10.5，人工改为 1.05）
    - `fx-redo`（重做时 codex 缺 p001）
    - `fx-rx-stamp`、`fx-lowres`、`fx-vertical`、`fx-rotated90`
    - `fx-u1-3labs`（恰好 5 条分歧）、`fx-u3-mixed`（一页无分歧、一页只有多数票分歧）、`fx-u8`（含 `p002-l07` 平票）
    - `fx-text-pdf`、`fx-mixed-pdf`（text/empty/garbled/扫描页混排）
    - `fx-book-aurora`（3 章，`desk.json` + 5 题 holdout）
    - `synth_project/`（假 CLAUDE.md、假 `.mcp.json`）
    - `fx-chain`（完整来源链产物 + 6 个损坏变体）
- **依赖**：00s。**lane**：unit。
- **验收标准**：
  - [ ] `gen_all --check` 生成到 tmp 后，与 MANIFEST 逐字节一致（用固定字体和种子，只在 `macos` 上做再生成校验）。
  - [ ] fixtures 下每个文件都在 MANIFEST 里，sha 正确；`privacy_scan` 0 命中；人名和编号只出自白名单（测试患者甲、T-0000001…、示例片）。
  - [ ] 错误注入器：
    - swap：值的多重集合不变，位置恰好错 2 格；
    - colshift：数值落进单位列；
    - missingrow：某一路少一行；
    - degenerate：`repeat_ratio > 0.3`。
  - [ ] 真值里设计了 ≥ 20 个四路一致字段（实际数量以 00c3 的运行结果为准）。
- **必需测试**：
  - `test_generation_deterministic`（生成两次，sha 相同）— L2·S5
  - `test_swap_injector_property`（hypothesis：任意表格任意相邻两行）— L1·S4
  - `test_manifest_complete` — L2·S2
  - `test_names_allowlist` — L1·S3
- **风险**：CJK 字体的授权和可得性（Linux CI 不再生成，直接提交成品图）；生成文字层 PDF 用 reportlab 自带的 CID 字体 STSong-Light。

#### WI-00b1：契约 schema 与 receipt 写入
- **目标**：用 JSON Schema 落实 r2.2、r3.1、r3.2、r4.1、r4.2、r4.3、R2-12、R3-08 的全部契约；receipt 原子写入并校验。
- **文件**：
  - `schemas/{ocr_line,manifest,derived,cells,fixes,record,choices,export_map,receipt,build_receipt,check_receipt,publish_log,kb_version,isolation_receipt,writer_receipt,lane_receipt}.json`
  - `src/klode_kit/contract/schema.py`、`src/klode_kit/receipts.py`
  - `tests/contract/schema_examples/`
- **依赖**：00a。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] 每个 schema 至少各有一个合法和一个非法样例。
  - [ ] `choices.kind=partial` 被拒。
  - [ ] `build_receipt.steps` 不是固定顺序的 13 项就被拒。
  - [ ] `ocr_line.tokens[].geometry ∈ {exact, approx}`。
  - [ ] `record`：有 `effective` 时顶层 `status == effective.status`；没有 `effective` 时取最新候选的 status（R4-01）。
  - [ ] `export_map` 用 `value_audited` 字段，并带 `transforms[]`。
  - [ ] `isolation_receipt` 必含 `cli_version`、`effective_config_sha`、`argv_sha`。
  - [ ] `lane_receipt.env_fingerprint` 含 `release_sha`。
  - [ ] `receipts.write` 遇到非法 receipt 时抛异常且不生成文件；在 tmp 写完、`os.replace` 之前注入异常，不留半个文件。
- **必需测试**：
  - 参数化 schema 样例 — L1·S2
  - `test_receipt_write_invalid_no_file` — L2·S3
  - `test_receipt_crash_no_partial` — L2·S5
  - hypothesis 生成合法 receipt，写入再读出相等 — L1·S5（law：round-trip）

#### WI-00b2：键、指纹与配置
- **目标**：`doc_id/page_id/rec_id/batch_id/cfg_fp/attempt_id/run_id/audit_rev/build_id/field_id/decision_id/idempotency key` 的算法，加上 `config/defaults.toml`（r2.6 的全部键）及加载、覆盖、规范化序列化。
- **文件**：`src/klode_kit/contract/ids.py`、`src/klode_kit/config.py`、`config/defaults.toml`。
- **依赖**：00b1。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] `batch_id` 与输入顺序无关。
  - [ ] `rec_id` 与记录日期无关。
  - [ ] `cfg_fp`：r4.4 列出的任一输入变化，fp 就变；TOML 键序、空白变化，fp 不变；只改 `config_version`，fp 不变。
  - [ ] `attempt_id` 原子分配：8 个进程并发分配，得到 8 个互不相同、连续的 `t<seq>`；目录名里实际包含 attempt。
  - [ ] 数据项目的 `klode-kit.toml` 覆盖生效；未知键或缺默认键抛 `ConfigError`，退出码 40。
  - [ ] `build_id` 用注入的时钟。
- **必需测试**：
  - `test_batch_id_permutation_invariant` — L1·S4
  - `test_rec_id_date_metamorphic` — L1·S6
  - `test_cfg_fp_sensitivity`（逐项变异）— L1·S4
  - `test_cfg_fp_canonical` — L1·S6
  - `test_attempt_concurrent_unique`（真多进程、真文件系统）— L2·S5
  - `test_config_unknown_key_exit40` — L1·S3
- **风险**：规范化序列化要固定 TOML→JSON 的键排序与数值表示。

#### WI-00b3：来源链校验器 + roundtrip
- **目标**：把 r3.1 的三段不变量和版本绑定做成可复用的校验器，先在 `fx-chain` 上跑通；WI-31 再用它校验流水线的真实产物。
- **文件**：`src/klode_kit/contract/provenance.py`、`tests/contract/test_provenance_roundtrip.py`。
- **依赖**：00b1、00b2。**lane**：contract_replay。
- **验收标准**：
  - [ ] 干净的 `fx-chain`：0 违例。
  - [ ] 6 个损坏变体各自恰好报出对应违例：
    1. span 文本 ≠ 候选值
    2. `der.parent` ≠ `manifest.img_sha256`
    3. manual 决定缺 `evidence.region`
    4. 一个字段有两条生效决定
    5. alias transform 作用到数值上
    6. `export_map.rev` ≠ `audit_bindings.rev`
  - [ ] 以下合法情形通过：四路 10.5、人工改 1.05；dateshift 导出；别名归一；旧 build 面对新 rev；pdftext 的 bbox 经 `page_map.dpi` 换算。
- **必需测试**：
  - 6 个变体各一个用例 — L1·S3
  - `apply/unapply(transforms)` 恒等（hypothesis）— L1·S5
  - `test_advance_rev_keeps_old_build_valid` — L1·S6

#### WI-00c1：云端 argv 构建 + 确定性隔离审查（r4.3 的 (a) 层）
- **目标**：生成 Claude/Codex 两路的 argv、cwd、env 和临时目录；`isolation_check` 逐项给出布尔结果；实现 `isolation_guard` hook。
- **文件**：
  - `src/klode_kit/ocr/{cloud_argv,isolation_check,isolation_guard}.py`
  - `config/cloud_settings.json`（`blockReadsOutsideWorkingDirectories=true`、deny 列表、PreToolUse hook）
  - `prompts/ocr_read.md`、`schemas/ocr_cloud_output.json`
- **依赖**：00b2。**lane**：unit、contract_replay（hook 作为真实子进程运行）。
- **验收标准**：
  - [ ] Claude argv 包含 r4.3 列出的全部必含项，必不含项一个都没有（包括 `--bare`）。Codex argv 包含 `-C <tmp> -s read-only --ephemeral --ignore-user-config --ignore-rules -c mcp_servers={}`，不含 `--full-auto` 和 `-s danger*`。
  - [ ] 以下任一情况判 FAIL：删掉任一必含项、加入任一禁用项；cwd 在数据项目内（包括符号链接解析后在项目内）；env 含白名单外的键或 `CLAUDE_*`；临时目录里有多余文件，或 png 的 sha 不在 MANIFEST；`cloud_settings.json` 的 sha 不符；`claude --version` < 2.1.259，或 codex 版本低于锁定值。
  - [ ] `isolation_receipt` 绑定 CLI 版本、有效配置和 argv（来自「实施时注意」）。`receipt_still_valid(env)` 在任一项变化后返回 False。
  - [ ] `isolation_guard`：`file_path` 在 cwd 外时 exit 2 并写日志；在 cwd 内时 exit 0 并写日志；输入 JSON 畸形时 exit 2（失败即关闭）。
- **必需测试**：
  - `test_required_flag_removal_fails`（hypothesis 生成必含项的子集）— L1·S4
  - `test_forbidden_flag_added_fails` — L1·S4
  - `test_cwd_inside_project_fails`（含 symlink）— L1·S3
  - `test_receipt_invalid_on_cli_version_change` — L1·S3
  - `test_guard_hook_subprocess`（真 python 进程，stdin 输入 JSON）— L3·S3
- **风险**：r3.7 ⑫ 的 `--setting-sources ""` 是否合法由 00c2 判定。如果需要改必含项集合，属于规格变更，必须写明理由（policy 第 12 条）。

#### WI-00c2：隔离实测 spike（r4.3 的 (b) 层 + R4-02/04/05）【G2】
- **目标**：在合成环境里用宿主证据证明两路云端的读取边界和启动上下文隔离成立。
- **文件**：`src/klode_kit/ocr/isolation_probe.py`、`tests/model/test_isolation_probe.py`、`docs/spikes/WI-00c-isolation.md`（新文件）。
- **依赖**：00a、00c1。**lane**：model_integration。
- **验收标准**：
  - [ ] canary 文件生成在 `synth_project` 和 `$HOME` 下，**以确切绝对路径传入**，调用前断言文件存在，调用后删除（只删本测试刚建的文件）。
  - [ ] Claude 路用 `--output-format stream-json --verbose`，按 tool_use id 对上真实的 tool_result；拒绝证据取自 `permission_denials` 和 hook 日志，不取自模型叙述。每个 canary 的结果分成「已拒绝 / ENOENT / 未尝试」三类。只有全部「已拒绝」且输出不含 nonce 才算 PASS；「未尝试」或 ENOENT 判 FAIL。Codex 路用 `--json` 事件流，做同样分类（r2.11 ⑩：不能把 `read-only` 理解为只能读 cwd）。
  - [ ] **R4-02**：在合成祖先目录的 CLAUDE.md、合成项目的 `.claude/`，以及（cc-doc-search 查到确有文档化的配置目录覆盖机制时）合成 user memory 里各放一个 nonce。断言模型输出和 init/system 事件里都没有 nonce。如果宿主拿不出确定性证据，在 spike 文档里记为残余风险，保持真实数据路径关闭，由用户决定。
  - [ ] 解决 ⑫ 和 ⑭，锁定 CLI 版本下限，写进 `defaults.toml`。
  - [ ] 两路 `isolation_receipt` 都 PASS 并提交。某路 FAIL 时，该路 `routes_enabled=false`，触发重新规划（见风险 R-4）。
- **必需测试**：
  - `test_claude_probe_denials` — L3·S3
  - `test_codex_probe_denials` — L3·S3
  - `test_startup_context_nonce_absent` — L3·S3
  - `test_canary_path_preexists`（unit 前置）— L2·S3
  - `test_classify_denied_enoent_unattempted`（unit，喂录制好的 stream-json 事件）— L1·S2

#### WI-00c3：最难页四路 spike + `eval_fields` / `cer`【G2 在实跑那一步】
- **目标**：先用 TDD 做四项字段指标和 CER（确定性部分）；再在合成最难页上实跑四路，产出回放录制和锁定参数。
- **文件**：`src/klode_kit/eval/eval_fields.py`、`src/klode_kit/cer.py`（从 189 搬入）、`tests/unit/test_{eval_fields,cer}.py`、`tests/model/test_hardest_pages_4routes.py`、`tests/fixtures/replay/<engine>/<sha>.json`（标 `recorded:true`）、`docs/spikes/WI-00c-4routes.md`。
- **依赖**：00a、00b1、00c2。**lane**：unit、model_integration。
- **验收标准**：
  - [ ] 四项指标的口径和阈值按 r2.7。`auto_verified_total==0` 时记 `n/a`，lane FAIL，原因 `no_auto_verified_samples`。四路一致字段数从**运行结果**算，不足 20 判 FAIL。
  - [ ] `cer.normalize` 对数字只做 NFKC。
  - [ ] 实跑（隔离 receipt PASS 是前提）产出：各路 CER；四项指标；R3-05 的 ocrmac 粒度结论（exact 还是 approx，以及降级后人工确认的比例）；⑤′ 锁定 Codex 最高 effort 的取值；各路录制写入 replay。
  - [ ] 如果错误自动通过率 > 0，**停止并重新规划**。
- **必需测试**：
  - `test_false_auto_pass_zero_samples_fails` — L1·S3
  - `test_metrics_on_truth_vs_perturbed`（hypothesis 随机扰动真值，指标单调不升）— L1·S4
  - `test_cer_selftest_port` — L1·S1
  - `test_cer_numeric_not_folded` — L1·S6
  - 四路实跑 — L3·S1

#### WI-00d1：执行桥（确定性部分）
- **目标**：CLI 桥的 argv、线程池并发、超时（按进程组终止）、取消、重试和 receipt。
- **文件**：`src/klode_kit/bridge.py`、`tests/fakes/bin/claude`（场景：成功、永久 sleep、exit 1、畸形 JSON、派生子进程）。
- **依赖**：00b1、00b2。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] argv 包含 `--agents <由 agents/*.md 生成的内联 JSON> --agent klode-card-writer --permission-mode dontAsk`，`--allowedTools "Read,Grep,Glob,Edit(cards_content/**),Bash(*/bin/klode-kit *)"`，`--disallowedTools "Edit(library/**),Edit(src/**),Edit(.claude/**)"`，以及 `--output-format json --json-schema --max-budget-usd --no-session-persistence`。cwd 为 `builds/<build_id>/`。不出现任何 `--force` 路径。内部 checker 统一经启动器调用（R2-06 剩余项）。
  - [ ] 超时时整个进程组终止，孙进程也不存活。
  - [ ] 10 个任务、并发 4 时，同时运行数 ≤ 4。
  - [ ] SIGINT 父进程后，所有子进程终止，receipt 记 `cancelled`。
  - [ ] 畸形 JSON 记 `parse_error` 并重试，≤ `writer_max_rounds` 次。
  - [ ] `cost_usd` 非空。
- **必需测试**：
  - `test_timeout_kills_process_group` — L4·S3
  - `test_concurrency_bound`（假可执行文件写时间戳）— L2·S4
  - `test_cancel_propagates` — L4·S3
  - `test_malformed_retry_exhausted` — L1·S3
  - `test_argv_contract` — L1·S2
- **配对**：00d2。

#### WI-00d2：宿主 spike（CLI 桥实写一张卡 + Workflow 两段式）【G2、G5】
- **目标**：完成 r2.5 和 r3.6 规定的 WI-00d 断言。
- **文件**：
  - `tests/host/test_bridge_spike.py`
  - 最小版本的 `.claude-plugin/plugin.json`、`agents/klode-card-writer.md`、`skills/kb-check/SKILL.md`（只输出 `plugin{name,version,entry,root_sha}`）、`workflows/kb-pipeline.js`
  - `src/klode_kit/hostenv.py`（信任探测，对应 ⑬）
  - `docs/spikes/WI-00d.md`
- **依赖**：00d1、00a。**lane**：host_acceptance、model_integration。
- **验收标准**：
  - [ ] (a) 对 `fx-book-aurora` 第 1 章，经 CLI 桥写成一张卡且 `check_cards` PASS。写 `cards_content/x.md` 成功；写 `library/x` 被拒，证据取自 `permission_denials`。
  - [ ] (b) `/klode-kit:kb-pipeline` 第一段以 exit 10 结束并写 `awaiting_confirm`；`args.resume` 时第二段完成并命中缓存。确定 headless 是否可行（⑪）。
  - [ ] ⑬ 信任探测方法写进代码，或记为不可探测。
  - [ ] ⑮ `claude mcp get` 遇到 `env` 键时，能解析出 connected。
  - [ ] R2-11：`claude -p --plugin-dir . "/klode-kit:kb-check --kb tests/fixtures/kb/fx-labs-3x"` 产出的 receipt 里 name、version、entry 三项断言通过。
- **必需测试**：上面每一项各一个用例，都是 L3·S1；拒写那一项是 L3·S3。
- **风险**：headless 跑不了 Workflow 时，U3 的 `/workflows` 观察降级为用户手动观察（写进 WI-31）。

#### WI-00e：三张 WUA 图通用版（文档 WI，不设用户停点）
- **目标**：按交接§3-5 用 wfviz 画常用场景、用户用例、工作流三张图，包含 r2–r4 的回路和状态机。
- **文件**：`docs/wua/{scenarios,usecases,pipeline}.wf.json`、`docs/wua/*.svg`、`tests/docs/test_wua.py`（`WFVIZ_SRC` 指向 001 的 `src`，缺失则 FAIL）。
- **依赖**：00b1。**lane**：cli_integration。
- **验收标准**：
  - [ ] 三张图 wfviz 都 exit 0，SVG 非空。
  - [ ] scenarios 恰好 6 个（拍板 2）；usecases 覆盖 U1–U8。
  - [ ] pipeline 含 preflight、ocr、audit、writeback、build、check、publish 各阶段；唯一的人工步骤是「点选」；L1–L3 三条 loop 都满足 `from` 在 `to` 之后；画出 `extracted→pending_confirm→verified|disputed` 状态机和 effective/candidate 的说明。
  - [ ] 不用禁用键，每个工件都至少被一步引用。
- **必需测试**：`test_wfviz_renders` — L3·S1；`test_pipeline_graph_content` — L1·S2。

### M1 · OCR + 审计

#### WI-04a：本机引擎 A/B 搬入 + 几何
- **目标**：搬入 189 的 OcrMac（多语言）、Mlx（Python API，主/备 prompt，`repeat_ratio`）、Tesseract（`chi_sim+jpn`，tsv）；按 R2-08、R3-05 输出 `lines/tokens/block_bbox`。
- **文件**：`src/klode_kit/ocr/{engines,geometry}.py`。
- **依赖**：00b1、00c3（粒度结论）。**lane**：unit、contract_replay、cli_integration（`macos`、`macos_mlx`）。
- **验收标准**：
  - [ ] Vision（左下原点、归一化坐标）、MLX（0–999）、tsv 三种坐标统一换算成像素、左上原点。
  - [ ] MLX 输出 `tokens:null` 加 `block_bbox`。
  - [ ] approx 框标记 `geometry:approx`。
  - [ ] 退化（`repeat_ratio > 0.3`）时依次走 alt prompt、tesseract 兜底，并记 `degenerate_output`。
  - [ ] 回放的原始输出转换后合乎 `ocr_line` schema。
  - [ ] 真实 ocrmac/tesseract 在 fx 图上输出的 bbox 都在图像边界内。
- **必需测试**：
  - `test_geometry_roundtrip`（像素→归一化→像素，误差 ≤ 1px）— L1·S5
  - `test_engine_replay_conforms` — L1·S2
  - `test_degenerate_fallback_chain` — L1·S3
  - `test_real_ocrmac_bounds`（cli、macos）— L3·S4
- **风险**：G4（安装依赖、权重）。权重缺失时 `macos_mlx` 子集 FAIL，并由 doctor 报出来。

#### WI-04b：`run_batch` 重写 + 方向转正
- **目标**：以图片目录为输入，生成 manifest/derived，转正方向，按 `run_id` 四段式落盘，支持原子写、续跑、重试和显式缺页。
- **文件**：`src/klode_kit/ocr/{run_batch,orient,manifest}.py`。
- **依赖**：04a、00b2。**lane**：unit、contract_replay、cli_integration。
- **验收标准**：
  - [ ] `fx-rotated90` 每页 receipt 写 `rotate: 90`（U2）。
  - [ ] 落盘目录为 `ocr/<engine>/<cfg_fp>.t<seq>/<page>.jsonl`。这样做是为了让 U2/U6 的 `ocr/<engine>/<fp>/p001.jsonl` 字面成立，见风险 R-3。
  - [ ] 进程被杀后续跑：已完成的页不重跑、无重复。
  - [ ] `--redo` 产生新 attempt，旧目录的树 sha 不变；普通 resume 复用三个 hash 都匹配的 done 结果；U6 的重做入口不会误走普通 resume。
  - [ ] 失败重试 ≤ `route_retry` 次后记 `route_failed`；缺页记 `route_missing_page`，不许静默（E21）。
- **必需测试**：
  - `test_page_conservation`（hypothesis：receipt 的页集合 == manifest × routes；对应 E68「回放前后页标数相同」）— L2·S5
  - `test_resume_after_kill` — L2·S5
  - `test_redo_new_attempt_old_untouched` — L2·S5
  - `test_rotate_receipt_u2` — L2·S1

#### WI-04c：文字层 PDF 分流
- **目标**：逐页 `pdftotext -bbox-layout`，分成 text/empty/garbled 三类；输出 pdftext JSONL，以及 `text_to_whole` 和 `page_map`。
- **文件**：`src/klode_kit/ocr/{textlayer,text_to_whole}.py`。
- **依赖**：04b。**lane**：unit、cli_integration。
- **验收标准**：
  - [ ] `fx-text-pdf` 的 receipt 写 `text_layer: true, routes_skipped:[vision,mlx,claude,codex]`（U4）。
  - [ ] bbox 像素 = pt × dpi / 72。
  - [ ] `fx-mixed-pdf` 逐页分类正确，empty 和 garbled 页进入四路。
  - [ ] 边界：`min_chars=50`（49 判 empty）、`garbage_max=0.05`。
- **必需测试**：
  - `test_classify_boundaries` — L1·S2
  - `test_whole_pagemap_roundtrip`（任一行经 `page_map` 定位回原页文本）— L1·S5
  - `test_real_pdftotext_mixed`（cli）— L3·S1

#### WI-05a：抬头打码
- **目标**：用 Vision 行框定位关键词行和页顶区域，涂黑；`_masked/` 加 MANIFEST；对照图放 `_mask_review/`。
- **文件**：`src/klode_kit/audit/mask.py`、`config/mask_rules.toml`。
- **依赖**：04a、04b。**lane**：unit、cli_integration（`macos`）。
- **验收标准**：
  - [ ] 掩码框内像素全为 0，框外像素与原图相同。
  - [ ] MANIFEST 行含 `{der_sha256,parent,page_id,mask_boxes,tool_version}`。
  - [ ] 重跑时 MANIFEST 不出现重复行。
  - [ ] 对打码后的图再做 Vision 识别，不出现「测试患者甲 / T-0000001」。
  - [ ] 对照图只出现在 `_mask_review/`。
- **必需测试**：
  - `test_mask_pixel_conservation`（hypothesis 生成框）— L2·S4
  - `test_manifest_idempotent` — L2·S5
  - `test_masked_text_absent`（cli）— L3·S3
- **风险**：U2 字面写「`_masked/` 下出现…打码对照图」，与 r2.2-B 冲突（风险 R-2）。

#### WI-05b：发送器契约 + `privacy_guard` hook
- **目标**：外发前重算 sha 并比对白名单，不符就 exit 60 且零调用；临时目录在数据项目外、权限 0700；有效隔离 receipt 是前提；日志不含路径和像素。
- **文件**：`src/klode_kit/ocr/{send,privacy_guard}.py`、`hooks/hooks.json`。
- **依赖**：05a、00c1。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] sha 不在登记表中，或路径在 `_mask_review/` 内：exit 60，假 `claude`/`codex` 的调用日志为空。
  - [ ] 隔离 receipt 无效：exit 61。
  - [ ] 临时目录在系统 TMPDIR 下、权限 0700、只有 3 个文件；即使发生异常也会删除。
  - [ ] hook 遇未登记 sha 时 exit 2。
- **必需测试**：
  - `test_unlisted_sha_exit60_no_spawn` — L2·S3
  - `test_review_dir_rejected` — L2·S3
  - `test_receipt_no_path_leak`（hypothesis 生成路径）— L1·S4
  - `test_tmpdir_cleanup_on_exception` — L2·S3
  - `test_hook_subprocess` — L3·S3
- **配对**：00c2、06a/06b 的 model 用例。

#### WI-06a / WI-06b：Claude / Codex 读图适配器
- **目标**：经 `send()` 调用，输出合乎契约的 JSONL（`bbox_px:null`、`tokens:null`）；receipt 记模型、费用和 `model_actual`。
- **文件**：`src/klode_kit/ocr/adapters_{claude,codex}.py`。
- **依赖**：05b、00c3（录制）。**lane**：contract_replay、model_integration【G2：重新录制】。
- **验收标准**：
  - [ ] 回放输出合乎 schema。
  - [ ] 超时记 `route_failed`。
  - [ ] schema 不合法时重试 1 次，仍失败记 `parse_error`。
  - [ ] 累计费用超过 `cloud.max_cost_usd_per_batch` 时 exit 50，并停止后续发送。
  - [ ] Codex 路用 `-o out.json --json`，并带 `-c model_reasoning_effort=<锁定值>`。
- **必需测试**：
  - `test_replay_conforms` — L1·S2
  - `test_timeout_route_failed` — L1·S3
  - `test_budget_stop_exit50` — L2·S3
  - `test_live_vs_replay_shape`（model）— L3·S1

#### WI-07a：字段抽取
- **目标**：`number|date|drug|unit|flag` 五类字段及其 span。
- **文件**：`src/klode_kit/audit/fields.py`、`config/fields/{units,flags,drugs}.txt`（合成药名，含「示例片」）。
- **依赖**：00b1。**lane**：unit。
- **验收标准**：
  - [ ] 能识别小数、范围、`×10^9/L`、`<0.1`；三种日期写法；常见单位；H/L/↑/↓/*；药名 + 剂量（如 `示例片 10mg`）。
  - [ ] 对每个抽出的字段，`text[s:e] == value`。
- **必需测试**：
  - `test_span_property`（hypothesis 生成行）— L1·S4
  - `test_fullwidth_metamorphic`（全角/半角数字等价）— L1·S6
  - `test_10_5_vs_1_05_distinct` — L1·S3

#### WI-07b：单元格对齐
- **目标**：按 r2.2-D、R2-08、R3-05 实现几何基准路、行列聚类、云端路单调对齐和显式 uncertain；`items.yaml` 加载器也在这里实现。
- **文件**：`src/klode_kit/audit/align.py`、`src/klode_kit/items.py`、`config/items.yaml`。
- **依赖**：07a、04a。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] 基准路取第一个有 exact tokens 的路。
  - [ ] 行：竖直重叠 ≥ 50% 合并为一行。列：按 token x0 聚类。
  - [ ] approx 框只有在同一行另有 exact 几何佐证时才参与，否则记 `no_geometry`。
  - [ ] 对不上记 `unaligned`；跨行合并记 `merged_rows`；重复值保留 `ordinal`。
- **必需测试**：
  - `test_nonbase_engine_permutation_invariant` — L1·S6
  - `test_alignment_monotonic`（hypothesis：对齐的下标对严格递增）— L1·S4
  - `test_approx_without_exact_no_geometry` — L1·S3

#### WI-07c：票型比对 + 白名单
- **目标**：N 路票型（全路齐且一致才算 agree）；`swap_suspect`；引擎弱点白名单；r2.2-D 的四个契约样例。
- **文件**：`src/klode_kit/audit/{compare,whitelist}.py`、`config/engine_weakness.toml`、`tests/contract/fields/`。
- **依赖**：07b。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] dup10：两个 cell 都 4/4 agree，不合并。
  - [ ] colshift：该 cell 分歧，并带 `item_label`。
  - [ ] swap：两个 cell 都分歧且标 `swap_suspect`，绝不判 agree。
  - [ ] missingrow：该行所有 cell 记 `missing_cell`；即使其余三路一致，也只能进确认页。
  - [ ] 白名单不作用于数值字段。
- **必需测试**：
  - 四个契约样例 — L1·S3
  - `test_auto_agree_implies_all_present_equal`（hypothesis 生成随机四路网格，属于关键不变量）— L1·S4
  - `test_vote_engine_permutation` — L1·S6
  - `test_recommended_in_candidates` — L1·S4

#### WI-07d：裁图
- **目标**：按行 ±1 裁图，像素框经变换链（rotate、crop、mask）回映到原图。
- **文件**：`src/klode_kit/audit/crop.py`。
- **依赖**：07b、05a。**lane**：unit。
- **验收标准**：
  - [ ] 裁图范围是基准路该行框 ±1 行。
  - [ ] 没有 bbox 时退回整行或半页，并记 `no_bbox_crop`。
  - [ ] 裁出的图不越出图像边界。
- **必需测试**：
  - `test_transform_roundtrip`（rotate ∈ {0,90,180,270} × 随机 crop 偏移）— L1·S5
  - `test_crop_within_bounds` — L1·S4

#### WI-08a：审计阶段与 `audit_rev`、uncertain 原因表
- **文件**：`src/klode_kit/audit/{stage,gate}.py`。
- **依赖**：07c、07d、04b。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] run 集合变化时生成 `a<N+1>`，不变时复用。
  - [ ] 同一 `a<N>` 内 `cells.jsonl` 和 `confirm.html` 只写一次，第二次写抛异常。
  - [ ] page 级 uncertain 使候选为 `disputed`（r3.3）。
  - [ ] uncertain 条目含 `reason/scope/fix_action/clear_evidence`。
  - [ ] `uncertain_log` 只追加。
- **必需测试**：
  - `test_write_once` — L2·S3
  - `test_uncertain_log_append_only`（前缀字节不变）— L2·S5
  - `test_page_level_to_candidate_disputed` — L1·S3

#### WI-08b：确认页 HTML + `choices` 导出 + verifier 提示
- **文件**：`src/klode_kit/audit/confirm_page.py`、`templates/confirm.html`、`templates/confirm_export.js`。
- **依赖**：08a。**lane**：unit、cli_integration（`needs_node`）。
- **验收标准**：
  - [ ] 每条分歧都有：分歧小图、四路候选值（逐路标注）、★推荐值、「都不对 / 手输」两个选项。
  - [ ] 缺路时显示「缺路，不可点选」和修复命令。
  - [ ] 显示「生效 r2 (verified) / 候选 r3 (disputed: …)」。
  - [ ] 有 `advice.json` 时只作提示显示。
  - [ ] 页面不引用任何外部 http(s) 资源。
  - [ ] 导出的 JS 用 node 执行后，生成的 `choices.json` 合乎 schema，并含 `confirm_sha256` 和 `fingerprints`。
- **必需测试**：
  - `test_item_structure_u8`（html.parser）— L1·S2
  - `test_no_external_urls` — L1·S3
  - `test_export_js_node`（cli）— L3·S1

#### WI-08c：写回（唯一提交者，候选/生效两层）
- **文件**：`src/klode_kit/audit/writeback.py`。
- **依赖**：08a、08b。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] `batch_id`、`fingerprints`、`rev_base`、`seen` 任一不符：exit 30，零写入。
  - [ ] 同一记录原子提交（共用 `commit_id`），`per_record[]` 记每条结果。
  - [ ] manual 空串按 `none` 处理；`none` 保持 `disputed`；`none` + `not_a_field` 标 `dropped`。
  - [ ] 只有 verified 候选才推进 `effective`；旧候选不能覆盖更新的 rev。
  - [ ] `_rev/*.r<N>.md` 只读不可改。
  - [ ] 崩溃恢复：`fixes.jsonl` 里有 `commit_id` 但没有提交标记的，重启后补完或回滚。
- **必需测试**：
  - `test_idempotent_resubmit`（重复提交不产生新 rev）— L2·S5
  - `test_fixes_append_only` — L2·S5
  - `test_effective_rev_monotonic`（hypothesis 生成随机提交序列）— L2·S5
  - `test_crash_injection_consistency`（在每个写点注入崩溃）— L2·S5
  - `test_stale_choices_exit30` — L2·S3
  - `test_manual_empty_is_none` — L2·S3

#### WI-08d：兼容视图 + U6/U8 行为
- **文件**：`src/klode_kit/audit/views.py`、`tests/replay/test_u6_redo_keeps_verified.py`。
- **依赖**：08c。**lane**：contract_replay。
- **验收标准**：
  - [ ] `records/<date>-<type>.md` 是指向 `effective` rev 的符号链接；同日同类型依次加 `-2`、`-3`。
  - [ ] **R4-01**：没有 `effective` 时，`records/` 视图指向最新候选，并显示其状态；`verified/` 只放生效的 verified 记录。
  - [ ] 视图重建是确定性的。
  - [ ] r4.1 的 U6 测试全部断言成立（①–④ 和续段），全程不改 `_rev/X.r1.md`。
- **必需测试**：
  - `test_view_rebuild_idempotent` — L2·S5
  - `test_first_ingest_pending_view_r4_01` — L2·S2
  - `test_u6_redo_keeps_verified` — L2·S5

### M2 · build / check / 发布 / MCP

#### WI-01a：`records_to_txt` + `export_map`
- **文件**：`src/klode_kit/build/records_to_txt.py`。
- **依赖**：08c、07b（items）、00b3。**lane**：unit、contract_replay、cli_integration。
- **验收标准**：
  - [ ] 每行格式为 `<item> <value_audited> <date> [flag] ⟨rec_id⟩`，分两个书架：items 和 records。
  - [ ] 值从 `audit_bindings[rec_id].rev` 读取；只导出 verified 记录。
  - [ ] **禁止读 `cells.jsonl`**：删掉或打乱 `cells.jsonl`，输出 sha 不变。
  - [ ] 拒绝空记录；源行与观测行分开归类。
  - [ ] `test_u5_after_manual_fix`：`klode verify <card> "示例片 1.05 2026-09-18"` 返回 VERIFIED，`10.5` 返回 NOT FOUND。用 `<card> <phrase>` 的调用形式（「实施时注意」）。
  - [ ] `fx-labs-3x`：3 行互不相同。
- **必需测试**：
  - `test_cells_independence` — L2·S6
  - `test_lines_unique_by_construction`（hypothesis）— L1·S4
  - `test_export_map_provenance`（用 00b3 的校验器）— L1·S5
  - `test_u5_after_manual_fix`（cli）— L3·S3

#### WI-01b：`records_to_l1` + `items_index`
- **文件**：`src/klode_kit/build/records_to_l1.py`。
- **依赖**：01a、02a。**lane**：unit、cli_integration。
- **验收标准**：
  - [ ] 卡片 front-matter 写 `kind: deterministic`。
  - [ ] 观测覆盖率 100%：锚点数 == 观测行数，且无重复。
  - [ ] Thin 写最新值加趋势；Full 每次观测一个整行锚点。
  - [ ] records 架同样生成 deterministic 卡。
  - [ ] `fx-labs-1x` PASS；`fx-labs-3x` 3/3 PASS，2/3 FAIL。
  - [ ] cli 下 `klode verify` 三条全部 VERIFIED，`zoom full` 三条齐全。
- **必需测试**：
  - `test_coverage_equals_rows`（hypothesis）— L1·S4
  - `test_fx_labs_3x_real_klode`（cli）— L3·S1

#### WI-01c：`deident` 两档脱敏
- **文件**：`src/klode_kit/build/deident.py`。
- **依赖**：01a。**lane**：unit。
- **验收标准**：
  - [ ] 默认 `pseudonym`：日期不动，假名稳定（加盐 hash）。
  - [ ] `dateshift`：全库单一偏移，密钥只存 `_private/deident_key.json`。
  - [ ] 合成样本脱敏后，PII 正则 0 命中。
- **必需测试**：
  - `test_pseudonym_stable`（同输入同输出）— L1·S5
  - `test_dateshift_preserves_order_and_intervals`（hypothesis）— L1·S5
  - `test_pii_zero_hits` — L1·S3

#### WI-02a：vendoring 第一批（`split_by_manifest` B5、`skeleton`、`check_cards`、`fix_anchors` B1）
- **文件**：`src/klode_kit/build/{split_by_manifest,skeleton,check_cards,fix_anchors}.py`、`config/head_re.toml`。
- **依赖**：00b1。**lane**：unit、cli_integration。
- **验收标准**：
  - [ ] `HEAD_RE` 外置为配置，能识别中文章头（「第一章」「一、」「检验报告」）。
  - [ ] B5：重复执行 `--apply`，BIBLIOGRAPHY 不重复追加。
  - [ ] B1：`"reset -- this"` 的变体集合含 `"reset — this"`。
  - [ ] `check_cards` 支持 `--json` 和 `--policy`；**policy 由来源类型（`library/items|records` 下的源）决定，不信卡片自填的 `kind`**（R2-02 剩余项）。
  - [ ] 去掉所有硬编码路径（G10）。
- **必需测试**：
  - `test_split_concat_equals_original`（hypothesis）— L1·S5
  - `test_b5_reapply_no_dup`（先写复现用例，确认红）— L2·S5
  - `test_b1_longest_match`（先写复现用例，确认红）— L1·S3
  - `test_fix_anchors_variants_superset` — L1·S6
  - `test_policy_from_source_not_kind` — L1·S3
  - `test_cn_heads` — L1·S2

#### WI-02b：`write_card` 内置门禁（B2）+ `check_published`
- **文件**：`src/klode_kit/build/{write_card,check_published}.py`。
- **依赖**：02a。**lane**：unit、cli_integration。
- **验收标准**：
  - [ ] 门禁 1 FAIL：exit 1，`library/cards` 的字节不变。
  - [ ] `--force` 只给人工用，用了即在 receipt 记 `forced=true`，该步骤失败。
  - [ ] `check_published` 对发布卡标记线以下的内容重跑逐字规则；能抓出 `--` 这类 FOLDED-ONLY 锚点和陈旧 receipt。
- **必需测试**：
  - `test_b2_fail_no_write`（先写复现用例，确认红）— L2·S3
  - `test_forced_receipt_fails` — L2·S3
  - `test_published_folded_detected`（cli）— L3·S3

#### WI-02c：书桌（`desk_build` B3、`check_synthesis` B4、`eval_compare` B3、`cue_suggest`）
- **文件**：`src/klode_kit/build/{desk_build,check_synthesis,eval_compare,cue_suggest}.py`。
- **依赖**：02a。**lane**：unit、cli_integration。
- **验收标准**：
  - [ ] B3：通过 `klode --json -c … diagnose` 解析，不含连字符的维度名（如 `safety`）也能判对。
  - [ ] B4：纯中文的加粗动作头会报 S4。
  - [ ] 调优集与留出集分开计分；**调优集全对但留出集 < 4/5 时仍 FAIL**。
  - [ ] 留出集冻结 sha，修改须 `--refreeze --reason`。
  - [ ] `cue_suggest` 只读调优集。
- **必需测试**：
  - `test_b3_json_parse`（cli）— L3·S3
  - `test_b4_cn_action_head` — L1·S3
  - `test_tune_pass_holdout_fail` — L1·S3
  - `test_cue_suggest_ignores_holdout`（metamorphic：改动留出集，输出不变）— L1·S6

#### WI-02d：`gen_agent` 参数化 + `smoke_mcp` + prompts 去路径
- **文件**：`src/klode_kit/build/{gen_agent,smoke_mcp}.py`、`templates/agent_records_zh.md`、`prompts/{write_card,synthesis}.md`。
- **依赖**：02c。**lane**：unit、cli_integration。
- **验收标准**：
  - [ ] 支持 `lang_source/lang_answer/source_kind/cite_format` 参数；医疗模板不出现「原文是英文」。
  - [ ] **R4-03**：生成文件第一行是 `---`，版本注释 `<!-- klode-kit build <id> -->` 放在闭合 frontmatter 之后。
  - [ ] example 由 `desk.json.tests` 自动填。
  - [ ] `smoke_mcp` 默认 query 取 alias 的第一项，6 项 PASS。
  - [ ] prompts 里没有 003/189 等私有路径。
- **必需测试**：
  - `test_first_line_frontmatter` — L1·S3
  - `test_zh_template_no_english_claim` — L1·S3
  - `test_smoke_6_pass`（cli）— L3·S1

#### WI-03a：`init_kb` + `gen_manifest`
- **文件**：`src/klode_kit/build/{init_kb,gen_manifest}.py`。
- **依赖**：02a。**lane**：cli_integration、unit。
- **验收标准**：
  - [ ] `init_kb` 补齐 `[library] id/name/description`，建目录，`.gitignore` 追加 `_work/ _raw/`（G11/G14）。
  - [ ] `gen_manifest`：候选章头 < 2 时整本作为单一来源；切章后拼回自校验；U7 显式 `--manifest` 可覆盖。
- **必需测试**：
  - `test_init_kb_toml_fields`（cli）— L3·S1
  - `test_init_idempotent` — L2·S5
  - `test_lt2_heads_single_source` — L1·S2

#### WI-03b：`gen_desk`（通用 + 医疗）
- **文件**：`src/klode_kit/build/gen_desk.py`、`templates/{desk_records.json,holdout_records.jsonl,holdout_generic.jsonl}`。
- **依赖**：03a、02c。**lane**：unit。
- **验收标准**：
  - [ ] 维度合并规则：> 8 章合并到 ≤ 8 个维度；< 2 章整本一个维度。
  - [ ] **单章、单项目时也自动生成 ≥ 5 道互不相同的留出题**。
  - [ ] 医疗留出题只从实际存在的维度生成；不足时 `holdout_insufficient`，exit 20。
- **必需测试**：
  - `test_holdout_ge5_distinct`（hypothesis 生成章节数和项目数）— L1·S4
  - `test_holdout_insufficient_exit20` — L1·S3

#### WI-10b：doctor / preflight 前提（提前到 09a 之前）
- **文件**：`src/klode_kit/check/doctor.py`。
- **依赖**：00b2、00d2（⑬ 的结论）。**lane**：unit、cli_integration。
- **验收标准**：
  - [ ] 检查 r2.5 doctor 清单的每一项；缺项按类别 exit 40，并给出修复命令。
  - [ ] import `klode.lib.mcp_server` 做冒烟，判定 MCP 包装用哪一级降级，写入 receipt。
  - [ ] 数据项目未信任，或 `settings.local.json` 被 git 跟踪：exit 40，并给出提示。
  - [ ] 不下载任何东西。
- **必需测试**：
  - `test_missing_binary_exit40`（PATH 里去掉 klode）— L3·S3
  - `test_tracked_settings_local_exit40` — L2·S3

#### WI-09a：pipeline 内核
- **文件**：`src/klode_kit/pipeline.py`。
- **依赖**：10b、00b1、00b2。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] 阶段为 preflight / ocr / audit / writeback / build / check / publish。
  - [ ] 三个 hash 都匹配才复用 done；产物缺失或 sha 不符就重跑。
  - [ ] `fcntl` 锁：第二个并发进程 exit 40。
  - [ ] receipt 为 `running` 且 pid 已死，视为崩溃并重跑该步。
  - [ ] 退出码全集：0 / 2 / 10 / 20 / 21 / 22 / 30 / 40 / 50 / 60 / 61。
  - [ ] 环境类错误不进入回路。
- **必需测试**：
  - `test_done_reuse_requires_three_hashes` — L2·S5
  - `test_lock_exit40`（真双进程）— L2·S3
  - `test_crash_recovery_dead_pid` — L2·S5
  - `test_exit_code_table` — L1·S2

#### WI-09b：13 步 build 落入不可变目录 `builds/<build_id>/`
- **文件**：`src/klode_kit/build/run_build.py`。
- **依赖**：09a、01a–c、02a–d、03a–b、00d1。**lane**：contract_replay、cli_integration。
- **验收标准**：
  - [ ] 13 步按固定顺序执行：医疗走 deterministic 卡；书类经 CLI 桥派发 writer，replay 下由假 claude 回放。
  - [ ] `audit_bindings` 快照只取 `effective`；`excluded[]` 写明 rec_id 和原因；`stale_candidates` 正确。
  - [ ] build 目录完成后 `chmod a-w`，`MANIFEST.sha256` 校验通过。
  - [ ] `builds/` 之外零写入。
  - [ ] 流水线从不传 `--force`。
- **必需测试**：
  - `test_no_writes_outside_builds`（快照前后的树 diff）— L2·S5
  - `test_excluded_not_in_inputs_u8` — L2·S3
  - `test_build_u7_real_klode`（cli）— L3·S1

#### WI-09c：三条回路 + 终止条件
- **文件**：`src/klode_kit/loops.py`。
- **依赖**：09b、02c。**lane**：contract_replay。
- **验收标准**：
  - [ ] L1、L2、L3 每条都走完「错误 → 修改器 → 重新发布 → 下游失效 → 复检」完整链。
  - [ ] L3 失败升级为 L2；L2 与 L3 同时触发时先走 L3（来自「可留 TDD」项）。
  - [ ] 问题清单 hash 连续两轮相同（无进展），或达到 `max_total_rounds`，或超预算：exit 50，receipt 记已花费金额和剩余问题。
  - [ ] 预算预留：派发前扣减预估费用。
- **必需测试**：
  - `test_no_progress_terminates` — L2·S3
  - `test_l3_escalates_l2` — L2·S3
  - `test_rounds_monotone_bounded`（hypothesis 生成失败序列）— L1·S4

#### WI-10a：`/kb-check` 内核（作用于 build 目录）
- **文件**：`src/klode_kit/check/kb_check.py`。
- **依赖**：09b、02b。**lane**：cli_integration、contract_replay。
- **验收标准**：
  - [ ] 全库判据：zoom 全为 full；aliases ≥ 10；锚点按 `kind` 判定（authored ≥ 5；deterministic 覆盖率 100%，records 架也计入）。
  - [ ] 还要通过 strict 0/0、`check_published`、留出集 ≥ 4/5（且 ≥ 5 题）、`smoke_mcp`。
  - [ ] receipt 记 `plugin` 字段；失败项映射回 L1–L3，或 exit 40/50。
  - [ ] stub 卡、FOLDED 锚点必须判 FAIL（B6 由这里兜住）。
- **必需测试**：
  - `test_stub_card_fails`（cli）— L3·S3
  - `test_forced_receipt_fails_check` — L2·S3
  - `test_loop_mapping` — L1·S2

#### WI-09d：发布协议（`current` 指针、`PUBLISH.log`、replay、rollback、prune）
- **文件**：`src/klode_kit/publish.py`。
- **依赖**：10a、09b。**lane**：unit、contract_replay。
- **验收标准**：
  - [ ] 切换是一次 `os.replace`。
  - [ ] `PUBLISH.log` 每行带 `seq` 和 `target_build_id`。
  - [ ] `--replay`：`current` 已不是目标 build 时 exit 22；遇到指向更新 build 的登记，记 `skipped_newer`。
  - [ ] replay 与 rollback 共用发布锁；待恢复状态按每个动作的**最新状态**判定。
  - [ ] prune 永不移动以下 build：current、回滚目标、待恢复的、最近 N 个、有存活读者租约的；pid 已死的租约由 prune 清理；被 prune 的移入 `builds/_trash/`。
  - [ ] 登记失败时指针不回退，exit 21。
- **必需测试**：
  - `test_pointer_always_complete`（在任意崩溃点注入）— L2·S5
  - `test_stale_replay_exit22` — L2·S3
  - `test_prune_never_moves_protected`（hypothesis 生成日志和租约）— L2·S4
  - `test_lease_vs_prune_race`（真双进程）— L2·S5

#### WI-11a：MCP 包装 `bin/klode-kit mcp`
- **文件**：`src/klode_kit/mcp_wrap.py`。
- **依赖**：09d、10b。**lane**：cli_integration、unit。
- **验收标准**：
  - [ ] `initialize`、`tools/list`、`ping` 交给 klode 的 `handle`。**`handle()` 返回 None、直接写 stdout，包装不当它返回响应对象**。
  - [ ] `kb_version` 由包装自己回答；`tools/list` 也由包装自己扩展，不交给旧 dispatcher。
  - [ ] **同一个长驻进程**：发布 A → 查询（`kb_version` 加真实 search/verify）→ 发布 B → 再查询，两次的 `build_id` 和查询结果分别对应 A 和 B。
  - [ ] 缺 `current` 时返回 `isError` 且进程不退出。
  - [ ] 读者租约在切换或退出时清理。
  - [ ] 降级①②的路径都有测试（强制 import 失败的场景）。
- **必需测试**：
  - `test_same_process_A_to_B`（cli，真 klode）— L3·S5
  - `test_missing_current_iserror` — L3·S3
  - `test_kb_version_equals_readlink`（每次调用时都成立）— L3·S4
  - `test_degrade_subprocess_proxy` — L3·S3

#### WI-11b：登记器 + `u5_ready` 双探针【宿主探针 G2；真实项目 G3】
- **文件**：`src/klode_kit/register.py`、`src/klode_kit/registry_write.py`、`src/klode_kit/u5_probe.py`。
- **依赖**：11a、09d。**lane**：unit、cli_integration、model_integration。
- **验收标准**：
  - [ ] `.mcp.json` 读-改-写合并：只增加或更新 `<kb>-klode` 键，保留其他键；command 写包装的绝对路径，并带 `env.KLODE_KIT_BUILD` 标记。
  - [ ] `settings.local.json` 追加 `enabledMcpjsonServers`。
  - [ ] `registry_write` 按 id 幂等 upsert；`KLODE_REGISTRY` 可覆盖。
  - [ ] expert agent 用 `os.replace` 写入数据项目 `.claude/agents/`。
  - [ ] 直连探针：用 `.mcp.json` 里**同一份 argv 和 cwd**启动，`initialize → kb_version` 拿到 `build_id`。
  - [ ] 宿主探针：`--output-format stream-json --verbose`，按 tool_use id 取真实的 tool_result（R4-04）。
  - [ ] 任一探针的 `build_id` ≠ 目标：`u5_ready:false`，exit 21。
  - [ ] **测试只写 tmp 合成数据项目**。
- **必需测试**：
  - `test_mcp_json_merge_preserves_others`（hypothesis 生成已有键）— L2·S5
  - `test_registry_upsert_idempotent` — L2·S5
  - `test_direct_probe_build_id`（cli）— L3·S1
  - `test_host_probe_tool_result`（model）— L3·S1
  - `test_probe_mismatch_exit21` — L2·S3

### M3 · med-intake、workflow、插件打包、文档

#### WI-12：agents（card-writer、kb-verifier）+ 宿主发现 expert【G2】
- **文件**：`agents/klode-card-writer.md`、`agents/kb-verifier.md`。
- **依赖**：02d、00d1、11b。**lane**：unit（frontmatter 静态检查）、model_integration、host_acceptance。
- **验收标准**：
  - [ ] 两个 agent 的 frontmatter 合法，不依赖 `hooks/mcpServers/permissionMode`。
  - [ ] kb-verifier 只读，只输出 `advice.json`；用它运行后，记录的 status 字节不变。
  - [ ] 真实 writer 写一张卡，`check_cards` PASS。
  - [ ] **宿主能发现生成到数据项目的 expert**（R4-03）。
- **必需测试**：
  - `test_frontmatter_static` — L1·S2
  - `test_verifier_never_changes_status` — L2·S3
  - `test_host_discovers_expert`（host）— L3·S1

#### WI-13b：`/med-intake` 编排
- **文件**：`src/klode_kit/med_intake.py`。
- **依赖**：08d、09a、05b。**lane**：contract_replay。
- **验收标准**：
  - [ ] 登记单据类型（由抬头关键词确定性判定，或用 `--type` 指定）、日期和来源。
  - [ ] 依次串起 ocr → audit，停在 `awaiting_confirm`（exit 10）。
  - [ ] 以 inbox 目录日期作为批次别名：`work/20261022` → `work/<batch_id>`，`confirm/20261022.html`（风险 R-3）。
  - [ ] 终端打印 `ocr: 3 页 × 4 路 OK` 和 `audit: 5 条分歧 → confirm/20261022.html`。
  - [ ] 备份方案只写进文档。
- **必需测试**：`test_u1_stdout_and_receipt`（见映射表）— L2·S1；`test_alias_symlink_stable` — L2·S5。

#### WI-13a：五个 `SKILL.md` + 已知坑 + 两条铁律 +（宿主）`/klode-kit:kb-check`【G2】
- **文件**：`skills/{kb-ocr,kb-audit,kb-build,kb-check,med-intake}/SKILL.md`、`tests/unit/test_skills_static.py`、`tests/host/test_kb_check_entry.py`。
- **依赖**：09d、10a、13b、15a。**lane**：unit、host_acceptance。
- **验收标准**：
  - [ ] 正文一律调用 `${CLAUDE_PLUGIN_ROOT}/bin/klode-kit`，白名单写 `Bash(*/bin/klode-kit *)`，正文里没有业务逻辑。
  - [ ] 写进 A11 的 12 条已知坑和 A8 的两条铁律。
  - [ ] 宿主 receipt 的 `plugin.name/version/entry` 正确，`root_sha == release_sha`。
- **必需测试**：`test_skills_static` — L1·S2；`test_kb_check_entry`（host）— L3·S1。

#### WI-14：Workflow `kb-pipeline.js` 薄壳【G2】
- **文件**：`workflows/kb-pipeline.js`、`tests/unit/test_workflow_static.py`、`tests/host/test_workflow_two_stage.py`。
- **依赖**：09a、00d2。**lane**：unit、host_acceptance。
- **验收标准**：
  - [ ] 脚本里没有 `Date.now`、`Math.random`，也没有文件或进程 API。
  - [ ] 每个阶段一次 `agent(…, {schema})`；第一段在 audit 处以 exit 10 结束；`args.resume` 时依次走 writeback → build → check → publish。
  - [ ] 宿主两段式实跑通过；如果 headless 不可行，按 00d2 结论记为用户观察项。
- **必需测试**：`test_workflow_static` — L1·S3；`test_workflow_two_stage`（host）— L3·S1。

#### WI-15a：打包与安装
- **文件**：`.claude-plugin/plugin.json`、`pyproject.toml`（extras：`core`、`ocr-mac`、`mlx`、`dev`；钉死 `klode==0.5.0`）、`uv.lock`、`scripts/install.sh`、`bin/klode-kit`。
- **依赖**：00s、10b。**lane**：cli_integration。
- **验收标准**：
  - [ ] `install.sh` 建 `.venv`，`bin/klode-kit --version` 可执行。
  - [ ] **在仓库外的 cwd 里运行**也能工作。
  - [ ] 插件升级后可以重建环境。
  - [ ] import `klode.lib` 冒烟通过。
- **必需测试**：`test_install_outside_repo`（tmp HOME 加 tmp cwd）— L3·S1；`test_reinstall_idempotent` — L2·S5。
- **风险**：G4。

#### WI-15b：`release_manifest` + `release_check` + CI 收口
- **文件**：`src/klode_kit/devtools/{release_manifest,release_check}.py`、`RELEASE.sha256`。
- **依赖**：15a、全部 lane。**lane**：unit。
- **验收标准**：
  - [ ] 发布摘要**按实际发布清单**覆盖全部运行资源，包括 `templates/**`、`scripts/install.sh`、`config/**`、`prompts/**`、`schemas/**`、`hooks/**`、`workflows/**`、`agents/**`、`skills/**`、`bin/**`、`src/**`、`plugin.json`、`pyproject.toml`、`uv.lock`（R3-08 剩余项）。
  - [ ] 只改动一个模板文件，旧 receipt 也失效。
  - [ ] 五条 lane 的 receipt 齐全且 PASS；`release_sha` 一致；不在 dirty 状态；≤ 7 天；`privacy_scan` 0 命中；doctor 0 缺项；mutation 达标。
- **必需测试**：
  - `test_release_sha_changes_iff_file_changes`（hypothesis 生成文件树）— L1·S4
  - `test_template_only_change_invalidates` — L2·S3
  - `test_missing_lane_receipt_fails` — L1·S3

#### WI-30：文档
- **文件**（全部新建）：`docs/目录结构.md`（全局规则要求）、`docs/install.md`（环境前提：Workflow 需付费方案、信任前提、Bash 权限规则模板、短名 `/kb-check` 与 `/klode-kit:kb-check` 并存说明）、`docs/med-intake-backup.md`。
- **README**：README.md 已存在，按拍板 9「文档另存」，生成 `README_v2.md`；是否覆盖原 README 先问用户。
- **依赖**：13a。**lane**：unit（链接和路径存在性检查）。
- **验收标准**：
  - [ ] 文档里引用的每个命令和路径都实际存在。
  - [ ] 文档里没有私有项目编号或路径。
- **必需测试**：`test_doc_paths_exist` — L2·S2。

#### WI-31：U1–U8 用户感知层验收套件
- **文件**：`tests/acceptance/test_u{1..8}_*.py`（详见第 3 节）。在流水线真实产物上重跑 00b3 的 roundtrip。
- **依赖**：M1、M2 全部，以及 13b、14、12。**lane**：contract_replay、cli_integration、model_integration、host_acceptance【G2】。
- **验收标准**：
  - [ ] 第 3 节映射表里每一条都通过。
  - [ ] `test_provenance_roundtrip_on_pipeline_output` 0 违例。

#### WI-17（可选）：ask-arbiter
- **文件**：`.claude/ask-arbiter/context.md`（项目内写入，不触发闸门）。
- **依赖**：无。**lane**：无。
- **验收**：内容与 04 拍板一致。属于低优先级。

#### WI-19（可选）：回修用户 skill B1–B5【G1】
- **目标**：把插件副本里已经验证过的 B1–B5 修复（每个都有复现测试）移植到 `~/.claude/skills/klode-build/scripts/`。
- **依赖**：02a–02c。
- **验收标准**：
  - [ ] 先向用户出示 diff 和影响文件，先备份；**用户批准后才写**。
  - [ ] 修好后，03 基线里的复现命令全部翻转为预期结果。
- **测试**：用 03 基线 §5 的复现命令做手动验收（不进 CI）。

---

## 3. U1–U8 → 用户感知层验收测试映射

| U | 测试文件（lane） | 输入 | 可直接观察的断言 |
|---|---|---|---|
| U1 | `test_u1_med_intake.py`（contract_replay） | `bin/klode-kit med-intake --inbox <tmp>/_inbox/2026-10-22/`，`fx-u1-3labs` 3 张图 | stdout 含 `ocr: 3 页 × 4 路 OK` 和 `audit: 5 条分歧 → confirm/20261022.html`；exit 10；状态 `awaiting_confirm`；`work/20261022/ocr/receipt.json` 四路各有耗时、失败页 0 |
| U2 | `test_u2_kb_ocr_rotated.py`（contract_replay；cli 版带 `macos`） | `kb-ocr --batch _inbox/clinic-0918/ --routes vision,mlx,claude,codex`，`fx-rotated90` | receipt 每页 `rotate: 90`；`_masked/` 有打码图，抬头区域像素全黑；对照图在 `_mask_review/`（R-2）；`ocr/vision/<fp>/p001.jsonl` 每行含 4 整数的 `bbox_px` |
| U3 | `test_u3_pipeline_first_stage.py`（contract_replay）+ `test_u3_workflows_host.py`（host，G2） | pipeline 两段式第一段，`fx-u3-mixed` | `records/2026-10-22-lab.md` 存在，`status: pending_confirm`、`uncertain: []`（只有多数票分歧）；无分歧页 `verified`；host 版：`/workflows` 出现 `kb-pipeline`（headless 不可行时由用户观察） |
| U4 | `test_u4_guide_pdf.py`（cli_integration） | `kb-ocr guide-2026.pdf`，然后 `kb-build --kb kb/guide-klode` | receipt 写 `text_layer: true, routes_skipped:[vision,mlx,claude,codex]`；`build_receipt.json` 13 步全 PASS；`klode -c … check --strict` 输出 `OK: 0 errors, 0 warning(s)` |
| U5 | `test_u5_verify_cli.py`（cli）+ `test_u5_expert_qa.py`（model，G2） | 医疗 fixture 库；问 `med-klode-expert`「示例片最近三次的剂量」 | cli：`klode verify <card> "示例片 10mg 2026-09-18"` 返回 VERIFIED；MCP 包装调 verify 结果一致；model：回答逐条带 `(grep: 示例片 10mg 2026-09-18)`，逐条 verify 都 VERIFIED；问库里没有的内容，回答「查不到」 |
| U6 | `tests/replay/test_u6_redo_keeps_verified.py`（contract_replay） | `fx-redo`：redo 后跑 audit | 新目录 `ocr/<engine>/<新fp>/` 出现，旧目录树 sha 不变；`fixes.jsonl` 原前缀字节不变；`verified/*.md` 的链接目标和 sha 不变，点选完成后才更新 |
| U7 | `test_u7_book_build.py`（cli，writer 用回放） | `kb-build --kb kb/demo-klode --source aurora-kettle.txt --desk src/desk.json` | `build_receipt`：`3 片拼回 == 原文`、`check_cards` 3/3 PASS、strict 0E、desk verify 5/5；`cards_content/` 下 3 张卡；`.mcp.json` 新增 `demo-klode` 条目；`smoke_mcp` 6 项 PASS |
| U8 | `test_u8_confirm_writeback.py`（contract_replay + cli `needs_node`） | 打开 `confirm/20261022.html`，模拟点选（含「都不对」）后导出 `choices.json`；`kb-audit --writeback choices.json` | 每条分歧都有小图、四路候选、★推荐值；写回后 `verified/2026-10-22-lab.md` 为 `status: verified`；选「都不对」又没手输的记录 `status: disputed`，`uncertain` 含 `p002-l07`，且不在 `build_receipt` 的输入清单里 |

---

## 4. 06 第 4 轮「实施时注意」落点

| 项 | 落点 WI | 对应测试 |
|---|---|---|
| R4-01 首次摄入视图 | 08d、00b1 | `test_first_ingest_pending_view_r4_01`、U3、U8 |
| R4-02 启动上下文隔离 | 00c2 | `test_startup_context_nonce_absent`；通过前不开放真实数据路径 |
| R4-03 版本注释位置 + 宿主发现 | 02d、12 | `test_first_line_frontmatter`、`test_host_discovers_expert` |
| R4-04 stream-json 按 tool_use id 取结果 / `permission_denials` | 11b、00c2、00d2 | `test_host_probe_tool_result`、`test_classify_denied_enoent_unattempted` |
| R4-05 canary 绝对路径，区分拒绝 / ENOENT / 未尝试 | 00a、00c2 | `test_canary_path_preexists`、分类测试 |
| R3-08 剩余：按实际发布清单摘要 | 15b | `test_template_only_change_invalidates` |
| MCP 同一进程 A→B、缺 current、租约/prune 竞态 | 11a、09d | `test_same_process_A_to_B`、`test_lease_vs_prune_race` |
| 新增工具由包装自己处理；`handle()` 返回 None 写 stdout | 11a | `test_same_process_A_to_B`（tools/list 含 `kb_version`） |
| 隔离 receipt 绑定 CLI 版本、有效配置、argv | 00c1 | `test_receipt_invalid_on_cli_version_change` |
| `klode verify <card> <phrase>` 形式 | 01a、31（U5）、02d | `test_u5_after_manual_fix`、`test_u5_verify_cli` |

---

## 5. 执行顺序（拓扑序）

- **M0**：00s → 00a → 00b1 → 00b2 → 00b3 → 00c1 → 00c2【G2】 → 00c3【G2】 → 00d1 → 00d2【G2、G5】 → 00e
- **M1**（入场条件：00c2、00c3、00d2 的 exit criteria 全部满足）：04a → 04b → 04c → 05a → 05b → 06a → 06b → 07a → 07b → 07c → 07d → 08a → 08b → 08c → 08d
- **M2**：01a → 01b → 01c → 02a → 02b → 02c → 02d → 03a → 03b → 10b → 09a → 09b → 09c → 10a → 09d → 11a → 11b
- **M3**：12 → 13b → 15a → 13a → 14 → 15b → 30 → 31 → 17（可选）→ 19【G1，可选】

注：01b 依赖 02a，按依赖关系实际执行顺序为 02a 先于 01b。

---

## Risks & Assumptions

- **R-1 klode 0.5.0 能否公开安装决定 cli lane 能否进 CI。** 装不上时，cli receipt 只能本地产出，`release_check` 强制要求它存在。
- **R-2 U2 字面与设计不一致。** 04 U2 写「`_masked/` 下出现打码图与打码对照图」；r2.2-B 按评审意见把对照图移到 `_mask_review/`，防止原图被当作可外发。04 已锁定，本计划**按设计断言 `_mask_review/`**，需要用户确认这只是澄清而不是「推翻04」。
- **R-3 批次 ID 与 04 观察路径。** 设计里 `batch_id` 是 hash，而 U1 写的是 `work/20261022/`、`confirm/20261022.html`。计划用日期别名符号链接保住这些可观察路径（WI-13b）。同理，run 目录命名为 `<cfg_fp>.t<seq>`，让 U2/U6 的 `ocr/<engine>/<fp>/p001.jsonl` 字面成立（WI-04b）。这两处属于实现层兼容，不改 04；如有异议请指出。
- **R-4 spike 失败的回退。** 00c2 某路隔离 FAIL → 该路停用，按 04 记录 disputed，四路目标实际无法达成，需要升级给用户（可能变成「推翻04」议题）。00c3 错误自动通过率 > 0 → 停止并重新规划对齐和票型。00d2 headless 跑不了 Workflow → U3 的宿主部分改为用户观察。
- **R-5 R4-02 可能拿不到确定性证据。** 宿主不一定提供 CLAUDE.md、memory 加载的事件证据。拿不到时记为残余风险，真实数据路径保持关闭，由用户裁决。user memory 不能直接写 `~/.claude`（G1），只能使用 cc-doc-search 查实有文档的配置目录覆盖机制。
- **R-6 字体与 fixtures 跨平台。** 生成的二进制成品直接提交，只在 macOS 上做再生成校验；CJK 字体授权需要确认。
- **R-7 node 依赖。** 确认页导出 JS 的测试需要 node，属于 cli lane 的依赖，缺失即 FAIL。
- **R-8 klode 内部 API 不稳定（H18）。** 钉死 0.5.0；doctor 做 import 冒烟；降级路径①②都有测试。
- **R-9 Claude Code 机制断言。** 按你的全局规则，实现者断言任何 CLI 参数或宿主行为（`--restricted`、`--permission-prompts`、`--setting-sources ""`、stream-json 字段、插件组件名单 ①）之前，必须先查 cc-doc-search 并附出处，不能凭记忆。
- **R-10 费用。** model 和 host lane 都设 `--max-budget-usd`；探针费用单独记账。
- **R-11 写 `$HOME` canary。** 00c2 会在 `$HOME` 下创建再删除自己生成的 nonce 文件。这只删本测试刚建的文件，不违反破坏性操作禁令，但实现时要在报告里写明。
- **R-12 `bin/klode-kit` 的形态。** 02 写的是由 `install.sh` 生成，但 R3-08 把 `bin/**` 列入发布摘要。计划改为启动器内容固定、提交入库，`install.sh` 负责校验。
- **R-13 README 覆盖。** 按拍板 9，文档更新另存 `_v2`；是否覆盖原 README 需要问用户。
- **R-14 U8 `uncertain: [p002-l07]` 与结构化条目。** 设计里 uncertain 是带 `reason`/`scope` 的对象列表，04 U8 写的是字符串 id 列表。验收测试断言条目的 `id` 为 `p002-l07`；如需 front-matter 字面渲染成 id 列表，在 views 层输出。
- **A-1 假设**：Python ≥ 3.12、macOS arm64 用于 A/B 两路；Linux CI 只跑 unit 和 contract_replay。
- **A-2 假设**：真实四路实测只用合成图；30 份报告的试点在阶段 6 的私有项目里执行。

## Deferred / Out of Scope

- klode gate rubric 和 `klode review`（拍板 5：本期不做）。
- MLX 权重自动下载（只检测并给命令）；用 CLI 探测 Workflow 是否启用（只写进 README）。
- 截图拼接 `stitch_screenshots.py`（⑧ / N12，需用户亲自指定）。
- `kb_status`、`kb_map`、user 级 `register.py`（A9、G13，留在用户 skill）。
- 备份实现（只写文档）。
- Agent SDK 流式附图路径（⑥，只作备选）。
- 五个组件目录以外的插件组件（①）。
- 改 klode 上游（B6 由 G4 判据和门禁 1 兜住）。
- 每次建库都加人工停点（「推翻04」，不做）。