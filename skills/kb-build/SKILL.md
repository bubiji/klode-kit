---
name: kb-build
description: 把一本或几本书（EPUB、有文字层的 PDF、md、txt），或已经核对过的医疗记录，建成可逐字引用原文的 klode 知识库：13 步固定流程（导出或抽取、切章、klode build、便宜模型并行写卡、两道门禁、strict 0 错 0 警告、书桌、专家 agent、自检），每步一个回执，成品是不可变的 kb/<库名>/builds/<构建号>/。用户说「这本书走 kb-build」「书架书桌一起建」「这份指南 PDF 建个库」（先 /klode-kit:kb-ocr，再走本 skill），或输入 /klode-kit:kb-build 时使用。
argument-hint: --kb kb/<库名> [--source <书的文本>] [--desk src/desk.json]
allowed-tools: Bash(*/bin/klode-kit *)
---

# /klode-kit:kb-build — 书 / 核对过的记录 → 可逐字引用的 klode 库

一句话：库里的原文由 klode 逐字抽取、脚本逐字切章，**模型不碰原文一个字**；模型只写卡片（中文说明加指向原句的 grep 锚点），
锚点由两道机器门禁核验。这个 skill 只负责调命令、派 worker、读回执、汇报，没有自己的业务逻辑。
在数据项目目录里运行（别处运行加 `--project DIR`）。

| 源 | 走哪条路 |
| --- | --- |
| 已经 verified 的医疗记录（`records/` 里） | 记录路径：不带 `--source`，卡片和书桌合成都由程序确定性生成，零模型调用 |
| EPUB、有文字层的 PDF、md、txt | 书籍路径：带 `--source`，写卡由便宜模型 worker 完成 |
| 扫描件、截图 | 先 `/klode-kit:kb-ocr`、`/klode-kit:kb-audit` 得到 verified 记录，再走记录路径 |

同一使用场景的几本书放一个库、多个书架；场景不同就分库。

## 一、构建

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit build --kb kb/KB_ID
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit build --kb kb/KB_ID --source BOOK.txt --shelf book
```

第一行是记录路径，第二行是书籍路径。可选参数：`--manifest` 显式章节清单，`--desk` 与 `--holdout` 显式书桌和留出集，
`--loops` 在内容门禁失败时自动修复，`--budget-usd` 写卡预算上限。**不要用 `--force`**：它只给人手动用，会让对应步骤判失败。

13 步按固定顺序：export、init_kb、ingest、split_manifest、klode_build、dispatch_writers、gate1、write_card、
check_strict、desk_apply、desk_verify、gen_agent、self_check。每步一个回执 `receipts/<步骤>.json`，失败即停，后面的步骤不跑；
成品写在 `kb/<库名>/builds/<构建号>/`，完成后整棵树只读，`current` 不会被动。汇报时读 `build_receipt.json`：13 步各自的状态、切章自校验、两道门禁结果。

退出码：0 全部通过；20 内容门禁失败（回执里点名哪一步，报告在 `reports/<步骤>.txt`）；30 契约问题；40 环境、配置或构建锁；
50 回路放弃（连续无进展）或预算用完；60 隐私拒绝；61 真实调用闸门（G2）没开，停在 dispatch_writers，
**不要自己设置环境变量去绕过**，把原话告诉用户；2 用法错误。

## 二、书籍路径：书桌合成由 worker 写

书桌（`library/frameworks/_syntheses/<维度>.md`）是跨卡蒸馏，必须由模型 worker 写。构建本身不派这批 worker（`dispatch_writers` 只管卡片），
所以第一次构建在 `desk_apply` 步以 **exit 20** 结束，回执 detail 写 `desk syntheses missing for: <维度…>`。这不是故障，是下面流程的起点：

1. 第一次构建，不带 `--syntheses`（命令见第一节）。得到一个只读的失败构建，里面已经有写好的卡。
2. 生成任务清单：

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit desk-tasks --build kb/KB_ID/builds/BUILD_ID --out work/syntheses/KB_ID
```

输出一份 JSON：`tasks[]` 每项有 `dimension`、`cards`、`prompt_file`（worker 提示词）、`output`（要写的文件）；`present[]` 是已经有文件的维度；
同时写出 `work/syntheses/KB_ID/repair.json`。

3. 每个 task 派一个 `klode-card-writer` 子代理（Agent 工具，model 用 haiku）：先读 `prompt_file`，把全文原样当作任务提示词。
   提示词开头的「任务类型变更」说明这次写的是维度合成，不是卡片。它只写 `output` 这一个文件，不碰构建目录和 git。
   并发最多 20 个，先发 20 个，之后每收到一个回报补发一个。子代理要写 `work/syntheses/` 下的文件，需要用户允许那个目录的编辑。
4. 全部 worker 交回（handback）后，跑门禁：

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit check-synthesis kb/KB_ID/builds/BUILD_ID --syntheses work/syntheses/KB_ID
```

   有 FAIL 就把该维度的问题原文贴给同一个 worker 重派，**不要自己改文件**。常见的是 S5：锚点必须从卡片逐字复制，不能重新从源里截。
5. 第二次构建，参数与第一次完全相同，再加 `--syntheses` 和 `--repair`：

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit build --kb kb/KB_ID --source BOOK.txt --shelf book --syntheses work/syntheses/KB_ID --repair work/syntheses/KB_ID/repair.json
```

   `--repair` 让新构建把第一次构建的卡**逐字节带过来**，不再调用写卡 worker（不再花钱），所以 worker 复制的锚点仍然有效；
   不带它就会重新写卡，卡一变，合成里的锚点就对不上。这一轮不要同时加 `--loops`。第一次的失败构建保留着，不要手动删，清理交给 `kb-publish --prune`。
6. 13 步全 PASS 后，运行 `/klode-kit:kb-check`。发布（`kb-publish --kb kb/KB_ID --build BUILD_ID`）会写项目的 `.mcp.json`、
   库登记表、专家 agent 文件和 `settings.local.json`，写的是用户全局目录的部分属于手动闸门：只在用户明确同意后做，不要自己发布。

书桌验收以**留出集**为准（`desk_verify` 步，至少 5 题、命中率不低于 0.8）。留出集在首次 `desk-build --verify` 时冻结（`src/holdout.freeze.json`），
之后改动要加 `--refreeze --reason` 并写明原因。记录路径的合成是程序按卡片锚点机械生成的索引，不需要 worker。

## 三、顺序约束

- 门禁 1（`check-cards`，逐字严格）必须在写入卡片（`write-card`）之前；写入之后再改卡，要重跑。
- `klode check --strict` 要先 0 错 0 警告，才进书桌；书桌验收过了，才生成专家 agent。
- 构建流程已经按这个顺序固定。构建出来的树是只读的：卡片有错，走 `--loops` 或重新构建，不要去改封存的树。

## 四、两条铁律

- **铁律 1：worker 交回（handback）之前，不碰它的文件。** 文件存在不等于写完；主会话提前修改会被 worker 覆盖。
  `desk-build --status` 和 `desk-tasks` 的 `present` 只看文件在不在，不能当完成判据；完成以 worker 的回报加门禁为准。
- **铁律 2：`_diagnostics.md` 是兜底，不是主路由；留出集永远不拿来调线索词。** 关键词子串对没登记过的说法几乎为零，
  专家 agent 自己路由（生成时已内置）。线索词只登记真实常见的说法，不要为了验收 100% 往里堆；
  调优集和留出集分开计分，`cue-suggest` 只读调优集，留出集由 `desk-build --verify` 冻结并核对（`src/holdout.freeze.json`）。

## 已知坑

| 编号 | 现象 | 原因 | 处理 |
| --- | --- | --- | --- |
| K01 | `config error: extra_guard_dirs … must be a simple directory name` | 配置里写了 `../_raw` | `extra_guard_dirs = []`，`_raw/` 靠 `.gitignore` 挡（`init-kb` 建的库已是这样） |
| K02 | 章头扫描（`split-chapters --scan`）漏了某章 | 章名含破折号或特殊字符，不匹配章头正则 | 用 `--head-re` 指定章头，或在整本文本里直接找行号；目录里出现一次、正文出现一次，取第二次 |
| K03 | 切章自校验失败 | 清单行号不是 1 基，或第一片不从第 1 行起 | 按报错改清单；只有「N 片拼回 == 原文」通过才会写入 |
| K04 | `check-cards` 报 `AMBIGUOUS` | 片段在章里出现多次（书的口头禅、小节名） | 把片段加长到唯一；实在要引重复句，在锚点后加 `#2`（指第 2 次出现） |
| K05 | 报 `NOT FOUND`，但肉眼看着在 | 引号 ’ 与 '、破折号、大小写、双空格不同 | 先 `fix-anchors` 自动换成等价字符；剩下的 `grep -n` 核对原句后逐字复制 |
| K06 | `fix-anchors` 也修不了 | 锚点跨了源文本的换行（EPUB 抽取按句硬换行） | 缩成行内子串（仍要 ≥ 15 字且唯一） |
| K07 | 写卡 agent 没加载（`Agent type not found`） | 新建或刚改的 agent 文件要下次启动才生效 | 插件自带的 `klode-card-writer` 随插件加载；刚装或刚改后重启或运行 `/reload-plugins`；临时可用通用子代理加 model，并把 agent 正文贴进提示词 |
| K08 | 并发 20 个封顶 | `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` 默认 20 | 一卡一个 worker，先发 20 个，之后每收到一个回报补发一个 |
| K09 | 切章行号对不上（自己生成的 txt 走 ingest） | ingest 会删掉重复的短行 | 切章清单一律基于 ingest 之后的文件生成：先 ingest 再定行号（`gen-manifest` 就是这样做的） |
| K10 | `klode check` 报 H 类陈旧警告 | 源文件变了 | 复核锚点后 `klode build --stamp`（构建的 klode_build 步已带 `--stamp`） |
| K11 | MCP 工具回 `Source for '' is not installed` | 参数名传错 | MCP 用 `id`，CLI 用 `card` |
| K12 | `search` 两个词 0 命中 | 不加 `--full` 只搜别名和 Thin | 两个词以上一律 `--full`（MCP 里 `full=true`） |
