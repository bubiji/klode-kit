---
name: med-intake
description: 把住院期或门诊的化验单、处方截图当天入库：登记单据类型（检验或处方）、日期和来源，依次跑四路 OCR 和审计，停在待确认（awaiting_confirm），打印「ocr: 3 页 × 4 路 OK」「audit: 5 条分歧 → confirm/20261022.html」；人在确认页点选之后，再写回成 verified 记录。用户说「今天的截图入库」「把 _inbox 里的单子收进去」，或输入 /klode-kit:med-intake 时使用。
argument-hint: --inbox <收件箱目录> [--type lab 或 rx]
allowed-tools: Bash(*/bin/klode-kit *)
---

# /klode-kit:med-intake — 当天的截图入库（两段式）

这个 skill 只调命令、读输出、汇报；不自己读图、不自己判断数值。在数据项目目录里运行（别处运行加 `--project DIR`）。
单据是医疗隐私数据：云端两路只收到打码后的图；汇报里只说页数、条数和文件位置，**不要在对话里复述姓名、证件号或具体数值**。

## 第一段：登记、OCR、审计，停在待确认

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit med-intake --inbox INBOX_DIR
```

`INBOX_DIR` 是放截图的目录（例如 `_inbox/2026-10-22/`）。要指定类型就加 `--type lab`（检验）或 `--type rx`（处方）。
命令依次登记单据类型、日期和来源，跑 OCR，再跑审计。标准输出是三行，原样转述给用户：

- `ocr: 3 页 × 4 路 OK`（某路某页失败时会点名，例如 `1 处失败（codex:p002）`；PDF 页自带文字层时不读图，写成 `ocr: 13 页文字层，跳过 OCR`，**跳过不是失败**）；
- `audit: 5 条分歧 → confirm/20261022.html`；
- `status: awaiting_confirm`。

退出码：

- **退出码 10（exit 10）**：`awaiting_confirm`，有分歧等人点选，继续下面的步骤。
- 0：`nothing_to_confirm`，四路全部一致，没有确认页。直接做第二段，**不带 `--choices`**，让记录入账。
- 2：用法错误。目录里没有图片，或者类型判不出来（页面没有「检验」「处方」之类抬头，或各页类型不一致）。
  这时 OCR 已经做完、审计还没跑：问用户这批是检验还是处方，加 `--type` 重跑，OCR 结果会复用。
- **20**：这批有页，审计却一条记录都没产出（M08）。**不是「无需确认」**，什么都没入库：把输出里的原话告诉用户，不要进第二段。
- 40：环境、配置或锁（另一个入库在跑，或体检缺项），输出里有修复命令；50、60、61 是预算、隐私、真实调用闸门（G2），
  **不要自己设置环境变量绕过 61**，把原话告诉用户。

分歧页以日期命名：别名 = 收件箱路径里的日期（`20261022`）；同一天的第二批叫 `20261022-2`，不会覆盖前一批。
请用户打开 **`confirm/<别名>/confirm.html`**（文件夹入口，分歧小图能显示），**不要**打开 `confirm/<别名>.html` 那个链接（小图会裂）。
每条分歧：选一路的值、选「都不对 / 手输」，或标「不是字段」；tesseract 的读数选不了。点完导出 `choices.json`，把文件位置告诉你。

## 第二段：写回（用户说「分歧我点完了」之后）

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit pipeline --batch INBOX_DIR --choices choices.json --stage writeback
```

`INBOX_DIR` 与第一段是同一个目录；第一段带过 `--type` 的，这里原样再带一次。**必须带 `--stage writeback`**：流水线默认一路跑到建库和发布，这里只做写回。
写回顺序是：先落点选、再入账其余已一致的候选、最后重建 `records/` 与 `verified/` 视图。输出最后是一行 JSON（批次号、各阶段、退出码）。

- 0 全部 verified；10 还有记录在等（比如留了「都不对」没手输，那条是 `disputed`，不会进构建输入）。
- 30：这份 `choices.json` 被**整份拒绝，零写入**，原因在输出里。不要手改，让用户用当前最新的确认页重新点选、重新导出。
- 汇报：新增或更新的文件 `records/<日期>-<类型>.md`、`verified/<日期>-<类型>.md`，各条记录的状态。

之后想把核对过的记录建成库，走 `/klode-kit:kb-build`；某个字段读错了，走 `/klode-kit:kb-audit` 的重开。发布到用户全局目录要用户另行同意。
备份方案只写在 `docs/med-intake.md` 里，这个 skill 不做任何备份动作。

## 已知坑

| 编号 | 现象 | 原因 | 处理 |
| --- | --- | --- | --- |
| M01 | exit 2，提示给 `--type` | 抬头关键词缺失，或各页判出的类型不一致 | 问用户检验还是处方，加 `--type lab` 或 `--type rx` 重跑；类型会一直保留 |
| M02 | 确认页小图是裂的 | 小图用相对路径，经 `confirm/<别名>.html` 链接打开会找错目录 | 打开 `confirm/<别名>/confirm.html` |
| M03 | 第二段命令把库也建了、发布了 | `pipeline` 不带 `--stage` 时跑全部阶段 | 第二段必须带 `--stage writeback` |
| M04 | 写回 exit 30，什么都没写 | choices 与当前确认页对不上（批次、审计修订、封存码），或点了 tesseract | 用当前确认页重新点选并导出 |
| M05 | 同一天的新批次别名不是日期 | 同日第二批叫 `<日期>-2`，不覆盖已有别名 | 用命令输出里打印的别名 |
| M06 | exit 0 且没有确认页链接 | 四路全部一致，无需确认 | 第二段不带 `--choices`，直接写回入账 |
| M07 | 预检 exit 40：`project_trust` 未通过，按提示「在数据项目目录运行 claude」永远不弹信任框 | 数据项目是非 git 普通文件夹且其上级已被信任：Claude Code 不会弹框，该目录自己的 `hasTrustDialogAccepted` 永远写不进去（信任以文件夹/git 根自身为键） | 只用本地路线时加 `--routes vision,mlx`（最后阶段 ≤ writeback），预检会跳过并在回执里写明原因；要用 claude 路线或发布时：对数据项目 `git init` 后在其中运行 `claude` 并接受，或由用户自己在 Claude 的信任存储（`.claude.json`）里加该项目的条目（闸门 G3，klode-kit 不代写） |
| M08 | exit 20，「kb-audit produced 0 records for the N page(s)」 | 有页却没有任何记录：旧版本对带文字层的 PDF 化验单只报「无需确认」（真实试点 P8，静默丢数据）；现在文字层页按「`pdftext` 一路精确读数」审计，仍出现 0 条就一定是真故障，一律停下 | 不要进第二段；保留输出和 `work/<批次>/receipt.json`、`work/<批次>/audit/` 交给开发者查；类型判不出时按 M01 加 `--type`。带文字层的 PDF 第二段也要带同样的 `--type` |
