---
name: kb-audit
description: 把 kb-ocr 一批的四路读数逐行对齐、投票、过闸门，生成分歧确认页；人在页面上点选后写回，成为可追溯的 verified 记录；已 verified 但读错的字段可以重开。用户说「分歧我点完了，写回」「2025 年那批重新 OCR 一遍」（OCR 之后接着审计），「这个字段其实读错了，重新确认」，或输入 /klode-kit:kb-audit 时使用。
argument-hint: --batch <批次号> | --writeback <choices.json> | --reopen <字段号> | --again --batch <批次号> | --attribute --batch <批次号> --reviewer-model M --reviewer-rule R | --redate --batch <批次号> [--apply]
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

如果某一路读到了整页没配上任何一行的带数字、日期或药名的原文（常见：页眉页脚旁的零散读数），这一页的这一路只多出**一条**「页面上没配上行的值」（不是每个字段都要点）：列出没配上的原文行（有框就带小图），人点一次「确认无遗漏」；如果其中确实有漏掉的值（这些行有框时），可以「手输」。只要这一条没处理，这条记录就不会变 verified，其余一致的字段不受影响。

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

## 五、第二轮确认（第一次写回后还有字段没定）

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-audit --again --batch BATCH_ID
```

第一次写回留了「都不对」的记录变成 `disputed`，原确认页对它已过期（再导出会被拒：`rev_base … (stale page)`）。这条命令为本批所有
`pending_confirm` / `disputed` 记录生成一页新的确认页（`a<N>/again/g<NNNN>.html`，回执里有路径），只列还没定的字段，按记录当前修订号。
退出码 10 表示有页可点（`opened` 新页，或 `unchanged` 沿用上一页）；退出码 0、`nothing` 表示本批没有待定字段（已 verified 的字段读错用第四节）。
让用户在新页上点选、导出 `choices.json`，再按第三节写回；仍留「都不对」的字段下次再跑一遍本命令即可。退出码 30 表示不能做（本批没有审计修订，或最新审计修订还没写回，先做第三节的写回）。

## 六、标注 AI 审核员的决定（reviewer）

由审核员代理（sonnet / opus，D-43）替本人做确认页选择时，在 `choices.json` 顶层加 `"reviewer": {"model": "<模型号>", "rule": "<规则名>"}`（各 1..64 字符，多一个键、缺一个键、空值或超长都整份拒绝，零写入）。
写回时这份文件里的每条决定记为 `by: "reviewer"`，并带 `evidence.reviewer`；新修订的 `verified_by` 在**它所依赖的任一人工决定**是审核员做的时为 `reviewer`（含之前几次写回留下的、重开时沿用的），全是本人做的才是 `human`，没人决定的仍是 `auto`。
审核员的决定和本人的一样算「已定」：`--again` 不会再列，页面项规则同样适用。不写 `reviewer` 就是本人，行为和以前完全一样。

以前（`reviewer` 出现之前）审核员做的决定被记成了 `human`，用回填补标：

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-audit --attribute --batch BATCH_ID --reviewer-model MODEL --reviewer-rule RULE
```

只追加一行到 `work/<批次>/audit/attributions.jsonl`（本批每个审计修订里、含 `by: human` 决定且已结束的提交），从不改 `fixes.jsonl`、`commits.jsonl`、记录正文或 `_rev/`。回执 `klode-kit.attribute_receipt/1`：`attributed`（退出码 0，含提交数和记录数）、`nothing`（已全部标过，退出码 0，不写任何东西）、`refused`（退出码 30：批次号不对、没有 `work/`、没有审计修订、缺 `--reviewer-model` 或 `--reviewer-rule`）。
回填之后再有本人在审核员的提交上继续写回，新修订的 `verified_by` 仍是 `reviewer`。导出的 `library/records/<记录>.txt` 每条记录末尾多一行 `verified_by: auto | human | reviewer (<模型>)`。

### 补改日期（`--redate`）

记录日期以前在没有「日期」行的页（日文检验单写「採取日」「検査日」等）上取页面里最早的日期，结果就是 `生年月日`。规则已改：`DATE_LABELS`（採取日、検査日、受付日、報告日、采样、报告时间……）标出的行优先；`生年月日` / `出生日期` / `DOB` / `Birth` 等行上的日期（以及单独一行出生标签的下一行）永远不当记录日期。已写错的记录日期不会自己改回来（重做不改视图名），用补改：

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-audit --redate --batch BATCH_ID            # 只列出，什么都不写
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-audit --redate --batch BATCH_ID --apply    # 写入
```

按本批最新审计修订的各路读数重新推导每条记录（任何状态）的日期，和 `records/_by_id` 里的日期不同的才列出。`--apply`：往 `work/<批次>/audit/redate.jsonl` 追加一行 `{op: redate, rec_id, old, new, source: page|inbox_dir|batch, at}`，改 `_by_id` 的 `date`，重建视图（`records/`、`verified/` 的文件名跟着新日期）；`_rev/` 里的修订正文不改（只追加的存储）。之后的写回把新日期写进新修订；`records_to_txt` 导出有 `redate` 事件的记录时用 `_by_id` 的日期。幂等：再跑一次是 `nothing`。回执 `klode-kit.redate_receipt/1`：`planned` / `applied` / `nothing`（退出码 0）、`refused`（退出码 30：批次号不对、没有 `work/`、没有审计修订；40 是项目锁被占）。不能和 `--again`、`--reopen`、`--writeback`、`--attribute` 同用（退出码 2）。

## 已知坑

| 编号 | 现象 | 原因 | 处理 |
| --- | --- | --- | --- |
| A01 | 写回 exit 30，什么都没写 | 整份被拒：批次、审计修订、指纹或确认页封存码对不上（常见于重跑 OCR 之后还用旧页面导出的 choices），或点了 tesseract、漏了待确认的字段 | 用当前最新的确认页重新点选并导出；不要手改 choices.json |
| A02 | 候选里有 tesseract 的值却选不了 | tesseract 只做几何，不投票，其读数不得作为点选结果 | 选其余三路之一，或手输 |
| A03 | 留了「都不对」没手输，记录是 `disputed` | 设计如此：disputed 记录不进构建输入；原确认页对它已过期 | 想补就用第五节 `--again` 生成第二轮确认页（`--reopen` 只收 verified 字段） |
| A04 | 四路读成同一个错值，自动 verified | 一致不等于正确 | 用户发现后用 `--reopen` 放回确认页手输；旧修订保留 |
| A05 | 确认页里的小图是裂的 | 小图用相对路径，经 `confirm/<别名>.html` 链接打开会找错目录 | 打开 `confirm/<别名>/confirm.html` |
| A06 | `kb-advice` 被拒绝（exit 30） | 确认页已经生成并封存，建议要在它之前 | 只能在生成确认页之前请求建议；经流水线或 med-intake 进来的批次已经生成页面，不再请求 |
| A07 | 写回后 `records/` 视图没变 | 视图不会随写回自动重建 | 写回之后运行第三节最后一条 views |
