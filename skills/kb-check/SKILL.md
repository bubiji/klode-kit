---
name: kb-check
description: 整库检查一个已构建的 klode 知识库并打印检查回执：构建回执、清单、写卡回执、卡片格式与锚点原文、strict、书桌与留出集、MCP 冒烟，以及 plugin 的 name、version、entry、root_sha。用户说「检查一下库过不过」「这个库能发布吗」，或输入 /klode-kit:kb-check 时使用。
argument-hint: --kb <kb/库名> [--build <构建号>]
allowed-tools: Bash(*/bin/klode-kit *)
---

# /klode-kit:kb-check — 整库检查

## 检查回执

!`${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-check $ARGUMENTS --entry skill:/klode-kit:kb-check`

## 你的任务

上面这一块是命令在 skill 渲染时跑出来的检查回执，同一份已经写进了构建目录的 `check_receipt.json`。检查的是 `builds/<构建号>/`
（不给 `--build` 就取最新一个），不看 `current`；它只写这一个文件，不改库里的任何内容。

1. 先**原样**回复回执 JSON，不增不减。如果上面是错误信息而不是 JSON，就原样回复错误信息。
2. 再用不超过 5 行汇报：`status`；每个 FAIL 行的 `name` 和 `detail`；`u5_ready`；`plugin` 块里的 name、version、entry。
   FAIL 行的 detail 前缀说明该走哪条回路：`L1:` 书桌路由（补线索词），`L2:` 卡片内容（重派写卡），`L3:` 锚点（`fix-anchors`），
   `40:` 环境问题（缺 klode 或 klode-mcp，先跑 doctor）。
3. 不要自己去改库里的文件。要修，走 `/klode-kit:kb-build`（书籍路径可以加 `--loops`）；发布要用户另行同意。

命令的退出码（回执里的 status 对应它）：0 全部通过；20 有检查项 FAIL；30 没有构建，或构建清单与文件对不上；
40 缺 klode 或 klode-mcp；50 问题与上一个构建的一模一样，回路没有进展，停下来问用户。

## 已知坑

| 编号 | 现象 | 原因 | 处理 |
| --- | --- | --- | --- |
| K04 | 卡片检查行 FAIL，detail 里有 `AMBIGUOUS` | 锚点片段在章里出现多次 | 把片段加长到唯一，或在锚点后加 `#2`；走 `/klode-kit:kb-build` 的回路重派，不要改封存的构建 |
| K05 | 锚点检查行 FAIL，detail 里有 `NOT FOUND` | 引号、破折号、大小写或空格与原文不同 | `fix-anchors` 自动换成等价字符；剩下的核对原句后逐字复制 |
| K10 | strict 行 FAIL，有 H 类陈旧警告 | 源文件变了 | 复核锚点后 `klode build --stamp`，重新构建 |
| K11 | MCP 冒烟行 FAIL，工具回 `Source for '' is not installed` | 参数名传错 | MCP 用 `id`，CLI 用 `card` |
| K12 | 冒烟里两个词的搜索 0 命中 | 不加 `--full` 只搜别名和 Thin | 两个词以上一律用 `--full`（MCP 里 `full=true`） |
| C01 | status 是 PASS 但 `u5_ready` 为 false | u5_ready 还要宿主 MCP 连上，且探针读到的就是这个构建 | 发布之后跑 `u5-probe`；没发布时 false 是正常的 |
