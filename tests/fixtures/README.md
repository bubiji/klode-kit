# tests/fixtures — 合成测试样本

**本目录全部合成**：所有图像、PDF、文本、JSON 均由本目录的 `gen_*.py` 确定性生成，不含任何真实
人员、单据或医疗数据。人名、编号、药名只用白名单：姓名 `测试患者甲`（`测试患者乙`…），编号
`T-0000001`（`T-0000002`…），药名 `示例片`，机构名 `示例医院`。`privacy_scan` 对本目录 0 命中；
单元测试 00a-05 另做一次独立抽取核对。

## 生成与校验

- 生成：`.venv/bin/python tests/fixtures/gen_all.py --write`（原地更新；被替换或不再生成的旧文件先移入
  仓库根 `_trash/fixtures-<UTC 时间>/`，从不删除）。
- 确定性：无时钟、无随机；`--out DIR` 两次生成逐字节相同（00a-01）；`--check` 生成到临时目录后与
  本目录逐字节比较（00a-02）。这两条在 `cli_integration` 的 `macos` 子集里跑；Linux CI 不再生成，
  只用已提交的成品（00a-03 校验 MANIFEST）。
- `MANIFEST.json`：本目录每个文件（含 `gen_*.py` 与本 README，不含 MANIFEST 自身、`__pycache__/`）的
  `path / sha256 / bytes`；`python -m klode_kit.devtools.fixture_manifest verify` 双向校验。

## 字体来源

- 位图（PNG）：macOS 系统字体 **Hiragino Sans GB**（`/System/Library/Fonts/Hiragino Sans GB.ttc`，
  index 0），由 Pillow 渲染。字体文件本身不入库、不再分发，只提交渲染后的像素；可再分发条件不明，
  因此**只在 macOS 上再生成**。
- 文字层 PDF：reportlab 内置 CID 字体 **STSong-Light**（不嵌入字体文件，Adobe-GB1 编码）。
- 版本以 `uv.lock` 为准（Pillow、reportlab）；升级任一项都要重新生成并提交，MANIFEST 随之变化。

## 目录约定（表格类场景）

- `images/<key>.png`：原件（一张图 = 一条记录，契约 `page_id = <doc_id>/p001`；`<key>` 是批内序号）。
- `engine/<route>/<key>.jsonl`：模拟的四路输出（vision / mlx / claude / codex），行契约
  `{engine, page_id, der_sha256, line_idx, text, bbox_px, tokens}`；云端两路 `bbox_px`、`tokens` 为 null，
  mlx 只有行框，vision 有词级 `tokens[].geometry`（exact/approx）。
- `truth.json`：结构化真值。`fields[]` 记每个字段的真值、各路读到的值及其在该路行内的 `line_idx + span`，
  和设计标签 `designed`（agree / majority / tie / plurality / all_differ / missing）。字段 id 为
  `<key>-l<NN>:<col>`，NN 是 vision 路该行的行号（如 `p002-l07:value`）。
- `scenario.json`：场景说明、页清单、注入的错误、多次尝试（attempts）。
- 错误注入器在 `gen_engine_outputs.py`：swap / colshift / missingrow / degenerate；退化判定
  `klode_kit.ocr.degenerate`（`repeat_ratio > 0.3`）。

## `fx-labs-1x`

单张血常规，6 行，四路完全一致（基线；第 5 行用于裁图 ±1 行）。

## `fx-labs-3x`

同一患者三个日期各一张化验单；血红蛋白三次观测中两次数值相同（128、128、135）。四路一致。

## `fx-dup10`

两行数值都是 10（白细胞、尿素），四路一致；对齐必须按单元格保留 ordinal，不得合并。

## `fx-colshift`

mlx 路第 3 行（row 2）列错位：数值落进单位列、数值列为空；其余三路正确。

## `fx-swap`

codex 路第 3/4 行（row 2、row 3）数值对调；值的多重集合不变。

## `fx-missingrow`

claude 路缺第 5 行（row 4）；codex 路把 row 1、row 2 拼成一行（merged_rows）。

## `fx-manualfix`

处方单一行示例片，图上规格为 1.05，四路都读成 10.5；人工确认时改为 1.05（日期 2026-09-18）。

## `fx-redo`

U6 重做：t1 四路齐、codex 在 row 0 数值有分歧（供人工点选为 r1）；t2 重做时 codex 缺 p001；t3 修复后四路一致。

## `fx-rx-stamp`

处方笺，三行示例片，红色印章压在第 2 行规格上；mlx 路把该格读错（多数票分歧）。抬头含姓名与编号，供打码测试。

## `fx-lowres`

低分辨率截图（按 0.4 倍缩小）；mlx 路把 4.35 读成 4.36（多数票分歧）。

## `fx-vertical`

横排表格右侧混排一列竖排文字（合成样本仅供测试）；四路一致。

## `fx-rotated90`

整页顺时针旋转 90° 存储；逆时针转 90°（PIL ROTATE_90）复原。各路结果针对复原后的 derived 图。

## `fx-u1-3labs`

U1：同日（2026-10-22）三张化验单，共恰好 5 条分歧：p001 三条（含一条四路全不同），p002 两条，p003 无分歧。

## `fx-u3-mixed`

U3：两张化验单，p001 四路完全一致（应 auto verified），p002 只有多数票分歧（两处）。

## `fx-u8`

U8：两张化验单；p001 一处多数票、一处四路全不同；p002 的 p002-l07（row 2 数值）2:2 平票，另一处多数票。

## `fx-approx-only`

row 2 只有 approx 几何（vision 该行 token 标 approx，且没有其他 exact 几何路），四路完全一致；该行应进 no_geometry，不能 auto verified。

## `fx-text-pdf`

三页文字层 PDF `guide-2026.pdf`（STSong-Light 合成指南文本），每页字符数远超 `min_chars`；
应判 `text_layer: true` 并跳过四路（U4）。

## `fx-mixed-pdf`

四页混排 `mixed.pdf`：第 1 页文字层、第 2 页空白、第 3 页文字层为符号噪声（garbled）、第 4 页只有扫描图像；
只有第 1 页跳过四路（`scenario.json` 记每页分类）。

## `fx-corrupt-pdf`

`corrupt.pdf` 是 `fx-text-pdf/guide-2026.pdf` 的前一半字节（截断、无 `%%EOF`）；应 exit ≠ 0、
receipt 记 `pdf_unreadable`、不产出任何 text 产物（04c-06）。

## `fx-book-aurora`

3 章合成英文小书 `aurora-kettle.txt`（虚构的 aurora kettle，不含人名），章头 `Chapter N: <title>`；
`src/desk.json` 三维度各 1 题调优题，`src/holdout.jsonl` 5 题冻结留出集（U7、02a/02c）。

## `synth_project`

假数据项目：`CLAUDE.md`、假 `.mcp.json`（一个无害的 `true` 命令）、`klode-kit.toml`（只覆盖
`route_retry = 5`，00b2-11）。canary 文件由测试在 tmp 拷贝里现场放置，不入库。

## `replay/probe-synth`

隔离探针事件流（合成）：`denied`、`enoent`、`unattempted`、`mixed`、`narrative-lies`（模型文字声称被拒、
工具结果实为成功）为 `claude -p --output-format stream-json` 形状；`codex-denied`、`codex-read` 为
`codex exec --json` 形状。字段名是占位形状，WI-00c2 在 G2 下录制真实流后校正。`expected.json` 给出每个
canary 的判定（denied / enoent / unattempted / read）与结论（只有全部 denied 才 PASS）。

## `fx-chain`

完整来源链产物（三段不变量 + 版本绑定，02 r3.1）。每个子目录都是一套完整产物，`variant.json` 写明基底、
期望违例与相对基底改动的文件；`clean` 即矩阵里的 `fx-chain`。

| 变体 | 期望 | 说明 |
|---|---|---|
| `clean` | 0 违例 | 完整来源链：四路读同一张打码 derived 图；row 1 数值分歧（vision 6.2，其余 6.8），人工按 route=vision 确认；其余字段 auto。 |
| `legal-manual` | 0 违例 | 四路都把 示例指标 读成 10.5，人工 manual 改为 1.05，evidence 带 region 与 seen；段 ① 对 10.5 仍成立。 |
| `legal-dateshift` | 0 违例 | deident.mode=dateshift 导出：每行日期经 dateshift transform 偏移到 2026-10-02。 |
| `legal-alias` | 0 违例 | row 0 项目名四路都读作 HGB，导出时经 alias transform 归一为 血红蛋白（只作用于 item）。 |
| `legal-oldbuild` | 0 违例 | 记录已推进到 rev 2（row 1 改选 mlx 的 6.8），旧 build 仍绑定 rev 1 且自洽。 |
| `legal-pdftext_dpi` | 0 违例 | 文字层 PDF 单路 pdftext：bbox_pt 经 page_map.dpi=150 换算为 bbox_px。 |
| `broken-1` | 恰好一个 `span_mismatch`（基底 `clean`） | vision 路 labs[0].value 的 prov.span 右移 1 位，行文本在该跨度处 ≠ 候选值。 |
| `broken-2` | 恰好一个 `der_parent_mismatch`（基底 `clean`） | derived.jsonl 的 parent 不等于 manifest 的 img_sha256。 |
| `broken-3` | 恰好一个 `manual_evidence_missing`（基底 `legal-manual`） | manual 决定的 evidence 去掉 region。 |
| `broken-4` | 恰好一个 `multiple_effective_decisions`（基底 `clean`） | labs[2].value 多出一条同 rev 的生效决定。 |
| `broken-5` | 恰好一个 `transform_on_numeric`（基底 `clean`） | 导出行给 labs[0].value（数值）加了 alias transform（from==to，文本不变）。 |
| `broken-6` | 恰好一个 `rev_binding_mismatch`（基底 `legal-oldbuild`） | export_map 中 row 0 那一行 rev=2，而 audit_bindings 绑定 rev 1（该行字段在 r1/r2 相同）。 |
| `broken-7` | 恰好一个 `missing_effective_decision`（基底 `clean`） | verified 记录的 date 字段没有任何决定。 |
| `broken-8` | 恰好一个 `route_value_mismatch`（基底 `clean`） | row 1 人工决定 route=vision，写入的却是★推荐值 6.8；记录与导出同步为 6.8。 |
| `broken-9` | 恰好一个 `record_value_mismatch`（基底 `clean`） | rev 1 记录里 labs[0].value 为 129，决定值是 128；导出与记录一致（129）。 |
| `broken-10` | 恰好一个 `export_text_mismatch`（基底 `clean`） | 导出行在 labs[0].value 声明位置上的文本为 182 ≠ apply(transforms, 128)。 |
| `broken-11` | 恰好一个 `pdftext_dpi_mismatch`（基底 `legal-pdftext_dpi`） | pdftext 路 row 0 那一行的 bbox_px 直接用 72dpi 的 bbox_pt，没有按 page_map.dpi 换算。 |
| `broken-12` | 恰好一个 `span_mismatch`（基底 `clean`） | codex 路（bbox_px:null）labs[3].value 的 prov.span 右移 1 位。 |

说明：`fx-publish-ab`（两个 build 的发布切换）由测试在 tmp 里用回放构建，不入库。
