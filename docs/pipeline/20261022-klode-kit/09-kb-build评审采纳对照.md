# 09 · 现有 /kb-build 评审 → 采纳 / 补缺 / 修复 对照（目标第 1 项的闭环）

原话（/goal 第 1 项，逐字）：「1 我现在已经有/kb-build 我基本实现 你也可以根据测试看缺什么 改什么 原来做的好也请评审采纳」

评审依据：`03-基线_现有kb-build实测.md`。那是对用户 skill `klode-build` / `klode-kb` 的 16 步实跑实测，结论分三类：做得好要采纳的 A1–A11，缺口 G1–G14，缺陷 B1–B6。
落地依据：`.claude/tdd-guardian/impl-deviations.md`（各 WI 行）与 git 提交。本表由主会话于 2026-10-27 汇总。验收（阶段 6）按本表逐行复核实物。

状态说明：**已落地**＝代码与测试已入库并推送；**部分**＝主体已落地，残余写在备注；**待验**＝实现已入库，需在 WI-31 / 阶段 6 实跑确认。

## A · 原来做得好的，采纳

| # | 原 skill 的做法 | klode-kit 落点 | WI | 状态 | 备注 |
|---|---|---|---|---|---|
| A1 | 「模型不碰原文」+ 切章自校验（拼回 == 原文） | `build/split_by_manifest.py`、`build/gen_manifest.py` | 02a、03a | 已落地 | 另修了 CRLF 切章拼回不等；HEAD_RE 外置，支持中文章头 |
| A2 | 两道门禁：check_cards 逐字严格 → klode check --strict | `build/check_cards.py` + `check/kb_check.py` 的 strict 行 | 02a、10a | 已落地 | 门禁 1 改为写入前强制（见 B2） |
| A3 | fix_anchors 等价字符自动修复 | `build/fix_anchors.py`；L3 回路自动调用 | 02a、09c | 已落地 | 修 B1；变异得分 0.864 |
| A4 | write_card 只写标记线以下、同步 zoom/aliases | `build/write_card.py` | 02b | 已落地 | 原子写；write_receipt；`--force` 只限人工，且记为步骤失败 |
| A5 | smoke_mcp「MCP 可用性只认实跑」 | `build/smoke_mcp.py`，kb-check 的 smoke_mcp 行 | 02d、10a | 已落地 | klode 0.5.0 CLI 用语为 FOUND/NOT-FOUND，不是 VERIFIED |
| A6 | 专家 agent 模板（cite, don't recall 等） | `build/gen_agent.py` | 02d | 已落地 | 参数化：医疗中文模板 / 通用模板；example 自动生成 |
| A7 | 书桌：desk.json 单一事实源、S5「锚点只能从卡片复制」 | `build/desk_build.py`、`check_synthesis.py`、`gen_desk.py` | 02c、03b | 已落地 | 修 B3/B4；门禁只看留出集 |
| A8 | 两条铁律 | `skills/kb-build/SKILL.md`「铁律 1/2」 | 13a | 已落地 | 留出集冻结（`holdout.freeze.json`），不拿来调线索词 |
| A9 | kb_status / kb_map 红灯 | 保留在用户 skill（跨库）；klode-kit 只做单库 kb-check | — | 按设计不搬 | 02 设计定案 |
| A10 | klode-card-writer：一卡一派、haiku、白名单、不碰 git | `agents/klode-card-writer.md` | 12 | 已落地 | 去掉本机路径；真实写卡在 G2 下待验（WI-31） |
| A11 | SKILL「已知坑」12 条 + 顺序约束 | `skills/kb-build/SKILL.md` K01–K12，其余 skill 各带本阶段的坑 | 13a | 已落地 | |

## G · 缺口，补齐

| # | 缺口 | 补法 | WI | 状态 | 备注 |
|---|---|---|---|---|---|
| G1 | 无编排层 | `pipeline.py` 内核（preflight→ocr→audit→writeback→build→check→publish）；`klode-kit build` 13 步写入不可变 build | 09a、09b | 已落地 | |
| G2 | 三条回路没有自动化 | `loops.py`：L1 线索词、L2 重写卡 / 确定性重生成、L3 修锚点；无进展、轮数或预算耗尽时 exit 50 | 09c | 已落地 | |
| G3 | 门禁 1 不强制 | write_card 写入前跑 check_cards，FAIL 全部不写 | 02b | 已落地 | 即 B2 |
| G4 | strict 放过 stub 卡 | kb-check 全库判据：zoom 全 full、aliases 下限、锚点数、覆盖率、check_published | 10a | 已落地 | 即 B6 |
| G5 | 留出集门槛不固定 | 留出集 ≥5 题且 ≥0.8；解析改用 `klode --json diagnose` | 02c、10a | 已落地 | U7 样书目前 3/5，用户定按此门槛，在 WI-31 调到 ≥4/5 |
| G6 | 只支持有文字层的源 | `/kb-ocr` 四路读图 + 打码 + `/kb-audit` 确认 → 结构化记录 → txt；HEAD_RE 可配置 | 04–08、01a | 已落地 | 真实云端两路在 G2 下待验（WI-31） |
| G7 | 无脱敏导出 | `build/deident.py`（pseudonym / dateshift，密钥放 `_private/`） | 01c | 已落地 | 变异得分 0.843 |
| G8 | 医疗层无结构化记录 → txt / L1 | `records_to_txt.py`、`records_to_l1.py`（确定性卡） | 01a、01b | 已落地 | |
| G9 | 0 测试 | unit 2796 / contract_replay 803 / cli 268；关键路径变异测试闸门 | 全部、15c | 已落地 | 变异测试全量跑在进行中（2026-10-27 夜） |
| G10 | 硬编码路径 | `shutil.which`、环境变量覆盖、`KLODE_REGISTRY`、插件相对路径；`test_no_hardcoded_paths` | 各 WI | 已落地 | klode==0.5.0 从 PyPI 装进 .venv（15a） |
| G11 | 建库骨架无脚本 | `build/init_kb.py` | 03a | 已落地 | |
| G12 | 写卡派发靠口头规则 | `bridge.py` 按批派发、收回执、FAIL 带问题清单重派；`desk-tasks` 补书桌合成 | 09b、09c、13a | 已落地 | |
| G13 | register 直接 `claude mcp add` | `register.py` / `registry_write.py`：project 级 `.mcp.json` 合并、幂等 upsert；u5 双探针 | 11b | 已落地 | 真实写全局 registry 属手动闸门 |
| G14 | `_work/`、`_raw/` 不在 .gitignore 模板 | init_kb 统一追加 | 03a | 已落地 | |

## B · 缺陷，修复（均有回归测试）

| # | 缺陷 | 修法 | WI | 状态 |
|---|---|---|---|---|
| B1 | fix_anchors `--` 修不成 em dash | 变体枚举改长匹配，变体集合 ⊇ 原集合 | 02a | 已修 |
| B2 | write_card 不跑门禁 1 | 写入前强制 check_cards | 02b | 已修 |
| B3 | desk `--verify` 抓不到无连字符的维度名 | 读 `klode --json diagnose` | 02c | 已修 |
| B4 | check_synthesis S4 中文动作头不报错 | 正则改 `[^\n]*` | 02c | 已修 |
| B5 | split `--apply` 重复追加 BIBLIOGRAPHY | 按条目去重 | 02a | 已修 |
| B6 | strict 放过 stub 卡与被折叠标点的锚点 | kb-check 判 FAIL（matrix 10a-01/02） | 10a | 已修 |

## 用户 skill 本身

`~/.claude/skills/klode-build`、`klode-kb` 保持只读，本流水线没有改动（写 `~/.claude` 属手动闸门）。
计划里的 WI-19「回修用户 skill B1–B5」是可选项，要用户批准才做，未执行。
klode-kit 验收通过后，旧 skill 是停用还是回修，在阶段 7 交付时问用户。
