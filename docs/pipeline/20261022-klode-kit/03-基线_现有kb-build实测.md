# 03 · 基线：现有 /kb-build（klode-build + klode-kb 两个 skill）实测与评审

日期：2026-10-22 ｜ 阶段：build-pipeline 阶段 1「现状基线」｜ 范围：`~/.claude/skills/klode-build/`、`~/.claude/skills/klode-kb/`、`~/.claude/agents/klode-card-writer.md`
环境：klode **0.5.0**（`~/.local/bin/klode`，uv tool；任务书写的 0.4.x 已过时）、Python 3.12、macOS。范本库 `~/Documents/Claude/Projects/189_…/kb/jtbd-klode` 只读参考，未改动。
实测全部在 scratchpad（下文记作 `<scratchpad>/kbbuild-baseline/`）用**合成文本**完成：虚构水壶说明书 2 章 + 前置页，42 行 557 词，含弯引号 / em dash / 重复句；未登记 registry、未执行 `claude mcp add`、未写 `~/.claude`、`~/.klode` 或任何 Projects 目录（register.py 用 `HOME=<假家目录>` 跑）。

**一句话结论**：书架链路（切章 → 建卡 → 两道门禁 → 写入 → strict → MCP 冒烟 → 专家 agent）全部跑通，设计可直接采纳；书桌链路跑通但 `--verify` 有两处正则 bug；相对 klode-kit 的「全自动 + 医疗」目标，缺的是**编排层**（一条命令串起来、三条回路自动触发、门禁强制）、**入口层**（图片/扫描 OCR、脱敏、结构化记录 → L1）和**测试**（两个 skill 共 13 个脚本 0 测试）。

---

## 1. 脚本清单（职责 / 输入输出 / 硬编码 / 测试）

### 1.1 klode-build（书架：一本书 0 → 1）

| 脚本 | 职责 | 输入 → 输出 | 硬编码 / 本机依赖 | 测试 |
|---|---|---|---|---|
| `split_by_manifest.py` | `--scan` 打印候选章头；`--manifest` 按 1 基行号逐字切章，拼回自校验，`--apply` 写 `library/<架>/<id>.txt`、整本移 `_raw/`、追加 BIBLIOGRAPHY | 整本 txt + `src/chapters-<架>.json` → 一章一源 | `HEAD_RE` 只认英文章头（Chapter/Part/数字+大写字母…）；无 `~` 依赖 | 无 |
| `skeleton.py` | 只读：打印小节标题 + 首段，写卡前看骨架 | `<kb> <id>` → stdout | 启发式为英文设计（≤12 词、下一段 ≥120 字符） | 无 |
| `check_cards.py` | **门禁 1**：F1 aliases≥10 / F2 Thin 有锚点 / F3 Full 有 ### 且每条 `- ` 带锚点 / A1 ≥15 字符 / A2 逐字唯一（或 #N）/ A3 无换行首尾空白 | `cards_content/<id>.md` + 源 txt → PASS/FAIL，exit 0/1 | 无 | 无 |
| `fix_anchors.py` | 把 NOT FOUND 锚点按 `'→’ "→“” --→— -→–/‐ ...→…` 生成变体，源内唯一命中则原地改写 | `cards_content/*.md` → 原地改 | 无 | 无 |
| `write_card.py` | 把 cards_content 写到 `library/cards/<id>.md` 标记线以下，改 `zoom: full` 与 aliases | cards_content → library/cards | 依赖 klode 的 scaffold 标记字符串 | 无 |
| `register.py` | 追加 `[[kb]]` 到 registry（幂等）；打印/执行 `claude mcp add -s user|project <id>-klode -- klode-mcp -c <toml>` | `<kb>` → `~/.klode/registry.toml`、`~/.claude.json` | `~/.klode/registry.toml`、`~/.local/bin/klode-mcp`、`claude` CLI | 无 |
| `smoke_mcp.py` | 实跑 klode-mcp stdio：initialize → tools/list → search → zoom → verify 真句/伪句；`--desk` 加 list_lenses / diagnose / consult_dimension | `<kb> --card --real [--desk]` → PASS 表，exit 0/1 | `~/.local/bin/klode-mcp` | 无 |
| `gen_agent.py` | 从卡片 front-matter 生成 `<id>-klode-expert.md`（工具白名单、卡片表、书桌表、流程纪律） | `<kb> --out` → agent md | 根目录写成相对 `~/Documents/Claude/Projects` 的路径，否则绝对路径 | 无 |
| `desk_build.py` | 书桌：`--apply` 开 `[frameworks]` + 由 spec 生成 `_diagnostics.md`；`--prompts` 每维度一份 worker 提示词；`--status`；`--verify` = check_synthesis + strict + diagnose 命中率 ≥ threshold | `src/desk.json` → toml / `_diagnostics.md` / `_work/desk_prompts/` | `~/.local/bin/klode`；提示词里写死 `~/.claude/skills/klode-build/…` | 无 |
| `check_synthesis.py` | 书桌门禁：S1 front-matter / S2 cards 存在 / S3 Craft 三小节 + Provenance / S4 动作头含 ASCII / S5 **锚点必须是卡片里的同一字符串** / S6 无引擎术语 | `_syntheses/<dim>.md` → PASS/FAIL | 无 | 无 |
| `eval_compare.py` | 同一 gold 跑多个库 `search --full`，Hit@1 / Hit@k / MRR；`--desk-gold` 跑 diagnose 路由准确率 | gold.jsonl → md 报表 | `~/.local/bin/klode` | 无 |
| `prompts/write_card.md` | 写卡规范（格式 + 锚点 5 条规则 + 取舍） | — | 引用 `189/kb/...` 范本路径 | — |
| `prompts/synthesis.md` | 书桌蒸馏规范（格式 + 6 条机器验规则） | — | 引用 `003/kb/...` 范本路径 | — |
| `SKILL.md` | 16 步流程表 + 已知坑 12 条 + 产物清单 | — | 步骤①要求「抄 189 的 library.toml」；扫描件指向 `003/doc/plan/...` | — |

### 1.2 klode-kb（全体库 1 → N）

| 脚本 | 职责 | 输入 → 输出 | 硬编码 / 本机依赖 | 测试 |
|---|---|---|---|---|
| `kb_status.py` | 逐库一行：书架 / 卡·锚点·别名 / 书桌 / `check --strict` / MCP 作用域 / 卡片进 git & .txt 泄漏 | registry → md 表 | `~/.klode/registry.toml`、`~/.local/bin/klode`、`~/Documents/Claude/Projects`、`~/.claude.json`、`~/.claude/plugins/cache` | 无 |
| `kb_map.py` | 结构地图：书架 → 卡（章名/锚点/源在否）→ 书桌维度 → **盲区卡** | registry → md，`--write-local` 写 `library/MAP.md` | 同上（registry、Projects） | 无 |
| `SKILL.md` | status / map / mcp / desk 六步 / doctor / 评测；**两条铁律** | — | 引用 003、189、16_d 项目 | — |
| `agents/klode-card-writer.md` | haiku 写卡工人：一卡一派，check_cards 到 PASS，不碰 git | — | `Bash(python3 <绝对家目录>/.claude/skills/klode-build/scripts/*)` 白名单写死了绝对家目录（非 `~/`） | — |

**测试现状**：两个 skill 目录下没有 `tests/`，13 个脚本 0 个自动化测试；唯一的「测试」是 jtbd 实跑记录与 SKILL.md 里的已知坑表。

---

## 2. 实测表（合成库 `demokb`，全部命令可在 `<scratchpad>/kbbuild-baseline/` 复跑）

`$S` = `<scratchpad>/kbbuild-baseline`，`$B` = `~/.claude/skills/klode-build/scripts`，`$K` = `~/.claude/skills/klode-kb/scripts`。耗时为 zsh `time` 的 wall-clock。

| # | 步骤 | 命令 | exit | 关键输出 | 耗时 |
|---|---|---|---|---|---|
| 1 | 建库骨架 | `klode init demo-kb --shelf manual` + 手补 `id/name/description` | 0 | `klode init` 产物**不含 `id/name/description`**，而 register / gen_agent / kb_status 都靠 `id`；所以 SKILL.md 才要求「抄 jtbd 的 toml」——骨架步骤无脚本 | 0.09s |
| 2 | 抽取 | `klode -c … ingest aurora-kettle-manual.txt --shelf manual --id _whole-manual --format txt` | 0 | `library/manual/_whole-manual.txt`，PROVENANCE 一行（`handler txt, 557 words, integrity abstained`）；`diff` 证明 txt 路线输出与输入**逐字相同** | 0.18s |
| 3 | 扫章头 | `python3 $B/split_by_manifest.py $S/demo-kb --whole library/manual/_whole-manual.txt --scan` | 0 | 4 行候选：TOC 区 5/6，正文 10/28 —— 与 SKILL 说的「TOC 先出现一遍」一致 | 0.05s |
| 3b | 中文章头探针 | 同上，对 `第一章 … / 一、检验报告` 文本 | 0 | **0 条候选**：`HEAD_RE` 不认中文章头与「一、」 | — |
| 4 | 切章预演 | `… --manifest src/chapters-manual.json` | 0 | `自校验通过：3 片拼回 == 原文 (sha256 …)`，列 33/280/244 词 | 0.05s |
| 4b | 错误清单 | 第一片 `start_line: 2` | 1 | `第一片必须从第 1 行开始…`，未写入 | — |
| 5 | 切章写入 | `… --apply` | 0 | 3 个 `.txt`、整本移入 `_raw/`、BIBLIOGRAPHY 追加 3 行 | 0.04s |
| 5b | 二次 `--apply` | 把整本拷回再跑 | 0 | **BIBLIOGRAPHY 变 7 行（重复追加）**→ Bug B5 | — |
| 5c | 整本已移走再跑 | `… --manifest …` | 1 | `找不到整本源文件`（doctor 流程需先 ingest 再切，符合预期） | — |
| 6 | 建卡骨架 | `klode -c … build --stamp` | 0 | `cards: 3 created … zoom coverage: stub=3 … alias 0/3` | 0.08s |
| 7 | 看骨架 | `python3 $B/skeleton.py $S/demo-kb demokb-ak-ch02-descaling 120` | 0 | 正确识别 3 个小节标题 | 0.03s |
| 8 | 手写 3 张卡 | ch01 故意埋 4 类错：直引号、`--`、13 字符短锚、重复句无 #N | — | — | — |
| 9 | 门禁 1 | `python3 $B/check_cards.py $S/demo-kb` | 1 | 精确报出 4 个问题（A2 AMBIGUOUS、A2 NOT FOUND×2、A1 太短），另两卡 PASS | 0.04s |
| 10 | 自动修锚 | `python3 $B/fix_anchors.py $S/demo-kb` | 1 | `修好 1（直引号→弯引号），仍找不到 1（-- → —）` → Bug B1 | 0.04s |
| 11 | 人工修 + 复检 | 加长短锚、加 `#2`、`--`→`—` | 0 | 3 卡全 PASS（22 锚点） | 0.04s |
| 12 | 写入 | `python3 $B/write_card.py $S/demo-kb` | 0 | `zoom: full`、aliases 同步；**在门禁 1 仍 FAIL 时也照写不误** → Bug B2 | 0.04s |
| 13 | 门禁 2 | `klode -c … check --strict` | 0 | `OK: 0 errors, 0 warning(s)`；NOTE：非 git 仓时版权守卫 N/A | 0.10s |
| 13b | 门禁 2 探针 | 卡里放不存在锚点 / `--` 锚点 / 重复句无 #N / 整卡 stub | — | 不存在 → `ERROR [F citation-rot]`；`--`（源为 —）→ **0E 0W 通过**（klode 折叠标点，`verify` 回 `FOLDED-ONLY` exit 0）；重复句 → `WARN [F-ambiguous]`；**stub 卡（没写 Thin/Full）→ 0E 0W 通过** | — |
| 14 | 登记（假 HOME） | `HOME=$S/fakehome python3 $B/register.py $S/demo-kb` | 0 | 假 registry 新增 `[[kb]] demokb`；打印 `claude mcp add -s user demokb-klode -- …/klode-mcp -c …`；再跑 → `已有，不重复登记`；真 `~/.klode/registry.toml` 无 demokb | 0.28s |
| 15 | MCP 冒烟 | `python3 $B/smoke_mcp.py $S/demo-kb --card demokb-ak-ch02-descaling --real "one part white vinegar to three parts water" --query "vinegar descale"` | 0 | 6 项 PASS（klode 0.5.0、8 tools、search 命中、thin 非空、真句 VERIFIED、伪句 NOT FOUND）；**不登记也能跑**（直接 `klode-mcp -c`） | 0.11s |
| 16 | 专家 agent | `python3 $B/gen_agent.py $S/demo-kb --out $S/out/agents/demokb-klode-expert.md` | 0 | 生成 115 行 agent；根目录写成绝对路径（不在 Projects 下时）；开书桌后自动加 `list_lenses/diagnose/consult_dimension` 与维度表 | 0.04s |
| 17 | 全体巡检（假 HOME） | `HOME=$S/fakehome python3 $K/kb_status.py` | 0 | `demokb ｜ manual ｜ 3/22/43 ｜ ❌ ｜ 0E/0W ｜ MCP 无 ｜ 不在 git 仓`；需把 `klode` 软链进假 HOME 的 `.local/bin`（硬编码） | 0.15s |
| 18 | 结构地图 | `HOME=$S/fakehome python3 $K/kb_map.py --kb demokb` | 0 | 书架表 3 行，锚点数正确 | 0.04s |
| 19 | 书桌开关 | `python3 $B/desk_build.py $S/demo-kb --apply` | 0 | toml `[frameworks] enabled = true`；`_syntheses/_diagnostics.md` 2 行 | 0.05s |
| 20 | worker 提示词 | `… --prompts` | 0 | `_work/desk_prompts/{descale-routine,safety}.txt` | 0.05s |
| 21 | 书桌门禁 | 手写 synthesis 故意埋：一条锚点重新从源截、一条动作头纯中文；`python3 $B/check_synthesis.py $S/demo-kb descale-routine` | 1 | 只报 **S5**（锚点不在卡片里）；**S4 没报** → Bug B4 | 0.04s |
| 22 | 书桌门禁复检 | 修正后 `check_synthesis.py $S/demo-kb` | 0 | 两维度 PASS（各 9 锚点） | — |
| 23 | 路由 | `klode -c … diagnose "保温功能能不能开着不管"` | 0 | `• safety`（正确）；`--json` 也正确 | — |
| 24 | 书桌验收 | `python3 $B/desk_build.py $S/demo-kb --verify` | 1 | synthesis PASS、strict 0E；**diagnose 2/4 = 0.50**：`safety` 两题都判 `∅`——klode 实际路由正确，是 verify 的正则只抓带连字符的词 → Bug B3 | 0.43s |
| 25 | 留出集 | `klode diagnose "壶底一层白的，烧水比以前久"` | 1 | `no symptom matched`——子串路由对未登记说法为零（印证铁律 2） | — |
| 26 | MCP 书桌冒烟 | `smoke_mcp.py … --desk "多久除一次垢=descale-routine"` | 0 | 9 项全 PASS | — |
| 27 | 评测 | `python3 $B/eval_compare.py --gold eval/gold.jsonl --kb demo=… --desk-gold eval/gold_desk.jsonl --out $S/out/eval_latest.md` | 0 | 4 题 Hit@1 1.00，中位 70 ms；书桌路由 1/2（留出题 ∅） | 0.46s |

产物位置：`<scratchpad>/kbbuild-baseline/{demo-kb, fakehome, out/agents/demokb-klode-expert.md, out/eval_latest.md, zh-probe}`。

---

## 3. 评审：做得好、应原样采纳进 klode-kit

| # | 项 | 为什么好 | klode-kit 处置 |
|---|---|---|---|
| A1 | **「模型不碰原文一个字」+ 切章自校验**（`split_by_manifest.py` 拼回 == 原文、不通过不写入、第一片必须从 1 行起） | 确定性、可重建（`src/chapters-*.json` 是全库重建依据）；实测错误清单被正确拒绝 | **vendoring**（修 B5；扩 `HEAD_RE` 到中文/医疗章头） |
| A2 | **两道门禁分工**：门禁 1 `check_cards.py` 逐字严格（标点、唯一、≥15 字符、格式 F1–F3）→ 门禁 2 `klode check --strict` 全库 citation-rot | 实测门禁 1 抓出全部 4 类埋错；门禁 2 会折叠标点（`FOLDED-ONLY`）且放过 stub 卡，所以**门禁 1 不可省** | **vendoring**（并在写入前强制执行，见 B2） |
| A3 | `fix_anchors.py` 等价字符自动修复 | 直引号 → 弯引号 一次修好，是写卡模型最常见的错 | **vendoring**（修 B1） |
| A4 | `write_card.py` 只写标记线以下、同步 `zoom/aliases` | 与 klode scaffold 的契约清楚，`build` 可反复跑不丢人写内容 | **vendoring**（加门禁前置） |
| A5 | `smoke_mcp.py` 「MCP 可用性只认实跑」+ 真句 VERIFIED / 伪句 NOT FOUND 成对 | 不登记也能跑，适合 CI；参数名坑（MCP `id` vs CLI `card`）写进代码注释 | **vendoring** |
| A6 | `gen_agent.py` 的专家 agent 模板：工具白名单、「cite, don't recall」、两个词以上必 `full=true`、`level=content` 不吐原文、查不到就说查不到、表格数字提醒 | 这些都是 jtbd 实跑踩出来的；开书桌后自动加路由工具 | **vendoring**（根目录改为 `~/` 或相对库；example 自动生成） |
| A7 | 书桌设计：`src/desk.json` 单一事实源 → `_diagnostics.md` 生成不手改；`check_synthesis` S5「**锚点只能从卡片复制**」 | 保证书桌与卡片不漂移、卡片重写后可检出；实测 S5 精确命中 | **vendoring**（修 B3/B4） |
| A8 | **两条铁律**（worker handback 前不碰其文件；`_diagnostics.md` 是兜底不是主路由，留出集不拿来调线索词） | 是方法论级结论，避免为验收 100% 堆线索词 | **写进 klode-kit 的 SKILL/README**，并以留出集 + `--verify` 落地 |
| A9 | `kb_status.py` 的 `.txt 被跟踪 = 版权泄漏 ‼`、`kb_map.py` 的「书桌盲区卡」 | 两个一眼能看的红灯 | kb_status/kb_map **保留在用户 skill**（管全体库，跨项目）；klode-kit 只需单库版的 `status` |
| A10 | `klode-card-writer` agent：一卡一派、haiku、工具白名单、**绝不碰 git** | 便宜模型 + 机器门禁兜底 的分工正确 | 插件自带同名 agent（白名单路径改成插件相对路径） |
| A11 | SKILL.md 的「已知坑」表（12 条）与「顺序约束」 | 多数坑是实跑所得（H 类陈旧警告、并发 20 封顶、`Agent type not found`） | 并入 klode-kit 文档 |

---

## 4. 缺口清单（相对 klode-kit 全自动链路 + 医疗层）

| # | 缺口 | 现状 | 对 klode-kit 的要求 |
|---|---|---|---|
| G1 | **无编排层**：16 步由人/主会话按表逐条敲 | 每步一个脚本，退出码各自为政；SKILL 里的「顺序约束」靠人记 | 一条 `kb-build` 命令（或 `pipeline.py`）串起 ①–⑬，每步产 receipt，失败即停并指出回路 |
| G2 | **三条回路没有自动化** | 路由不足 → 人改 cues 再 `--apply`；引文非 FOUND → 人改卡；锚点失效 → 人跑 fix_anchors | ①`--verify` 未达阈值自动列出未命中题与建议 cues，回书桌；②smoke/strict 出现 NOT FOUND 自动定位卡并重派 writer；③strict 的 `F citation-rot` 自动跑 `fix_anchors` 再复检 |
| G3 | **门禁 1 不强制**（B2） | `write_card.py` 不调 `check_cards` | 写入前必须 PASS；否则 exit 1 不写 |
| G4 | **门禁 2 放过 stub 卡**（实测 13b） | strict 对未写 Thin/Full 的卡 0E 0W；`build` 的 `zoom coverage: stub=N` 只是信息 | 增加「卡片 zoom 全为 full、aliases ≥10、锚点 ≥ N」的全库判据（可从 kb_status 的计数派生） |
| G5 | **diagnose ≥4/5 路由测试**未成为固定门 | `desk.json` 每维度 `tests` 可 1 条；阈值 0.8 但题数无下限；verify 解析用 prose 正则（B3） | 固定：每维度 ≥1 题 + 全库留出集 ≥5 题，命中 ≥4/5；改用 `klode --json diagnose` 解析 |
| G6 | **只支持有文字层的源**（EPUB/PDF-text/txt） | SKILL 明说扫描件走别处；`klode ingest` 无图片输入；章头正则英文 | klode-kit 核心：图片/扫描 → OCR → **脱敏** → txt → 本流程；`HEAD_RE` 需可配置（中文「第 N 章」「一、」、化验单「检验报告」等） |
| G7 | **脱敏导出**完全没有 | 无任何 PII 处理；`library/*.txt` 靠 .gitignore 挡版权，不挡隐私 | 导出 txt 前的脱敏器（姓名/编号/日期偏移）+ 脱敏回归测试；隐私库 MCP 只允许 project 级（klode-kb SKILL 已有此规则，但没有机器强制） |
| G8 | **医疗层：结构化记录 → txt / L1** 没有 | 写卡只有「模型读整章写卡」一条路 | 「verified 结构化记录（front-matter，labs/meds 三元组）→ 确定性生成 txt 源 + 按字段生成 L1」的生成器 + 校验器（数值/单位/日期三元组逐字可 grep） |
| G9 | **0 测试** | 13 脚本无 tests/ | 插件内 `tests/`：切章拼回、门禁 1 六条规则、fix_anchors 变体、check_synthesis S1–S6、desk verify 解析、smoke（可用本基线的合成库作 fixture） |
| G10 | **硬编码**：`~/.local/bin/klode(-mcp)`、`~/.klode/registry.toml`、`~/Documents/Claude/Projects`、agent 白名单绝对路径、prompts/SKILL 引用 003/189/16_d | 换机或他人安装即断 | `shutil.which("klode")` + 环境变量覆盖；registry 路径走 `klode --registry`；文档去项目号 |
| G11 | **建库骨架无脚本** | `klode init` 不写 `id/name/description`，要抄范本 toml | `init_kb.py`：init + 补 `[library] id/name/description` + 建 `cards_content/ src/ _raw/` + .gitignore 追加 |
| G12 | **写卡派发靠主会话 Agent tool** | 并发/补发/FAIL 重派都是口头规则 | 编排层按批派发并收 receipt；FAIL 自动把问题贴回重派同一卡（最多 N 次） |
| G13 | `register.py` 直接 `claude mcp add`，无 dry-run 校验、无 `claude` 不存在的提示 | 实测打印命令正确；失败只打印 | 插件应走 `.mcp.json`（插件级 MCP）而不是 user 级 add；隐私库强制 project 级 |
| G14 | 书桌 worker 产物 `_work/` 与 `_raw/` 不在 `.gitignore` 模板里 | 本次手动追加 | 骨架脚本统一写 |

---

## 5. Bug 清单（均可在 `<scratchpad>/kbbuild-baseline/` 复现）

| # | 脚本 | 现象 | 根因 | 复现命令 | 修法 |
|---|---|---|---|---|---|
| B1 | `fix_anchors.py` | `--`（源为 ` — `）永远修不成单个 em dash；实测「修好 1，仍找不到 1」 | `variants()` 用 `spots.sort()` 后按「起点不重叠」剪枝，`"-"`(长 1) 与 `"--"`(长 2) 同起点时短的排前面被保留、`"--"` 被丢弃，于是只生成 `-—`/`—–` 等双字符变体 | `python3 - <<'EOF'`<br>`import importlib.util as u;s=u.spec_from_file_location("fa","~/.claude/skills/klode-build/scripts/fix_anchors.py".replace("~",__import__("os").path.expanduser("~")));fa=u.module_from_spec(s);s.loader.exec_module(fa)`<br>`print("reset — this" in set(fa.variants("reset -- this")))  # False`<br>`EOF` | 排序改 `spots.sort(key=lambda x:(x[0],-(x[1]-x[0])))` 让长匹配优先；或先替换多字符组再单字符 |
| B2 | `write_card.py` | 门禁 1 FAIL 的卡照样写进 `library/cards/`，随后 strict 0E 放行（标点折叠） | 不调用 `check_cards`；SKILL 的「⑧ 必在 ⑨ 前」是口头约束 | 用第 2 节步骤 9–12：ch01 仍有 `A2 NOT FOUND` 时 `write_card.py` exit 0 并写入 | `write_card.py` 内置 `check_one()`，有问题 exit 1 且不写；或加 `--force` |
| B3 | `desk_build.py --verify`、`eval_compare.py --desk-gold` | 维度名**不含连字符**（如 `safety`）时永远判 `∅`，验收被压到 0.50；klode 本身路由正确 | 用正则 `\b([a-z]+(?:-[a-z]+)+)\b` 从 prose 输出里抓维度名，只匹配带连字符的词；且会误抓 core_question 里的英文连字词 | `python3 $B/desk_build.py $S/demo-kb --verify` → `FAIL diagnose「保温功能能不能开着不管」→ ∅（期望 safety）`，而 `klode -c … diagnose "保温功能能不能开着不管"` 打印 `• safety` | 改用 `klode --json -c … diagnose`，取 `value[0][0]` |
| B4 | `check_synthesis.py` S4 | 「动作」条目纯中文加粗头**不报错**（规范要求 ASCII 头，否则 gate slug 塌成同名） | `re.search(r"^### .*动作.*$\n(.*?)(?=^### \|^## \|\Z)", body, re.M\|re.S)`：`re.S` 下 `.*` 跨行贪婪，捕获组实测为空串 `''`，循环体从不执行 | 把 `safety.md` 第 21 行改成 `- **壶、底座、电线不可浸水。** (grep: …)` 后 `python3 $B/check_synthesis.py $S/demo-kb safety` → PASS | 标题行用 `[^\n]*` 代替 `.*`：`r"^### [^\n]*动作[^\n]*\n(.*?)(?=^### |^## |\Z)"` |
| B5 | `split_by_manifest.py --apply` | 重复 `--apply`（doctor 流程「源丢了重切」时必然发生）BIBLIOGRAPHY 重复追加：3 行 → 7 行 | 只判 `bib.exists()` 不判已有行 | 第 2 节步骤 5b | 追加前按 `` `library/<架>/<id>.txt` `` 去重；或整段 `<!-- shelf:<架> -->` 标记替换 |
| B6（设计洞） | `klode check --strict` 作门禁 2 | stub 卡（未写 Thin/Full）0E 0W；`--`/直引号锚点 0E（`FOLDED-ONLY`） | klode 的设计：只查 citation-rot 与歧义，不查「卡写没写」「标点是否逐字」 | 第 2 节 13b | 不改 klode；在编排层加 G4 判据，并让门禁 1 成为硬门 |

次要：`smoke_mcp.py` 默认 query 取 `--real` 前两词（实测 `"one part"`），命中靠运气，建议默认取卡 aliases 第一项；`check_cards.py` F3 只查顶层 `- `，缩进子弹不查；`kb_status.py` 的 `klode` 路径写死 `~/.local/bin/klode`，假 HOME 下需软链才跑得起（见步骤 17）。

---

## 6. 采纳 / 补缺处置清单

### 6.1 klode-kit 插件直接 vendoring（复制进插件 `scripts/`，修 bug，加测试）

| 脚本 | 改动 |
|---|---|
| `split_by_manifest.py` | 修 B5；`HEAD_RE` 外置为可配置（英文 + 中文「第 N 章 / 一、/ 检验报告」）；增加 `--scan` 的「正文第 2 次出现」自动判别 |
| `check_cards.py` | 原样；加 `--json` 输出供编排层 |
| `fix_anchors.py` | 修 B1 |
| `write_card.py` | 修 B2（内置门禁） |
| `smoke_mcp.py` | 原样；默认 query 改取 alias |
| `gen_agent.py` | 根目录用 `~/` 或库相对路径；example 由 `desk.json` 的 `tests` 自动填 |
| `desk_build.py` | 修 B3（`--json`）；`--verify` 加留出集必填与 ≥4/5 |
| `check_synthesis.py` | 修 B4 |
| `eval_compare.py` | 修 B3 同源问题 |
| `skeleton.py` | 原样 |
| `prompts/write_card.md`、`prompts/synthesis.md` | 去掉 003/189 范本路径，范本改为插件内 fixture |
| `agents/klode-card-writer.md` | 复制为插件 agent；白名单改 `${CLAUDE_PLUGIN_ROOT}/scripts/*` |

### 6.2 klode-kit 新写（现有 skill 没有）

`init_kb.py`（G11）、`pipeline.py` 编排 + receipt + 三条回路（G1/G2/G12）、OCR/截图入口 + 脱敏导出（G6/G7）、`records_to_txt.py` / `records_to_l1.py` 结构化记录生成器与校验（G8）、全库完成度判据（G4）、`tests/`（G9，用本基线合成库作 fixture）。

### 6.3 保留在用户 skill 里不动（不进插件）

| 项 | 理由 |
|---|---|
| `klode-kb/kb_status.py`、`kb_map.py` | 管的是本机全体库（跨 003/189/16_d），依赖本机 registry 与 Projects 布局，是个人运维工具 |
| `register.py` 的 user 级 `claude mcp add` | 插件应走自身 `.mcp.json`；个人库的 user 级登记留给用户 skill |
| `klode-build/SKILL.md` 的「一库还是多库」「EPUB 范本 jtbd」等书籍向导 | 书籍场景与 klode-kit 的扫描件/医疗场景并行存在，各自演进 |
| 两条铁律、已知坑表 | 文字规则，klode-kit 文档引用即可，不复制脚本 |

---

## 7. 附：本次合成测试的约束与可复现性

- 合成文本为虚构品牌「Aurora Kettle AK-200」说明书，无任何真实个人、医疗或版权内容；章节 id 用 `demokb-ak-chNN-slug`。
- 全程未写入 `~/.claude`、`~/.klode`、任何 Projects 目录；`register.py` 以 `HOME=<scratchpad>/kbbuild-baseline/fakehome` 运行，真 registry 经 `grep -c demokb` 核实为 0。
- 每步耗时 ≤ 0.5 s（557 词语料）；jtbd 三书的实际耗时主要在写卡模型侧，本基线不覆盖模型写卡质量（那部分由 `eval_compare.py` 的 Haiku/Opus 对照另行评测）。
