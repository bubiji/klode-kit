# write_card — 从章节源文本写 L1（Thin）/ L2（Full）卡片内容（EPUB / 文字层路线）

用途：给 `cards_content/<id>.md` 写内容，再由 `klode-kit write-card <kb> <id>` 写进 `library/cards/<id>.md` 标记线以下（写入前先跑门禁 1，不过不写）。
本路线**没有页码、没有台账**（源是逐字文本，`klode check` 直接核锚点）。

下文的 `klode-kit` 指插件启动器 `${CLAUDE_PLUGIN_ROOT}/bin/klode-kit`（派工提示词里会给出它的完整路径）。

## 输入
- 源：`<kb>/library/<shelf>/<id>.txt`（整章逐字文本；grep 的就是它）
- 骨架：`klode-kit skeleton <kb> <id>`（小节标题 + 首段）

## 输出格式（严格，`klode-kit check-cards` 机器验）
```
aliases: [術語1, 術語2, …]   ← ≥ 10 个；书里原词为主，补中文别名；klode search 不加 --full 时只搜这里和 Thin

## Thin
一段 3–6 句：这章解决什么问题、给什么结论、什么时候该来翻它。至少 1 个锚点。

## Full

### <小节标题（照抄书里的节名或概括）>
- 要点（中文说明）… (grep: `源文本逐字片段`)
- …
```

## 锚点规则（每条都会被 `klode-kit check-cards` 和 `klode check --strict` 机器验证）
1. `(grep: \`…\`)` 里是**源文本逐字**的片段：≥ 15 字符、在本源 .txt 里**唯一**命中；不改标点、不改引号样式（’ vs '）、不改大小写、不跨行。
2. 同一句在源里出现多次时，加长到唯一；实在要引重复句，用 `(grep: \`…\` #2)` 标第几次。
3. 锚点是**短片段**（一句话以内，通常 15–120 字符），不是整段——卡片是索引，不是摘录。
4. 中文是你的说明，反引号里是原文；说明在前，锚点在后，版式上一眼可分。
5. 不确定原句怎么写就 `grep -n "片段" <源.txt>` 核一下，别凭印象写。

## 内容取舍
- Thin 回答"这章是干什么的"；Full 按小节给"作者的判断 + 依据 + 实务建议"，每个小节 2–6 条，全章 15–40 个锚点。
- 定义、判词、方法步骤、案例的关键转折是高价值锚点，优先锚。
- 前置页 / 目录 / 索引 / 致谢这类源，Thin 一段 + Full 一个小节即可，别硬凑。
- 不写书里没有的东西；不确定的结论不写；不写你自己对这个主题的通识。

## 工作循环
1. `klode-kit skeleton <kb> <id>` 看骨架 → 读源 .txt → 写 `cards_content/<id>.md`
2. `klode-kit check-cards <id> --kb <kb>` → 不是 PASS 就改到 PASS
3. 回报：锚点数、小节数、`check-cards` 结果
