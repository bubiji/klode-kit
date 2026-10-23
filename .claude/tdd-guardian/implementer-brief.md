# 实现者通用派工说明（阶段 5，每个 WI 都适用）

项目根：`/Users/deanlee/Documents/Claude/Projects/klode-kit`（公开仓库，Python 3.12，`.venv` 已就绪）。

## 先读

1. 计划 `.claude/tdd-guardian/plan-20261022-klode-kit.md`：§0 全局约定与本 WI 条目
2. 矩阵 `.claude/tdd-guardian/test-matrix-20261022-klode-kit_v8.md`：本 WI 的全部 `<WI>-*` 行与 §0.3
3. 设计 `docs/pipeline/20261022-klode-kit/02-概要设计.md`（正文 + r2–r5，后段覆盖前段）与 `04-需求摘要与usercase.md`（U1–U8 验收基准）
4. **偏离记录 `.claude/tdd-guardian/impl-deviations.md`**：之前各 WI 定下的接口、自定规则、用户批准的改动，以本文件为准
5. venv 附录 `.claude/tdd-guardian/plan-20261022-klode-kit_addendum-venv.md`（V1–V7）

## 流程（严格 TDD）

- 先写测试跑红 → `node ~/.claude/plugins/cache/local-src/tdd-guardian/0.10.2/scripts/tdd-guardian/receipt.js record --id <WI>.<lane> --lane <lane> --files <测试文件>` → 实现 → 跑绿 → `receipt.js verify --id <WI>.<lane>`。
- 为避免 red 被判成 runner-missing，可先放只抛 `NotImplementedError` 的接口桩。
- **red 记录之后不改任何已记录的测试行**；发现测试本身有缺陷就停下，在回报中说明（主会话会问用户）。
- lane 命令一律 `.venv/bin/python -m pytest -m <lane> …`（不调用 console script）；cli_integration 需设 `WFVIZ_SRC=~/Documents/Claude/Projects/001-agent-rational-unified-process-P1/src`。
- 属于计划 §0.3 criticalPaths 的新文件，完成后在 `.claude/tdd-guardian/config.json` 补条目。
- 涉及 Claude Code 机制的断言：先查本地文档镜像 `~/Documents/Claude/Projects/25_d_cc-docs-bilingual_html-zh-mirror_P2/claudecodedoc/docs/`（或 cc-doc-search MCP），附官网 URL；查不到如实写。

## 红线

- 禁止 `rm` / `rm -rf`：清理一律移入仓库根 `_trash/`。
- 不 git commit / push（主会话负责）；不改 `docs/pipeline/`；不原地改 `README.md`。
- 不写 `~/.claude`、`~/.claude.json`、`~/.codex`；不安装插件到 user 作用域。
- 不读 `~/Documents/Claude/Projects/002_d_family-medical_scan-kb_P2`（私有项目）。
- 仓库公开：任何入库文件不得出现真实个人 / 医疗信息、本机用户名或绝对家目录路径；完成前跑 `bin/klode-kit dev privacy-scan`（0 命中）与 `.venv/bin/python -m klode_kit.devtools.fixture_manifest verify`。
- 真实云端调用（闸门 G2）只在派工明确授权时进行，只用 MANIFEST 登记的合成图，`KLODE_KIT_REAL=1 KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`，遵守派工给出的费用上限。

## 完工前必须

- **完整**跑 unit 与 contract_replay 两条 lane（不是只跑改动文件），确认 `0 failed`；hypothesis 用例连跑 3 次无失败。改动涉及 cli 用例时也完整跑 cli_integration。

## 回报格式

文件清单；red / verify 原文；各 lane 实跑结果；本 WI 矩阵行对应表；自定的设计细节；偏离（改了已记录断言的标 SEPARATION 问题）。
