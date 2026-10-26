---
name: kb-audit
description: 把 kb-ocr 一批的四路读数逐行对齐、投票、过闸门，生成分歧确认页；人在页面上点选后写回，成为可追溯的 verified 记录；已 verified 但读错的字段可以重开。用户说「分歧我点完了，写回」「2025 年那批重新 OCR 一遍」（OCR 之后接着审计），「这个字段其实读错了，重新确认」，或输入 /klode-kit:kb-audit 时使用。
argument-hint: --batch <批次号> | --writeback <choices.json> | --reopen <字段号>
allowed-tools: Bash(*/bin/klode-kit *)
---

# /klode-kit:kb-audit — 对齐投票、确认页、写回、重开

这个 skill 只调命令、读回执、汇报；不自己比对读数、不自己改记录。所有判断在 `klode-kit` 里。
在数据项目目录里运行（当前目录就是数据项目；别处运行加 `--project DIR`）。批次号是 `work/` 下 12 位十六进制的目录名（`/klode-kit:kb-ocr` 汇报过）。

## 一、对齐与投票（出审计修订 a<N>）

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-audit --batch BATCH_ID
```

- 退出码 0：每条候选记录都 verified，没有需要人点的。
- 退出码 10：有候选在等人确认（`pending_confirm` 或 `disputed`），继续下一节。
- 2 批次号不对或没有 kb-ocr 回执（先跑 `/klode-kit:kb-ocr`）；30 输入对不上（换过批次、文件缺失）；40 项目锁被占。
- 汇报：审计修订号（新建还是复用）、每页的状态与待确认条数。四路读数没有变化时复用旧修订，不会重写。

## 二、确认页

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit confirm-page --batch BATCH_ID
```

页面落在 `work/<批次号>/audit/a<N>/confirm.html`，写一次、只读。从 `/klode-kit:med-intake` 或流水线进来的批次，页面已经生成并链接好：
**打开 `confirm/<别名>/confirm.html`（文件夹入口，小图能显示）**，不要打开 `confirm/<别名>.html` 那个链接（小图会裂）。

页面上每条分歧是：分歧小图、四路候选值、★ 推荐值（多数票，只是建议）。人对每条选一路的值、选「都不对 / 手输」，或标「不是字段」，然后导出 `choices.json`。
tesseract 只做版面几何、不投票，它的读数选不了。

可选的 kb-verifier：只在用户明确要求「让校对员先看一遍」时才跑，**不要自己主动跑**。它把分歧小图交给云端模型看，只产出给确认页看的建议（`advice.json`），不改任何记录和状态；要在确认页生成之前跑，生成后就封存拒绝：

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-advice --batch BATCH_ID --run
```

退出码 61 表示真实调用闸门（G2）没开：**不要自己设置环境变量绕过**，把原话告诉用户，由他决定是否授权。跑完再运行本节开头的 `confirm-page` 命令生成确认页，它会自动带上建议。

## 三、写回（用户说「分歧我点完了」之后）

用户把导出的 `choices.json` 交给你，依次运行（与流水线写回阶段同一顺序：先点选、再入账、最后重建视图）：

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-audit --batch BATCH_ID --writeback choices.json
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-audit --batch BATCH_ID --writeback
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit views
```

- 退出码 0 全部 verified；10 还有记录在等（比如留了「都不对」没手输，那条是 `disputed`，不会进构建输入）。
- 退出码 30：这份 choices 被**整份拒绝，零写入**，原因逐条打在输出里。不要手改 `choices.json`，让用户用当前最新的确认页重新点选、重新导出。
- 汇报：每条记录的新修订号与状态，视图文件 `records/<日期>-<类型>.md`、`verified/<日期>-<类型>.md` 的变化。

## 四、重开一个已 verified 的字段（四路同错被自动通过时）

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-audit --reopen FIELD_ID --reason 原因
```

只在用户明确说某个字段读错了、并给出字段号时才做。命令把该字段放回一个只有这一条的确认页（`a<N>/reopen/o<NNNN>.html`，回执里有路径），
退出码 10 表示已重开。让用户在页面上选一路或手输正确值（「都不对」和空白手输会被拒绝），导出 `choices.json`，再按第三节写回。
新值成为新修订生效，旧修订保留，全程留痕。退出码 30 表示不能重开（字段没有 verified 的有效记录，或审计修订落后于记录，先做第三节的写回）。

## 已知坑

| 编号 | 现象 | 原因 | 处理 |
| --- | --- | --- | --- |
| A01 | 写回 exit 30，什么都没写 | 整份被拒：批次、审计修订、指纹或确认页封存码对不上（常见于重跑 OCR 之后还用旧页面导出的 choices），或点了 tesseract、漏了待确认的字段 | 用当前最新的确认页重新点选并导出；不要手改 choices.json |
| A02 | 候选里有 tesseract 的值却选不了 | tesseract 只做几何，不投票，其读数不得作为点选结果 | 选其余三路之一，或手输 |
| A03 | 留了「都不对」没手输，记录是 `disputed` | 设计如此：disputed 记录不进构建输入 | 想补就让用户再点一次手输，或用 `--reopen` |
| A04 | 四路读成同一个错值，自动 verified | 一致不等于正确 | 用户发现后用 `--reopen` 放回确认页手输；旧修订保留 |
| A05 | 确认页里的小图是裂的 | 小图用相对路径，经 `confirm/<别名>.html` 链接打开会找错目录 | 打开 `confirm/<别名>/confirm.html` |
| A06 | `kb-advice` 被拒绝（exit 30） | 确认页已经生成并封存，建议要在它之前 | 只能在生成确认页之前请求建议；经流水线或 med-intake 进来的批次已经生成页面，不再请求 |
| A07 | 写回后 `records/` 视图没变 | 视图不会随写回自动重建 | 写回之后运行第三节最后一条 views |
