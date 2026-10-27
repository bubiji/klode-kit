---
name: kb-ocr
description: 把一个文件夹的截图或拍照单据（或一份 PDF）OCR 成带像素框的逐行文字：本机 Vision 与 MLX 两路，加上 Claude、Codex 两路云端路（云端路只发打码图）；有文字层的 PDF 自动分流，不走 OCR。用户说「这几张拍的单子先 OCR」「四路读一下」「这份指南 PDF 建个库」（先 OCR，再走 /kb-build）「2025 年那批重新 OCR 一遍」，或输入 /klode-kit:kb-ocr 时使用。
argument-hint: --batch <收件箱目录或 PDF> [--routes vision,mlx,claude,codex] [--type lab 或 rx] [--redo]
allowed-tools: Bash(*/bin/klode-kit *)
---

# /klode-kit:kb-ocr — 一批图 / PDF → 逐行文字（四路）

这个 skill 只做「解析参数 → 调命令 → 读回执 → 汇报」。所有逻辑在 `klode-kit` 里，不要自己写脚本、不要拼 shell 管道。
在数据项目目录里运行（当前目录就是数据项目；别处运行加 `--project DIR`）。

## 步骤

1. 确认用户给了 `--batch`：收件箱目录，或一份 PDF。没有就问，不要猜路径。
2. 运行（参数原样传入）：

```
${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-ocr $ARGUMENTS
```

3. 读回执 `work/<批次号>/receipt.json`（批次号 = `work/` 下这次出现的 12 位十六进制目录名），按下面的清单汇报：
   - 每页 `rotate`（歪斜页转正了多少度）、`status`（`done`、`incomplete` 或 `failed`）；
   - 每路 `route_stats`：耗时 `elapsed_ms` 与失败页数 `failed_pages`；`issues` 里列出的每一项都要说；
   - PDF：`text_layer`（`true` 表示整份有文字层）与 `routes_skipped`（文字层页不走 OCR，四路都被跳过）；
   - 请求了云端路时，`kb-ocr` 会在云端路之前自动打码（批次回执 `mask.status`：`done` 或 `failed`）；云端路只读 `_masked/` 下登记过的打码图，汇报里说明打码图在哪、打码对照图（`_mask_review/`）只给人看。只跑本机路（`--routes vision,mlx`）不打码。
4. 告诉用户下一步：单据批次走 `/klode-kit:kb-audit --batch <批次号>`（对齐、投票、出确认页）；文字层 PDF 想建库走 `/klode-kit:kb-build`。

重跑（例如「2025 年那批重新 OCR 一遍」）：同一个 `--batch` 再跑一次，加 `--redo` 才会强制开新一轮尝试；
新读数落在新指纹目录 `ocr/<路>/<指纹>/`，旧目录不动，旧的 `verified` 记录保持原状，直到新的确认页点选完成。
要指定单据类型就带 `--type lab`（检验）或 `--type rx`（处方），它写进批次回执，之后的记录文件名按它归类；
重跑不带 `--type` 会保留已写的类型。

## 退出码

- 0 跑完（有的路、有的页没读出来不算失败，看回执的 `issues`）。
- 2 用法错误：参数不对、目录不存在、里面没有图片或 PDF（这时不会建任何目录）。
- 40 配置错误或项目锁被另一个运行占着；缺 poppler 也是 40，信息里点名缺什么。
- 50 云端预算用完，批次停在已读到的页。
- 60 发送器因隐私拒绝了某张图。
- 61 真实调用闸门（G2）没开，或隔离回执不合格。**不要自己设置任何环境变量去绕过**；
  把原话告诉用户：云端路是需要他明确授权的真实调用，本机两路不受影响。

## 已知坑

| 编号 | 现象 | 原因 | 处理 |
| --- | --- | --- | --- |
| O01 | 云端路（claude、codex）整路 `route_failed`，信息含 `isolation_refused` 或 exit 61 | 真实调用闸门没开，或还没有隔离回执 | 不要绕过；告诉用户需要他授权；本机 vision、mlx 两路照常出结果 |
| O02 | 云端路 `route_failed`，信息含 `masking failed`，批次回执 `mask.status` 为 `failed` | 云端路只发 `_masked/` 里登记过的打码图；打码本身没做成（多为 Apple Vision 不可用或图读不了），所以两路云端一张都没发 | 按信息里的原因修好再重跑（已打好的页不会重打）；绝不把原图路径直接交给云端路 |
| O03 | 某一路 `route_unavailable` | 本机引擎没装全：ocrmac、MLX 权重、tesseract 语言包 | 跑 doctor 看缺项和修复命令；其余路的结果照常可用，这不是整批失败 |
| O04 | 重跑后读数没变 | 输入和配置没变时复用已完成的尝试 | 想强制开新一轮就加 `--redo`；旧指纹目录不要手动删 |
| O05 | exit 2，提示没有图片 | 路径写错，或目录里没有图片和 PDF | 核对路径；非图片文件会被跳过并列在回执的 `skipped` 里 |
| O06 | 医疗批次的记录落在 `-doc` 类型下 | 页面没有「检验」「处方」之类抬头关键词，类型判不出 | 第一次就带 `--type lab` 或 `--type rx`，类型会一直保留 |
