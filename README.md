# klode-kit

> A Claude Code plugin that turns scanned documents (and books) into a citable [klode](https://github.com/xiaolai/klode) knowledge base: multi-engine OCR, cross-engine field audit with a local confirmation page, shelf/desk build, strict citation gate. Medical records are the first scenario; the framework is generic.

## 1. 这个项目是什么

**一个通用的 klode 建库工作流**：把「图片、PDF、书」变成可以逐字引用原文的 klode 知识库。库里的每一句原文由 klode 逐字抽取、由脚本逐字切分，**模型不碰原文一个字**；模型只负责写卡片（说明加指向原句的锚点），锚点由两道机器门禁核验。回答问题时每个数字都带逐字锚点，查不到就说查不到，不编造。

通用层与医疗层是拆开的：

- **通用层**：OCR、对齐审计、书架（原文）与书桌（按问题路由的综合）、建库、检查、发布、MCP。书、指南 PDF 同样适用。
- **医疗层（首个场景）**：化验单、处方截图入库（`/klode-kit:med-intake`）；记录要经人在本地确认页点选才算 verified；脱敏导出；云端读图只收到抬头打码后的图。

**插件不带数据。** 本仓库是公开的，样本全部是合成的；真实单据只存在使用者自己的**数据项目**里，插件在数据项目的当前目录（或 `--project DIR`）里读写。

主要特性：

- **四路读图互相比对**：本机 Vision、本机 MLX、云端 Claude、云端 Codex。云端两路只发打码后的图。
- **分歧只呈现、不自动改字**：数值只要有一路不同就进确认页，多数票只是推荐值。
- **可追溯**：任何一个数字都能回到原图的那一行，错了能查出是哪一路读错、什么时候谁改的。
- **构建不可变**：每次建库是一个只读的构建目录，检查通过才能发布，发布是一次原子切换，能回滚。
- **有测试**：先写测试再实现（TDD），四个测试 lane，覆盖率闸门，发布前变异测试。

## 2. 四件套

按需求，最终产出是 skill、agent、plugin、workflow 四类件，都在本仓库根（仓库根就是插件根）：

| 件 | 位置 | 内容 |
|---|---|---|
| plugin | `.claude-plugin/` | 清单，命令前缀 `/klode-kit:`。 |
| skill | `skills/` | `kb-ocr`、`kb-audit`、`kb-build`、`kb-check`、`med-intake`。只调命令、读回执、汇报，附「已知坑」表。 |
| agent | `agents/` | `klode-card-writer`（便宜模型一卡一派写卡）、`kb-verifier`（只读，只出建议）。每个库的专家 agent `<库名>-klode-expert` 由建库生成到数据项目。 |
| workflow | `workflows/` | `kb-pipeline.js`：两段式流水线薄壳（待确认处停下，点选后续跑）。 |

另有：`hooks/`（外发命令的第二道闸）、`bin/klode-kit`（启动器）、`src/klode_kit/`（全部实现）。每个目录的用途见 [docs/目录结构.md](docs/目录结构.md)。

## 3. 快速开始

前提：macOS、`uv`、Python 3.12。完整说明和 Claude Code 侧的前提（Workflow、工作区信任、权限规则）见 [docs/install.md](docs/install.md)。

```sh
git clone <本仓库地址> klode-kit
cd klode-kit
scripts/install.sh
bin/klode-kit doctor
```

然后在**数据项目**目录里（先在那里交互运行一次 `claude` 接受工作区信任）加载插件：

```sh
claude --plugin-dir <klode-kit 目录>
```

第一批：把截图放进 `_inbox/2026-10-22/`，对 Claude 说「今天的截图入库」，即 `/klode-kit:med-intake --inbox _inbox/2026-10-22/`。它登记类型、日期和来源，跑四路 OCR 和审计，停在待确认，打印确认页位置。用浏览器打开 `confirm/20261022/confirm.html`，逐条点选，导出 `choices.json`，再说「分歧我点完了，写回」（`/klode-kit:kb-audit --writeback choices.json`）。

没有 Workflow 也完全可用；命令行直接跑：

```sh
bin/klode-kit med-intake --inbox _inbox/2026-10-22/ --project <数据项目目录>
bin/klode-kit pipeline --batch _inbox/2026-10-22/ --choices choices.json --stage writeback --project <数据项目目录>
```

数据项目的备份见 [docs/med-intake-backup.md](docs/med-intake-backup.md)，`med-intake` 的行为细节见 [docs/med-intake.md](docs/med-intake.md)。

## 4. 典型用法（U1–U8）

下表的触发说法是对 Claude 说的话，命令是它调用的。「看到什么」是可以直接检查的输出。

| 编号 | 场景 | 说或运行 | 看到什么 |
|---|---|---|---|
| U1 | 住院期，当天截图入库 | 「今天的截图入库」→ `/klode-kit:med-intake --inbox _inbox/2026-10-22/` | 终端打印 `ocr: 3 页 × 4 路 OK` 与 `audit: 5 条分歧 → confirm/20261022.html`，状态 `awaiting_confirm`。 |
| U2 | 门诊纸质单手机拍照（歪斜） | 「这几张拍的单子先 OCR」→ `/klode-kit:kb-ocr --batch _inbox/clinic-0918/` | 批次回执里每页有转正角度；`_masked/` 有打码图，`_mask_review/` 有对照图；每行带像素框。 |
| U3 | 家人远程放图，用户一键跑 | 「跑一遍流水线」→ `/klode-kit:kb-pipeline` | `/workflows` 里出现运行中的 `kb-pipeline`；到待确认处停下；点选后带 `resume` 续跑。 |
| U4 | 指南 PDF 入库 | 「这份指南 PDF 建个库」→ `/klode-kit:kb-ocr` 再 `/klode-kit:kb-build` | 有文字层的 PDF 不走 OCR；构建回执 13 步全 PASS；klode strict 0 错 0 警告。 |
| U5 | 查询问答 | 对 `<库名>-klode-expert` 提问 | 回答逐条带逐字锚点，`klode verify` 能核对；库里没有的回「查不到」。 |
| U6 | 历史资料重做 OCR | 「2025 年那批重新 OCR 一遍」→ `/klode-kit:kb-ocr --batch archive/2025-q3/` 再 `/klode-kit:kb-audit` | 新指纹目录，旧目录不动；旧的 verified 记录保持有效，直到新确认页点选完成。 |
| U7 | 用书走建库 | 「这本书走 kb-build」→ `/klode-kit:kb-build --kb kb/demo-klode` | 构建回执：切章自校验、卡片门禁、strict、书桌留出集检查；`.mcp.json` 新增该库条目（发布后）。 |
| U8 | 确认页点选与整库检查 | 「分歧我点完了，写回」→ `/klode-kit:kb-audit --writeback choices.json`；「检查一下库过不过」→ `/klode-kit:kb-check --kb kb/demo-klode` | 每条分歧 = 小图 + 各路候选值 + 推荐值；写回后记录为 verified；留「都不对」未手输的记录是 disputed，不进构建输入；检查回执逐项 PASS / FAIL，FAIL 行指明回路。 |

读错的字段即使已 verified，也可以重开：`/klode-kit:kb-audit --reopen <字段号>`，把它放回一字段确认页，新修订生效，旧修订保留。

命令短名（`/kb-check` 与 `/klode-kit:kb-check` 并存）见 [docs/install.md](docs/install.md)。

## 5. 闸门

有几类动作不会自动发生，需要你明确同意。

**G2：真实云端调用。** 把图发给云端模型读，或真实写卡、真实宿主验收，都是真实调用，默认关闭。要打开，同时设置两个环境变量：

```sh
KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd> bin/klode-kit kb-ocr --batch _inbox/2026-10-22/ --routes claude
```

没有设置或日期格式不对，发送器和桥都会拒绝并以退出码 61 退出，零次调用。另有几道独立的保护，缺一不可：

- 云端路只发 `_masked/` 里登记过的打码图，发送前重算校验和，发送器和 `hooks/` 里的钩子各查一遍。
- 费用有预算账本，按批限额，超限就停。
- 本仓库的测试只用合成图（`tests/fixtures/MANIFEST.json` 里登记过的）。真实数据只在你的私有数据项目里跑。

**发布需要你同意。** `kb-publish` 和 `register` 会写数据项目的 `.mcp.json`、`.claude/settings.local.json`，以及用户全局的 registry（`~/.klode/registry.toml`）。这些属于手动闸门：

- Workflow 默认**止于 check**，不发布；只有参数 `publish` 严格等于 `true` 才追加发布。skill 调流水线时也都带了明确的 `--stage`。
- **注意命令行**：`bin/klode-kit pipeline` 不带 `--stage` 时跑**全部**阶段，含 `publish`。手动跑第二段请写明 `--stage check`，只有你决定发布时才去掉或写 `--stage publish`。
- 测试一律不碰真实路径（registry 在测试里被指向临时文件）。
- 发布前先过 `/klode-kit:kb-check`；`check_receipt` 不是 PASS 的构建，发布会被拒绝。

**安装依赖与权重。** 本机 MLX 模型权重很大，不会自动下载；`doctor` 只报缺失，不下载。

## 6. 开发与测试

先写测试再实现。测试按 lane 分，标记定义在 `pyproject.toml`，目录见 [docs/目录结构.md](docs/目录结构.md)。

```sh
scripts/install.sh --dev
.venv/bin/python -m pytest -m unit --disable-socket
.venv/bin/python -m pytest -m contract_replay --disable-socket
```

| lane（pytest 标记） | 内容 | 何时跑 |
|---|---|---|
| `unit` | 纯函数、schema、静态检查；无真实模型调用 | 每次提交；CI |
| `contract_replay` | 契约、来源链往返、用回放录制重放的链路；无真实模型调用 | 每次提交；CI |
| `cli_integration` | 真实进程：klode / klode-mcp、pdftotext、本机 OCR 引擎、node；缺依赖就失败，不跳过 | 提交前本机；不在 CI |
| `model_integration` | 真实模型调用，只用合成图 | 手动，需要 G2 |
| `host_acceptance` | `claude -p --plugin-dir .` 宿主验收 | 手动，需要 G2 |

`cli_integration` 里渲染工作流图的用例需要 `WFVIZ_SRC` 指向 wfviz 工具的 `src` 目录：

```sh
WFVIZ_SRC=<wfviz-src> .venv/bin/python -m pytest -m cli_integration
```

一条 lane 带回执地跑（回执写到 `work/lanes/`）：

```sh
bin/klode-kit dev lane unit
```

CI（`.github/workflows/ci.yml`）在 macOS 上跑隐私扫描、`unit`、`contract_replay` 和合并覆盖率闸门；它的回执标记为非发布用。

提交前：

```sh
bin/klode-kit dev privacy-scan
```

扫描发现个人信息（身份证号、手机号、姓名标签）、未登记的图片即失败，仓库是公开的，必须 0 命中。

## 7. 发布检查与变异测试

发布前的总闸是 `release-check`，它一次列出**全部**未满足项（不是遇到第一个就停）：

```sh
bin/klode-kit dev release-manifest --check
bin/klode-kit dev release-check
```

检查项：五个 lane 的回执（必须是本机、绑定当前代码树的非 CI 回执）、`RELEASE.sha256` 是否最新、工作树是否干净、隐私扫描、`doctor`、安装指纹、变异测试得分。退出码 0 通过，20 有未满足项。

**变异测试**是发布前的手动闸门，不进每次提交。它对关键路径文件（`.claude/tdd-guardian/config.json` 里的 `criticalPaths`）生成变异体，看现有测试能不能杀死它们，阈值 0.80：

```sh
bin/klode-kit dev mutation-gate --jobs 6
bin/klode-kit dev mutation-gate --jobs 6 --resume
```

整套跑完要很多小时（单个文件几十分钟到几小时），可以用 `--resume` 续跑；只有**每个**关键文件都跑完、且代码没变，才会写出 `work/mutation.json`，`release-check` 读它（最长 7 天内有效，并绑定当前代码树）。`--file` 只跑单个文件，结果永远不算发布依据。运行期间不要修改被测文件和测试。

## 8. 当前状态

写成本文时：实现与单元、契约测试已覆盖到发布检查和变异测试工具；整套 U1–U8 验收、第一次真实云端试跑（G2）、在真实数据项目上的发布（手动闸门）、完整变异测试得分都还没有完成，所以 `release-check` 现在不会全绿。

## 9. 许可证

MIT，见 [`LICENSE`](LICENSE)（`pyproject.toml` 同声明）。本项目依赖的 klode 同为 MIT（公开 PyPI，固定为 0.5.0）。
