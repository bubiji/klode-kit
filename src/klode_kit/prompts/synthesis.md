# synthesis — 书桌（frameworks）维度蒸馏的写法（通用版，EPUB / 文字层路线）

一个维度（dimension）= 跨章、跨书回答**消费方的一个实务问题**的蒸馏，例如 `forces-of-progress`（怎么用四力诊断一次购买/放弃）。
文件：`library/frameworks/_syntheses/<dimension>.md`。`klode consult <dimension>` 默认只返回 `## Craft`；`klode diagnose "<一句话>"` 按 `_diagnostics.md` 把问题路由到维度（`_diagnostics.md` 由 `klode-kit desk-build <kb> --apply` 从 `src/desk.json` 生成，不要手改）。

下文的 `klode-kit` 指插件启动器 `${CLAUDE_PLUGIN_ROOT}/bin/klode-kit`；等价的写法是 `python3 -m klode_kit.build.check_synthesis`（派工提示词里用的就是它）。本文件是插件包内的 `klode_kit/prompts/synthesis.md`，`klode-kit desk-build <kb> --prompts` 生成的派工提示词会让 worker 读它。

## 输入
- 本维度要覆盖的卡片 id 清单（由主会话给）；读 `library/cards/<id>.md`（Thin/Full 里的中文要点与原文锚点）
- **不读源 .txt**——书桌是从卡片蒸馏，不是从书蒸馏

## 文件格式（严格；`klode-kit check-synthesis` 机器验）
```
---
dimension: forces-of-progress
title: 四力诊断：推 / 拉 / 焦虑 / 习惯
cards: [jtbd-kl-ch07-forces-of-progress, jtbd-cc-ch05-hear-what-customers-dont-say]
status: draft
tier: 1
---

**Core question:** 一句话：消费方带着什么问题来翻这个维度。
**Status/Tier:** draft / 1（从哪几张卡蒸馏；相邻维度各管什么，边界写清）

## Craft

### 机制（为什么这样做有效）
- 中文说明… (grep: `从卡片复制的原文锚点`)

### 动作（按顺序做什么；每条加粗头 = 将来 gate rubric 的一个 criterion）
- **A1 ascii-slug 中文一句话。** (grep: `…`)
- **A2 ascii-slug 中文一句话。** (grep: `…`)

### 可选项（几条路怎么选）
- 情境 → 选法… (grep: `…`)

## Operational spec
（留空；评测与 rubric 建立后补）

## Provenance
- 来源卡与章；写作日期；未覆盖的内容归哪个维度。
```

## 规则（每条都被机器验）
1. **锚点只能从 `cards:` 列出的卡片里逐字复制**（同一字符串，含 `#N`）；不要重新从源里截——不一致 `klode check` 报漂移。
2. 只引 `cards:` 里的卡；要引新卡先加进 `cards:`。
3. `## Craft` 自足、无引擎术语（不出现 scorers / RT- / ε-test / gate）；每条承重断言带锚点。
4. 「动作」每条 `- **<ASCII开头的加粗头> …**`，加粗头里要有 ASCII（纯中文会让 gate derive 的 slug 塌成同名）。
5. 机制 3–6 条、动作 4–8 条、可选项 2–5 条；一个维度 15–30 个锚点。跨书时注明"X 书说…，Y 书补充…"，两书冲突要如实写出，不调和。
6. 不写卡片里没有的结论；不用你自己对这个主题的通识。

## 工作循环
1. 读本维度全部 `cards:` 卡片 → 写 `_syntheses/<dimension>.md`
2. `klode-kit check-synthesis <kb> <dimension>` → 改到 PASS
3. 回报一行：`<dimension>  PASS  锚点 N  动作 M 条`
