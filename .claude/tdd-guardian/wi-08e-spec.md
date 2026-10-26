# WI-08e 规格：`kb-audit --reopen <field_id>`（重开字段）

来源：impl-deviations.md「新增 WI-08e（用户 2026-10-24 批准）」「待评估：item_label 不在 `_rev` 正文里」；设计 02 r3.1「四路 10.5、人工改 1.05」、r4.1 双层记录、04 U5。
本文件是 WI-08e 的计划条目 + 测试矩阵（计划 v8 / 矩阵 v8 无 08e，由本文件补）。实现模型 Sonnet 5.5。

## 1. 问题与目标

四路 OCR 同读错（例 `10.5`，图上是 `1.05`）→ 四路一致 → `verified(auto)` → 写回成 effective 修订。确认页只列待确认字段，已 verified 的字段没有任何公开入口可更正（`tests/cli/test_records_to_txt_klode.py` 只能手写磁盘格式冒充人工改动）。

目标：`kb-audit --reopen <field_id>` 把已 verified 字段放回确认页 → 人手输（或点选/删除）→ 经**既有写回**提交 → 新 rev 生效、旧 rev 原样保留、全程留痕；下游导出、视图、来源链校验器、U5 全部跟随。

## 2. 公开接口

```
klode-kit kb-audit --reopen <field_id> [--project DIR] [--batch ID] [--reason TEXT]
```

- 与 `--writeback` 互斥（同时给 → 用法错 exit 2）。
- stdout 一行 JSON 回执 `klode-kit.reopen_receipt/1`（代码内定义，同 writeback 回执的做法）：
  `{schema, result: opened|unchanged|refused, exit_code, field_id, rec_id, batch_id, audit_rev, reopen_id, rev_base, effective_rev, page, confirm_sha256, error?}`；`page` 为相对项目根的路径。
- 退出码：**10** 已重开（或已有未决重开，`unchanged`），该字段等待人工确认（与 kb-audit「有待确认」同义）；**2** 用法（field_id 格式不对、`--reopen` 与 `--writeback` 同给、`--batch` 非法）；**30** 拒绝、零写入（record/字段不存在、记录无 effective、有更新的未 verified 候选、最新审计修订未入账、`--batch` 与记录所在批次不符等）；**40** 项目锁 `work/.lock` 被占。
- 之后：`confirm_export`（node 或 `confirm_page.export_choices`）导出 choices → `kb-audit --writeback choices.json`（既有入口）→ `klode-kit views`。流水线 `pipeline --choices` 同样可用（不改 pipeline 代码）。

## 3. 数据形态（全部为增量）

| 产物 | 位置 | 性质 |
|---|---|---|
| 重开日志 | `work/<batch>/audit/a<M>/reopen.jsonl` | **只追加**。行 `{op:"reopen", reopen_id:"<audit_rev>/o<NNNN>", field_id, rec_id, audit_rev, rev_base, effective_rev, value (现值，被删字段为 null), dropped:bool, reason:str\|null, by:"human", at, page:"reopen/o<NNNN>.html"}` |
| 重开确认页 | `work/<batch>/audit/a<M>/reopen/o<NNNN>.html` | **写一次**（tmp→rename→只读），与 `confirm.html` 同模板同封存戳；只含该记录该字段一项；数据块多可选键 `reopen:{reopen_id, field_id, value}`；`rev_base` = 记录当前 rev |
| 决定行 | `a<M>/fixes.jsonl` | 重开提交产生的决定多可选顶层键 `reopen_id`（**fixes.json schema 增量**，pattern `^[0-9a-f]{12}/a[0-9]+/o[0-9]{4}$`） |
| `_rev` 正文 | `records/_rev/<rec>.r<N>.md` | 重开提交的正文多可选键 `reopened: <reopen_id>`（正文无 schema；views / records_to_txt 只读已知键） |

`a<M>` = 该记录所在批次**最新**审计修订，且必须等于记录最新候选的 `audit_rev`（否则先 `kb-audit --writeback` 入账，避免重开提交的 `audit_rev` 盖住尚未入账的更新修订）。

## 4. 规则

1. **可重开**：记录有 `effective`（verified），其 rev 即记录当前最高 rev（没有更新的未决候选），字段在 effective 正文的 `fields` 或 `dropped` 里，字段在 `a<M>` 的 `cells.jsonl` 与候选里存在。否则 exit 30，零写入。
2. **重开不改生效值**：只追加 `reopen.jsonl` 一行并写页；`records/` 一字节不动；effective 仍是旧 rev，导出/视图/旧 build 不变，直到人工提交。
3. **重复重开**：同字段已有未决重开（`rev_base == 记录当前 rev`）→ 不追加日志、不写新页，回执 `unchanged`、exit 10。提交之后（rev 前进）再重开 → 新事件 `o<NNNN+1>`、新页，旧事件/旧页保留。
4. **提交走既有写回**：choices 的 `confirm_sha256` 对上 `a<M>/reopen/o*.html` 之一（而非 `confirm.html`）即进入重开模式；`batch_id/audit_rev/fingerprints/rev_base/cell/seen` 全部沿用既有校验；待确认字段集 = 该页数据块里的字段（不是候选的分歧字段）。
5. **重开模式的选择**：`route`（须有值的投票路，tesseract 仍拒）、`manual`（须 region，沿用）、`none`+`not_a_field`（删除）可提交；**纯 `none`（都不对）拒绝**（整份记录拒、零写入）——已 verified 字段不能被「悬空」，也避免出现无法再重开的未决候选。
6. **新 rev**：`r<current+1>`，`status verified`、`verified_by human`、`audit_rev` 同 `a<M>`；`fields` = effective 正文 `fields`（去掉被重开字段）+ 新值；`dropped` = effective 的 `dropped`（去掉被重开且已改值的）+ 新删除的；只为被重开字段写一条人工决定（其余字段沿用各自原有效决定，来源链「生效决定取 rev_new ≤ R 中最大者」不变）；旧候选 `superseded_by`、`effective` 前移，沿用 `_plan` 的 r4.1 晋升。旧 `_rev` 正文不被改写（只读 0444，字节不变）。
7. **提交协议不变**：begin（整份计划）→ fixes → `_rev` → `_by_id` → end；启动先 roll-forward 未完成提交；同一项目锁 `work/.lock`。
8. **重开自身崩溃安全**：锁 → 追加事件行 → 建 `reopen/` → 页 tmp → rename → chmod；每段至多一次持久写；重跑补齐缺页（事件已记则不重复记）。崩溃点 `reopen.logged / reopen.mkdir / reopen.page_tmp / reopen.page_renamed / reopen.page_written`（锁沿用 `writeback.lock`）。
9. **留痕链**：`reopen.jsonl` 事件 →（`reopen_id`）→ `fixes.jsonl` 决定 →（`commit_id`）→ `commits.jsonl` begin/end → `_rev` 正文（`reopened`）→ `writeback_receipts.jsonl`。所有文件只追加或写一次。
10. **下游**：`records_to_txt` 取 `effective.rev` 正文的值（导出行、`export_map.rev`、`value_audited`、`audit_bindings`）；item 名仍经正文 `audit_rev` 的 `candidates.jsonl` 取（见 §6）；`views` 的 symlink 随 `effective.rev` 指向新正文；`contract/provenance.validate` 对新旧链 0 违例；旧 build（绑定旧 rev）仍 0 违例（版本绑定）。

## 5. 已知限制（记录，不在本 WI 解决）

- 重开日期字段只改 `fields[...]`，不改正文的 `date` / 视图文件名（既有写回对人工改日期字段同样如此，`date` 来自页元数据）。
- 重开提交不重新判断同页其他字段（沿用旧 rev 的值），只替换被重开字段。

## 6. 结论：item_label 不在 `_rev` 正文里（评估）

- 现状：records_to_txt 经正文 `audit_rev` 的 `a<N>/candidates.jsonl`（写一次、只读、永不改写）取 `item_label`。
- 是否把 label 存进正文：好处是导出不依赖 candidates.jsonl；代价是改 `_plan` 的正文形态，所有既有写回/视图/导出测试里对正文键集合、字节的断言都受影响（含逐字节对比崩溃重跑的参考结果），而当前依赖是稳定的（该文件写一次、只读、与正文同批次）。
- **结论：保持现状**。重开不引入新问题：新 rev 的 `audit_rev` 与被重开字段所在的 `a<M>` 相同，且重开前已校验字段在 `a<M>` 候选里存在，所以新正文的 label 查得到（08e-13 钉）。若将来允许跨审计修订搬运字段，再回头把 label 写进正文（可选键，旧正文缺省回退到 candidates.jsonl）。

## 7. 契约增量清单

1. `schemas/fixes.json`：可选顶层 `reopen_id`。
2. 重开确认页数据块：可选键 `reopen`（`klode-kit.confirm_page/1` 未变，多一个可选键）。
3. `_rev` 正文：可选键 `reopened`。
4. 新文件：`reopen.jsonl`、`reopen/o*.html`；新回执 schema 名 `klode-kit.reopen_receipt/1`（代码内）。
5. `kb-audit` 新选项 `--reopen / --reason`（`--batch` 对 reopen 为可选校验项）。

## 8. 矩阵

**Law**（单元整体）：① 重开+提交是保全变换：除被重开字段外 effective 值不变，旧 rev 正文/旧日志行字节不变（只追加）；② 重开幂等；③ 往返：任意合法手输值 v 提交后 `effective.fields[f] == v`，且导出行含 v；④ 来源链校验器对任何重开链 0 违例。故含 S4–S5 用例（08e-21/22/23）。

| 行 | 内容 | 类别 | 规格级 | Lane |
|---|---|---|---|---|
| 08e-01 | 重开已 verified(auto) 字段：exit 10，回执 opened，事件行字段齐全，页存在且只读；`records/` 全树字节不变，effective 仍 r1 | success | S1 | unit |
| 08e-02 | 确认页含该字段：数据块一条记录一个字段，四路读数 seen，`reopen` 块，rev_base=1，sha 封存戳有效；HTML 有该字段的 fieldset 与三类选项；`export_choices` 可导出 | success | S1 | unit |
| 08e-03 | 不存在的 field_id：格式合法但无此 record → 30；record 在、slot 不在 → 30；格式不合法 → 2；均零写入（树 sha 不变） | guard | S3 | unit |
| 08e-04 | 未 verified 字段：记录仅 pending_confirm（无 effective）→ 30 零写入；有 effective 但有更新的未决候选 → 30；最新审计修订未入账 → 30；被删/非本记录字段 → 30 | guard | S3 | unit |
| 08e-05 | 已删（not_a_field）字段可重开；手输后进 `fields`、出 `dropped` | state | S2 | unit |
| 08e-06 | 重复重开：第二次 unchanged、exit 10、`reopen.jsonl` 与页字节不变、树不变；提交后再重开得 `o0002`，旧页旧行保留 | state | S5 | unit |
| 08e-07 | 同记录重开两个字段：两事件两页；先提交页1 → rev2；页2 此时 stale（30、零写入）；重新重开得 `o0003`（rev_base 2）→ 提交 → rev3，两处新值都在 | state | S5 | unit |
| 08e-08 | 手输 `11.9` 提交：exit 0；front：effective r2 human、候选 r1.superseded_by=2；r2 正文除被重开字段外 == r1 的 `fields`，`reopened` 指事件；r1 正文字节不变且 0444；决定行 by human/manual/value、rev_base 1/rev_new 2、evidence.region+seen(四路)+confirm_sha256==页戳、`reopen_id`；commits begin/end 成对 | success | S1 | unit |
| 08e-09 | 重开模式的选择：route（同值）→ r2 值不变、决定 route；纯 none → 30 零写入；none+not_a_field → r2 的 `dropped` 含该字段、`fields` 无；tesseract 点选仍 30 | guard | S3 | unit |
| 08e-10 | 留痕可审计：从 `reopen.jsonl` 的事件出发能无歧义走到决定、commit、正文、回执；所有文件在提交前后只追加（旧内容是新内容的前缀）或不变 | state | S5 | unit |
| 08e-11 | 来源链：重开后、提交后各导出一次并 `provenance.validate` = 0 违例；旧 build（绑 r1）在 r2 生效后仍 0 违例；篡改决定值 → 报 `record_value_mismatch`（校验非空转） | property | S5 | contract |
| 08e-12 | 崩溃安全：写回 11 个崩溃点逐点 SIGKILL 后重跑 == 未中断的结果；reopen 5 个崩溃点逐点 SIGKILL 后重跑 == 未中断的结果（事件行不重复）；分段 ≤1 次持久写、崩溃点集合 == 清单；随机时刻 SIGKILL（hypothesis） | failure | S5 | contract |
| 08e-13 | 导出取新 rev：`export_map.rev==2`、`value_audited=="11.9"`、`audit_bindings.rev==2`、导出行含 `11.9` 不含 `119`、项目名（血红蛋白）仍解析；仅重开未提交时导出仍是 r1 | success | S1 | unit |
| 08e-14 | 视图：重开不动 symlink；提交后 `views` 的 `records/` 与 `verified/` 链接指向 r2 正文，r1 正文仍在 | success | S1 | unit |
| 08e-15 | 隔离/并发：锁被占 → reopen 与重开提交都 exit 40 零写入；两个进程同时重开同字段 → 恰好一条事件行、两个都 exit 10；reopen 只写 `a<M>/reopen.jsonl`、`a<M>/reopen/`、`work/.lock` | state | S5 | contract |
| 08e-16 | 伪造/过期：页被改 → 导出拒；`confirm_sha256` 伪造 → 30；choices 含页外字段/页外记录 → 该记录拒；rev_base 不对 → 30；cell/seen 不对 → 30；普通 `confirm.html` 的 choices 路径不受影响（回归） | guard | S3 | unit |
| 08e-17 | CLI：`python -m klode_kit kb-audit --reopen` stdout 单行 JSON 回执字段/类型；`--reopen`+`--writeback` → 2；`--batch` 不符 → 30；`--reason` 落入事件行；`bin/klode-kit` 启动器同样可用 | success | S2 | contract |
| 08e-18 | schema 增量：`fixes` 接受合法 `reopen_id`，拒绝畸形；`record` schema 不变；重开提交产生的所有决定行过 `fixes` schema | guard | S2 | unit |
| 08e-19 | 与 pipeline 对接（U5 前半）：`pipeline --stage writeback` 后用启动器重开，`pipeline --stage writeback --choices` 提交 → 记录 r2、views 更新、writeback 阶段回执 done | state | S5 | contract |
| 08e-20 | U5 全链（真实 klode）：fx-manualfix 四路 `10.5` 自动 verified → 公开入口重开 → 手输 `1.05` → 导出 → klode：`示例片 1.05 2026-09-18` FOUND、`10.5` NOT-FOUND；导出行 rev 2 | success | S3 | cli_integration |
| 08e-21 | 往返性质：任意合法手输 v（hypothesis）→ 提交 → `effective.fields[f]==v`，其余字段不变，旧正文字节不变，导出含 v | property | S4 | contract |
| 08e-22 | 重开幂等性质：重开 k 次（k≥1，穿插重复调用）→ 日志恰 1 行、页恰 1 个、`records/` 不变 | property | S4 | unit |
| 08e-23 | 保全/单调性质（S5）：随机「重开→提交」序列（字段 × 值）后，每个 rev 的 `fields` 与上一 rev 只在被重开字段上不同；rev 连续；旧正文永不被改写 | property | S5 | contract |
| 08e-24 | item_label 评估钉：重开提交后新正文的项目名经 `audit_rev` 的 `candidates.jsonl` 解析成功（见 §6，保持现状的依据） | success | S1 | unit |
