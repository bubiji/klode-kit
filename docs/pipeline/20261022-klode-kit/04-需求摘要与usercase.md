# 阶段 2 · 需求摘要与 user case（2026-10-22 **用户已确认——确认版，后续阶段的验收基准**）

> 这一页是整条流水线的锚：评审、TDD 计划、实现、验收都对照它。确认后标题改为「用户已确认——确认版」，之后不再改；要改就回到阶段 2 重新确认。
> 依据：`docs/交接_2026-10-22.md`（交接§N）、`01-输入清单.md`（01§N，原话逐字出自此）、`02-概要设计.md`（02§N）、两份 03 基线（实测 A/G/B 编号、OCR E/N 编号）。未读私有项目 002。示例数据全部合成（假名「测试患者甲」、假编号 `T-0000001`、假药「示例片」）。

## 1. 你的原话需求（逐条引用）

| # | 原话（逐字，出自 01§1） | 指向 |
|---|---|---|
| 1 | 「1 我现在已经有/kb-build 我基本实现 你也可以根据测试看缺什么 改什么 原来做的好也请评审采纳」（2026-10-22 `/goal`） | 现有 klode-build / klode-kb 两个 skill 的实测评审 → 03 实测 A1–A11 采纳、G1–G14 缺口、B1–B6 bug |
| 2 | 「2 按流水线做 klode-kit  docs/概要计划_v2.md」（同上） | 本流水线；02 为正本、`docs/概要计划_v2.md` 做指针（01§3-8） |
| 3 | 「抽象出共同框架，先落地医疗库；最终产出 **skill、agent、plugin、workflow 四件套**，带测试用例，用 TDD 实现；先给用户确认概要计划。」（2026-10-12，交接§1 整理版） | 分层（02§2.1）、四件套、TDD（02§7）、本页即「确认概要计划」 |
| 4 | 「用 wua skill 画常用场景、用户用例与工作流，用户确认后再出 TDD 详细计划。」（同上） | 三张 WUA 图通用版（WI-20）；本页 §6 user case |
| 5 | 「OCR 双模型审计抽象出来；图片还要发给 **Claude Opus 最高档**和 **Codex 最高档**读图，**四路互相比对**。」（同上） | `/kb-ocr` 四路 + `/kb-audit` 比对（02§2.3、§5 不降级） |
| 6 | 「确认方式：本地 HTML 页逐条点选。备份：数据项目本地 git + 外置盘裸仓。」（同上） | 确认页（N8）+ 写回；备份只进 `/med-intake` 文档（02§9-4） |
| 7 | 「`/kb-build` 必须包含**书架和书桌**机制，并且**全部自动化**。」（同上） | `pipeline.py` 编排 + 三回路（G1/G2/G12）；唯一人工步骤是点选 |
| 8 | 「我在这个项目执行这个  帮我根据 klode-kit/docs/交接_2026-10-22.md 创建必要条件 我授权一定要在这个执行这个测试」（本会话） | ask-arbiter 影子模式已登记（commit 41b95ce），WI-22 |
| 9 | AskUserQuestion 已定：合成样本目录 `tests/fixtures/`（01§2，非逐字） | WI-18 fixtures 生成器 |

## 2. 补充需求（我推断的，标依据）

- 分歧只呈现、不自动改字；多数值只是推荐值：依据 交接§2「原则」、E12/E14。
- `uncertain` 非空的记录不得标 verified，且不带进 build：依据 交接§2 审计第 9 步、N9、E21（缺页不许静默）。
- 确认必须在导出 txt 之前：依据 交接§5「klode 无法发现表格数值互换」、E03/E04、E45。
- 外发云端的图只能是 `_masked/` 或 `tests/fixtures/`，脚本 + 可选 hook 双检：依据 交接§6 红线、OCR§4 003「正文永不出网」。
- 隐私库 MCP 只登记到数据项目 `.mcp.json`（project 级），插件不登记具体库：依据 实测 G13、A5「不登记也能跑」。
- 门禁 1（`check_cards`）前置为硬门，`/kb-check` 加全库判据（zoom 全 full、aliases≥10、锚点≥N、留出集≥4/5）：依据 实测 B2、B6、G4、G5。
- 公开仓自包含、去硬编码（`which klode`、环境变量、`klode --registry`）、有测试：依据 交接§6、实测 G9/G10。
- 中文章头与单据标题（「第 N 章」「一、」「检验报告」）可切章：依据 实测 G6、步骤 3b 探针 0 命中。
- Workflow 两段式（生成确认页即结束 → 点选后 `resume`）：依据 02§4 待核实 ③（文档无阻塞等人工原语）。
- 数值字段禁止 NFKC 之外的归一（`10.5` ↔ `1.05` 必须算分歧）：依据 OCR§2.1 耦合表。

## 3. 背后的实际需求

- 一张单据从拍下到能被引用，中间只在一个页面点几下；其余全部机器跑完并留 receipt。
- 任何一个数字被引用时都能回到原图那一行；错了能查出是哪一路读错、什么时候谁改的。
- 真实数据永远不进公开仓、不以整图形式出网；他人装插件无需本机私有路径。
- 现有跑通的书架 / 书桌链路不重造，只修已知 bug 并补测试；书籍场景与医疗场景各自演进互不拖累。
- 以后换引擎 / 换模型，验收口径固定（分歧率 + 合成金标 CER），不靠感觉。

## 4. 会改变方案的最佳实践

- 原始 OCR 永不改、改动只进追加式补丁再重放（E12、`apply_fixes` 规则）→ 写回不是覆盖 JSONL，而是 `fixes.jsonl` + 重放出 verified 记录。
- 第三票按「短替换块」选页、系统性同类错只有多路才查得出（E14/E34）→ 白名单要区分「引擎弱点（忽略）」与「系统性错（必须呈现）」，而非一刀切过滤。
- 分歧小图按行 ±1 裁而非按句（E23）→ 坐标先统一成像素 bbox，无 bbox 的云端两路落到同一行。
- 插件 subagent 的 `mcpServers` frontmatter 被忽略（docs:sub-agents）→ expert agent 必须生成到数据项目 `.claude/agents/`，不进插件。
- `klode check --strict` 放过 stub 卡与折叠标点（实测 13b）→ 验收不能只看 0/0，必须加全库判据。
- 金标必须混入最难版式（E05/E16）→ fixtures 含表格化验单、盖章处方、低分辨率截图、竖排混排。

## 5. 功能清单（本次交付）

| 编号 | 功能 | 对应（原话 # / 02 / 实际需求） |
|---|---|---|
| F1 | `/kb-ocr`：形态判定 → 转正 → 可选打码 → 四路读图 → 统一 JSONL + receipt | 原话#5；02§2.3；N1/N6/N7；WI-03–06 |
| F2 | 抬头打码 `mask.py` + 外发来源双检（脚本 + hook） | 原话#5、#6 后半；交接§6；N5；WI-08 |
| F3 | `/kb-audit`：五类字段抽取 → N 路票型比对（白名单）→ 裁分歧小图 | 原话#5；N2/N3/N4；WI-07 |
| F4 | 确认页 HTML 点选 → `choices.json` → 写回补丁 → verified 记录；`uncertain` 门禁 | 原话#6；N8/N9；WI-09/10 |
| F5 | `/kb-build`：`pipeline.py` 一条命令串书架 + 书桌 + expert + MCP，receipt + 三回路 + 批派发 | 原话#7、#1；G1/G2/G12；WI-12 |
| F6 | vendoring 11 脚本 + 2 prompt，修 B1–B5，去硬编码，`init_kb.py` | 原话#1「原来做的好也请评审采纳」；A1–A7、G10/G11；WI-01/02 |
| F7 | `/kb-check`：strict + 全库判据 + smoke + 留出集 ≥4/5，exit 0/1 并指明回路 | 原话#7；G4/G5；WI-13 |
| F8 | `/med-intake`：单据登记 → F1–F5 串联；`records_to_txt` / `records_to_l1` / `deident` | 原话#3「先落地医疗库」；G7/G8；WI-11 |
| F9 | agents：`klode-card-writer`、`kb-verifier`；expert 由 `gen_agent.py` 生成到数据项目 | 原话#3 四件套；A6/A10；WI-15 |
| F10 | Workflow `kb-pipeline.js` 两段式（ocr → audit 停 → build → check） | 原话#3 四件套；交接§3-2；WI-17 |
| F11 | 合成 fixtures 生成器 + `privacy_scan.py` + 四 lane 测试 + CI | 原话#3「带测试用例、TDD」、#9；交接§6；WI-18 |
| F12 | 项目级 `.mcp.json` 登记 + `smoke_mcp` 冒烟 | 原话#7「MCP 登记」；G13；WI-16 |
| F13 | 三张 WUA 图通用版（`docs/wua/`） | 原话#4；交接§3-5；WI-20 |
| F14 | 五个 `SKILL.md` + 已知坑 A11 + 两条铁律 A8 文字化 | 原话#3「skill」；WI-14 |

## 6. 使用方式（user case）

| # | 谁 | 场景（交接§4-2） | 输入（命令 / 提示词 / 操作） | 看到什么（可观察） |
|---|---|---|---|---|
| U1 | 家属（住院期） | 住院期截图当天入库 | 把 3 张化验单截图放进 `_inbox/2026-10-22/`，说「今天的截图入库」→ `/med-intake --inbox _inbox/2026-10-22/` | 终端打印 `ocr: 3 页 × 4 路 OK` 与 `audit: 5 条分歧 → confirm/20261022.html`，状态 `awaiting_confirm`；`work/20261022/ocr/receipt.json` 四路各有耗时与 0 失败页 |
| U2 | 家属（门诊） | 门诊纸质单手机拍照（歪斜） | `/kb-ocr --batch _inbox/clinic-0918/ --routes vision,mlx,claude,codex` | `receipt.json` 每页有 `rotate: 90`；`_masked/` 下出现打码图与打码对照图，抬头「测试患者甲 / T-0000001」区域涂黑；`ocr/vision/<fp>/p001.jsonl` 每行含 `bbox` 像素框 |
| U3 | 异地家人 + 用户 | 家人远程放 `_inbox` | 家人把图放进同步目录；用户说「跑一遍流水线」→ `/klode-kit:kb-pipeline` | `/workflows` 里出现运行中的 `kb-pipeline`；结束时 `records/2026-10-22-lab.md` 新增，front-matter `status: pending_confirm`，`uncertain: []`；无分歧页直接 `verified` |
| U4 | 用户 | 指南 PDF 入库 | `/kb-ocr guide-2026.pdf` 再 `/kb-build --kb kb/guide-klode` | `receipt.json` 写 `text_layer: true, routes_skipped: [vision,mlx,claude,codex]`；`build_receipt.json` 13 步全 PASS；`klode -c … check --strict` 输出 `OK: 0 errors, 0 warning(s)` |
| U5 | 用户 | 查询问答 | 对 `med-klode-expert` 说「示例片最近三次的剂量」 | 回答逐条带 `(grep: 示例片 10mg 2026-09-18)` 逐字锚点，`klode verify` 回 `VERIFIED`；库里没有的问题回「查不到」而不是编一个值 |
| U6 | 用户 | 历史资料重做 OCR | `/kb-ocr --batch archive/2025-q3/ --routes vision,mlx,claude,codex` 后 `/kb-audit` | 新指纹目录 `ocr/<engine>/<新fp>/`，旧目录不动；`fixes.jsonl` 只追加；旧 `verified/*.md` 保持 `verified`，直到新确认页点选完成才被新补丁重放 |
| U7 | 用户 | 用现有 klode-build 的书走 `/kb-build` | `/kb-build --kb kb/demo-klode --source aurora-kettle.txt --desk src/desk.json` | `build_receipt.json`：切章自校验 `3 片拼回 == 原文`、`check_cards` 3/3 PASS、strict 0E、`desk --verify` 5/5；`cards_content/` 3 张、`.mcp.json` 新增 `demo-klode` 条目、`smoke_mcp` 6 项 PASS |
| U8 | 用户 | 确认页点选 | 打开 `confirm/20261022.html`，每条分歧选一路或「都不对 / 手输」，导出 `choices.json`；`/kb-audit --writeback choices.json` | 页面每条 = 分歧小图 + 四路候选值 + ★推荐值（多数票）；写回后 `verified/2026-10-22-lab.md` 为 `status: verified`；留「都不对」未手输的记录为 `status: disputed`、`uncertain: [p002-l07]`，且不出现在 `build_receipt.json` 的输入清单 |

「看到什么」全部是命令输出、页面元素或文件内容。阶段 4 每条 U 至少对应一条用户感知层验收测试（e2e lane 用合成 fixtures 回放）。

## 7. 触发每个功能的那句话

- U1「今天的截图入库」/「把 _inbox 里的单子收进去」→ `/med-intake`
- U2「这几张拍的单子先 OCR」/「四路读一下」→ `/kb-ocr`
- U3「跑一遍流水线」/「接着上次那批继续」→ `/klode-kit:kb-pipeline`（带 `resume:<batch>`）
- U4「这份指南 PDF 建个库」→ `/kb-ocr` + `/kb-build`
- U5「问一下 <库名>：……」→ `<kb>-expert` agent（经 MCP）
- U6「2025 年那批重新 OCR 一遍」→ `/kb-ocr --batch` + `/kb-audit`
- U7「这本书走 kb-build」/「书架书桌一起建」→ `/kb-build`
- U8「分歧我点完了，写回」→ `/kb-audit --writeback`；「检查一下库过不过」→ `/kb-check`

## 8. 架构：各用在哪

| 形态 | 用在哪 | 为什么（以及为什么不用别的） |
|---|---|---|
| **plugin** | 本仓根 = 插件根，`.claude-plugin/plugin.json` 捆绑 skills / agents / hooks / workflows | 他人可装、自包含、公开（交接§6）；不用 user 级 skill 是因为硬编码私有路径（G10） |
| **agent** | `klode-card-writer`（便宜模型一卡一派）、`kb-verifier`（只读判 verified/disputed）；`<kb>-expert` 生成到数据项目 | 写卡要并行且不碰 git（A10）；expert 需 MCP 而插件 agent 的 `mcpServers` 被忽略（docs:sub-agents） |
| **skill** | `/kb-ocr` `/kb-audit` `/kb-build` `/kb-check` `/med-intake`，正文只「解析参数 → 调 `src/` → 读 receipt → 汇报」 | 官方优先 skill 而非 `commands/`；逻辑不写在 SKILL.md 里，便于测试 |
| **MCP** | 插件不自带具体库；`/kb-build` 末步写数据项目 `.mcp.json`（`klode-mcp -c <toml>`）；冒烟直连 | 隐私库只允许 project 级（G7/G13）；A5 证明不登记也能跑 |
| **workflow** | `workflows/kb-pipeline.js` 两段式，运行名 `/klode-kit:kb-pipeline` | 交接§3-2 单列工作项；无 Workflow 时 `pipeline.py` 可独立跑 |
| **hooks** | 可选 `PreToolUse` 拦 `codex exec -i` 的图路径不在 `_masked/`、`tests/fixtures/` | 红线第二道闸（交接§6） |
| **脚本** | 全部实现在 `src/klode_kit/{ocr,audit,build,check}/` + `pipeline.py`；189 搬入函数层与 vendoring 脚本都在此 | 用户全局规则「实现代码放 `src/`」；skill / agent / workflow 只编排 |

## 9. 确认记录（AskUserQuestion，2026-10-22）

| 拍板点 | 选项（推荐项标★） | 决定 |
|---|---|---|
| 1 通用层 / 医疗层拆法（02§9-1） | ★a 按交接§2：医疗为可选模块，数据与运行在私有项目 / b 医疗单独成第二个插件 || a（全部按推荐） |
| 2 场景清单（02§9-2） | ★a 就是 6 个（U1–U6，另加 U7/U8 为使用面不是新场景）/ b 增删：＿＿ || a（全部按推荐） |
| 3 云端外发方式（02§9-3） | a 整图 / ★b 抬头打码后外发（N5 + hook）/ c 完全离线（四路变两路） || b 抬头打码后外发 |
| 4 备份（02§9-4） | ★按交接：数据项目本地 git + 外置盘裸仓 + `rsync -c`，只写入 `/med-intake` 文档，不在插件实现 || 按交接（全部按推荐） |
| 5 klode gate rubric 人工审批（02§9-5） | ★a 本期不做 / b 做（加 `klode review` WI） || a 本期不做（全部按推荐） |
| 6 试点验收（02§9-6） | ★按交接：一次住院约 30 份报告跑通，`klode check` 0/0 + 全库判据，在私有项目执行；本仓 e2e 用合成样本 || 按交接（全部按推荐） |
| 7 现有 skill 接入方式（02§9-7） | A 调用用户 skill 脚本 / ★B 搬入（vendoring，修 B1–B5，用户 skill 不动，回修另列 WI-19 闸门）/ C 同步（插件为正本） || B 搬入插件 |
| 8 概要计划与 02 的关系（02§9-8） | ★02 为正本，`docs/概要计划_v2.md` 只做指针 / 两份并行维护 || 02 为正本（全部按推荐） |
| 9 TDD 原地改 `src/` 与「单文件另存 `_v2`」规则的关系（02§8 行 1） | ★`src/` 与 `tests/` 在 git 下、TDD 迭代原地改（git 可恢复），文档类单文件更新仍另存 `_v2` / 每个 WI 开分支 / 代码也另存 `_v2` || 代码原地改，文档另存 |
| 停点 | ★只在阶段 2 停 / 每阶段都停 || 只在阶段 2 停（全部按推荐） |

user case 补充：无改动，U1–U8 按草稿确认。

确认方式：2026-10-22 主会话 AskUserQuestion 四问（接入方式 / 云端外发 / _v2 规则 / 其余各项），用户均选推荐项；该题经 ask-arbiter 影子评审。
