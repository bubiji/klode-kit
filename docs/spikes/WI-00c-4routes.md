# WI-00c3 最难页四路 spike（`eval_fields` / `cer` + 实跑）

- 日期：2026-10-23（UTC 00:07–00:15）。闸门 G2 用户预授权（`KLODE_KIT_GATE_ACK=G2-20261023`），G4（本仓 `.venv` 装依赖、MLX 权重复用本机缓存）已批准。
- 宿主：macOS arm64；`claude` 2.1.285、`codex-cli` 0.163.0、`tesseract` 5.5.3、`ocrmac` 1.0.1、`mlx-vlm` 0.7.1。
- 外发内容：只有 `tests/fixtures/MANIFEST.json` 登记过 sha 的合成图（见 §1）。没有任何真实数据外发。
- 代码：`src/klode_kit/eval/eval_fields.py`（r2.7 四项指标）、`src/klode_kit/cer.py`（从 189 搬入并改造）、
  `src/klode_kit/eval/spike4.py`（单次云端调用的闸门 + 录制）；测试 `tests/unit/test_{eval_fields,cer,spike4_gate}.py`（unit）、
  `tests/model/test_hardest_pages_4routes.py`（model_integration）；本地引擎适配 `tests/_support/spike_engines.py`（只供 spike）。
- 结果数据：`work/spike/WI-00c3/latest.json`（gitignored；每次运行另存 `work/spike/WI-00c3/<run>/report.json`）。
  云端调用在 run `20261023T000728Z`（探针页）和 `20261023T001026Z`（其余 6 页）完成；之后的重跑全部复用录制，没有新的调用。

## 1. 页与四路

| 页 | 图（发送的就是这张） | der_sha256 |
|---|---|---|
| fx-u1-3labs p001 | `fx-u1-3labs/images/p001.png` | `2c3a82a9…1ca2` |
| fx-u1-3labs p002 | `fx-u1-3labs/images/p002.png` | `ad9aa32e…b95c` |
| fx-u1-3labs p003 | `fx-u1-3labs/images/p003.png` | `0c2b54bc…d390` |
| fx-rx-stamp p001（红章压在规格格上） | `fx-rx-stamp/images/p001.png` | `3e68e97d…6f80` |
| fx-lowres p001（0.4 倍） | `fx-lowres/images/p001.png` | `bf5e6888…8dc3` |
| fx-vertical p001（右侧竖排一列） | `fx-vertical/images/p001.png` | `d328c4ab…aa23` |
| fx-rotated90 p001（复原后的 derived） | `fx-rotated90/derived/p001.upright.png` | `69f87050…7e76` |

| 路 | 实现 | 参数 |
|---|---|---|
| A vision | ocrmac `framework="vision"`（`VNRecognizeTextRequest`） | `accurate`，`zh-Hans`+`en-US`；observation 按纵向重叠归行、行内按 x 排序后用空格连接 |
| B mlx | Unlimited-OCR 8bit，mlx-vlm Python API | 189 的参数：prompt `document parsing.`、`max_tokens` 3072、temperature 0、snapshot `6d2a0677…`；`HF_HUB_OFFLINE=1`（只用本机缓存，没有下载）。退化 / 出错时用 tesseract 兜底（本次 7 页都没触发） |
| B′ tesseract | `tesseract -l chi_sim+eng --psm 6 tsv` | 兜底，同时提供 R3-05 用的词框 |
| C claude | `python -m klode_kit.eval.spike4`：生产 argv（`cloud_argv.claude`） | `--model opus --effort max`（defaults.toml），每次 `--max-budget-usd 5` |
| D codex | 同上（`cloud_argv.codex`，permission profile） | 7 页实跑：`-m gpt-6-sol -c model_reasoning_effort=xhigh`（当时的 defaults）；⑤′ 锁定 `max` 后在最难页 rx-stamp 复核 1 次（§5） |

**每次云端调用前的闸门**（`spike4`，按顺序）：`KLODE_KIT_REAL=1` → isolation receipt（`docs/spikes/receipts/` 的脱敏副本）是本路
schema 合法的 PASS、所有检查 ok → `routes_enabled` 开启 → `isolation_check.check` 对**这一次**的 argv/cwd/env 全部通过 → CLI 版本
（claude 另加 `cloud_settings` sha）与 receipt 一致。任何一步不过：exit 61，不调用（00c3-10 覆盖）。本次 14 次调用全部过闸。
claude 7 次的 guard 日志都只有 `allow Read <sha>.png` + `allow StructuredOutput`（已写入各条 claude 录制的 `guard_log`）。

## 2. 各路 CER

口径：`klode_kit.cer`，参考文本为该页生成器版面的文字（无注入的模拟路逐行文本），去页标、NFKC、去全部空白，**数字只做 NFKC**。

| 页 | vision | mlx | claude | codex | tesseract |
|---|---|---|---|---|---|
| fx-u1-3labs p001 | 0.0060 | 0.0000 | 0.0000 | 0.0000 | 0.0419 |
| fx-u1-3labs p002 | 0.0060 | 0.0000 | 0.0000 | 0.0000 | 0.0419 |
| fx-u1-3labs p003 | 0.0060 | 0.0000 | 0.0000 | 0.0000 | 0.0422 |
| fx-rx-stamp | 0.0897 | 0.3462 | 0.0641 | 0.0641 | 0.1410 |
| fx-lowres | 0.0231 | 0.5462 | 0.0000 | 0.0000 | 0.3923 |
| fx-vertical | 0.0510 | 0.0764 | 0.0000 | 0.0000 | 0.1465 |
| fx-rotated90（upright） | 0.0060 | 0.0000 | 0.0000 | 0.0000 | 0.0419 |
| **合计（Σ距离 / Σ参考字符，1 032 字）** | **0.0213** | **0.1066** | **0.0048** | **0.0048** | **0.1095** |

- claude / codex 在 rx-stamp 上的 0.0641 来自它们**正确**读出了印章文字「示例专用章」（参考文本里没有印章）。
- vision：`10^9/L` 有 6 次读成 `10~9/L`；lowres 上 `10^12/L` → `10^12亿L`，抬头占位名的第一个字读成繁体（測）；rx-stamp 上 `10mg`→`0mg`、`0.25g`→`8.25g`，
  `5mg` 那一格被印章文字取代（`示們奇用章`）。
- mlx：rx-stamp 整张表**按列**输出（药品×3、规格×3、用法×3），没有行结构；lowres 把单位写成 LaTeX
  `\( {10}^{ \times 9}/\mathrm{L} \)`（还多出一个 `\times`），vertical 写成 `\( 10^9/L \)`。

## 3. 四项字段指标（r2.7，`eval_fields.evaluate`）

字段 = 每行的 值 / 单位 / 标志（真值非空才算），共 77 个字段、36 行。每路的读数由 `eval_fields.parse_rows` 按项目名从该路的行里解析。
**合并结果** = 每个字段取严格多数（≥3/4）路的读数，否则记未读（即进人工）。**自动通过** = 四路都读到且读数相同。

| 指标 | 阈值 | 合并结果 | vision | mlx | claude | codex |
|---|---|---|---|---|---|---|
| 字段精确匹配率 | ≥ 0.98 | **0.922** ✗ | 0.870 | 0.883 | 1.000 | 1.000 |
| 项目—数值—单位关联准确率 | ≥ 0.98 | **0.833** ✗ | 0.722 | 0.750 | 1.000 | 1.000 |
| 缺字段率 | ≤ 0.02 | **0.078** ✗ | 0.000 | 0.039 | 0.000 | 0.000 |
| 错误自动通过率 | = 0 | **0 / 64 = 0** ✓ | | | | |
| 四路一致字段数（从运行结果算） | ≥ 20 | **64** ✓ | | | | |

- **错误自动通过率 = 0**：没有触发「> 0 即停止并重新规划」。64 个四路一致的字段全部等于真值。
- 00c3-09 的两项断言（错误自动通过率 == 0、四路一致 ≥ 20）**通过**。
- `evaluate` 的整体 verdict 为 **FAIL**，原因 `exact_match_rate`、`association_accuracy`、`missing_rate`：合并结果（人工裁决之前）有
  71 个字段正确、**0 个错读**、6 个未读（6 行受影响，30/36 行全对）。6 个未读都是 2:1:1 分歧：rx-stamp 的 3 个规格（vision 错读 + mlx 无行结构），
  lowres 的 2 个单位与 vertical 的 1 个单位（vision `10~9/L` / `10^12亿L` 与 mlx LaTeX 各错一种写法，只剩 claude、codex 两路一致）。
  这些字段在流水线里都是「分歧 → 人工」，不会自动通过；但按 r2.7 对合并结果的阈值是不达标的。
  **口径定案（主会话 2026-10-23）**：r2.7 前三项阈值（精确匹配率、关联准确率、缺字段率）针对的是**人工确认之后**的值；人工裁决之前的合并值
  只作参考，用来衡量人工工作量（本次：77 个字段里 6 个要人工裁决，0 个错读）。因此上表「合并结果」的 ✗ 不构成本 WI 的失败。
  错误自动通过率（= 0）和四路一致字段数（≥ 20）仍按运行结果判定。

## 4. R3-05：ocrmac==1.0.1 的 Vision 粒度

方法：对每个 observation，看它的框里有几个**绘制段**（生成器版面的段框 = 模拟 vision 路的 `tokens[].bbox_px`，按段中心落入判定）。
恰好一个段、且框能定位它（IoU ≥ 0.5，或段框落在 observation 框外扩 6 px 之内）→ `exact`；含 ≥2 个段 → `multi`（token 框只能按字符比例估计，即
`geometry: approx`）。字段（表格单元格）由容纳它的 observation 决定 exact / approx；approx 的单元格若同一行有 tesseract 词框（exact）佐证仍可用于列聚类，否则记
`no_geometry`（要人工点选）。

| 页 | observation | exact（=一个段） | multi | 单元格 | exact | approx | 需人工 |
|---|---|---|---|---|---|---|---|
| fx-u1-3labs ×3 | 35 / 35 / 34 | 32 / 32 / 31 | 3 / 3 / 3 | 12 ×3 | 12 ×3 | 0 | 0 |
| fx-rx-stamp | 16 | 13 | 3 | 3 | 3 | 0 | 0 |
| fx-lowres | 24 | 20 | 4 | 8 | 8 | 0 | 0 |
| fx-vertical | 33 | 30 | 3 | 10 | 10 | 0 | 0 |
| fx-rotated90 | 35 | 32 | 3 | 12 | 12 | 0 | 0 |
| **合计** | **212** | **190** | **22** | **69** | **69** | **0** | **0** |

**结论（R3-05）**：
- **observation 框 ≠ 词。** ocrmac 1.0.1 的 `framework="vision"` 每个框是一个 `VNRecognizedTextObservation`（一段连续文字），没有词级框；
  `unit` 参数只对 `framework="livetext"` 生效（源码 `ocrmac/ocrmac.py`：`livetext_from_image(..., unit='token')`，「'token' (default) returns the
  finest-grained children (often characters for CJK)」）。22/212 个 observation 含两个以上绘制段：每页抬头的姓名、编号、日期三行
  （标签与值合成一段），lowres 另有表头「提示 参考范围」。因此契约保留 `tokens[].geometry: exact|approx`：单段 observation 记 `exact`，多段 observation
  拆出的 token 记 `geometry: approx`。
- **表格单元格：69/69 为 exact，降级（approx）0 个，降级后需人工确认的比例 0/69 = 0%。** 这些合成表格列间距很宽，Vision 每格都是独立 observation。
  真实单据列距窄时，同一 observation 可能跨格，这个比例会上升（RR-11）。
- 观察：短字形（`g/L`、`%`、`L`）的 observation 框比绘制框松几个像素（IoU 0.39–0.49）。第一版判定只用 IoU ≥ 0.5，把 17 个单元格误判为 approx；
  改为「单段 + 包含（外扩 6 px）」后为 0（数据同一份，判定规则改动记在本节，见偏离 D-9）。
- 可选项（未量化）：`framework="livetext", unit="token"` 给出逐字框（p001 共 103 个），可作为 exact 几何的另一来源，留给 WI-04a 评估。

## 5. ⑤′ Codex 最高 effort

| 来源 | 内容 |
|---|---|
| `codex exec --help`（0.163.0） | 只有 `-c, --config <key=value>`，**没有列出** effort 的枚举 |
| config-reference（https://developers.openai.com/codex/config-reference ，本地快照 `work/isolation/wi-00c2b-discovery-20261022T225300Z/docs/config-reference.html`） | `model_reasoning_effort` string：「Reasoning effort advertised by the selected model, such as low, medium, high, xhigh, max, or ultra. Available levels depend on the model and client.」 |
| `codex debug models`（0.163.0，「Render the raw model catalog as JSON」） | `gpt-6-sol`：`supported_reasoning_levels` = low、medium、high、xhigh、**max**（「Maximum reasoning depth for the hardest problems」）、ultra（「Maximum reasoning with automatic task delegation」）；默认 medium。没有 `minimal` |

**结论**：对 `gpt-6-sol`，纯推理的最高档是 **`model_reasoning_effort=max`**。`ultra` = max + 自动任务委派（派生子 agent），与隔离 profile 的
`agents.enabled=false`（关闭多 agent 工具，用户 2026-10-23 决定）相冲突，排除。

**已锁定（用户要求「四路固定最高档、不降档」，主会话 2026-10-23 转达）**：

- `src/klode_kit/config/defaults.toml`：`[codex] effort = "max"`（原 `xhigh`）。
- `src/klode_kit/ocr/cloud_argv.py`：`CODEX_EFFORTS` 加入 `max`；`ultra` 不加（构建器与审查闭集都拒绝）。
- 已记录断言的修改（按用户决定）：`test_cloud_isolation_edges.py::test_codex_effort_config_twice_or_bad_fails` 里用来判 FAIL 的值从 `max`
  改为 `ultra`；连带 `test_cloud_isolation.py::test_builder_rejects_unknown_effort` 的 `("codex", "max")` → `("codex", "ultra")`、
  `test_config.py` 的 `R26_DEFAULTS["codex.effort"]` 和 `test_ids.py` 的默认配置镜像 `effort = "xhigh"` → `"max"`。新增
  `test_codex_effort_max_accepted_ultra_rejected`。
- **复核**（run `20261023T002746Z`，`test_codex_max_effort_live`）：生产 argv 带 `-c model_reasoning_effort=max`，过闸（isolation_check + receipt），
  1 次调用 exit 0，9 行结构化答案，rx-stamp 三个规格全对（精确匹配 1.0）；token：输入 8 827、输出 675（推理 516，xhigh 时同页 262）。
  录制 `tests/recordings/codex/3e68e97d…6f80.effort-max.json`。
- 7 页四路的 codex 录制仍是 `xhigh`（锁定之前跑的），按原样复用，没有重跑。
- `minimal` 仍在 `CODEX_EFFORTS` 里（gpt-6-sol 的目录里没有它）：拿掉它要改 `test_codex_profile_00c2c.py` 的已记录取样，本次没有授权，未动。

## 6. 费用与调用次数（台账 `work/isolation/ledger.jsonl`，`wi: WI-00c3`）

| 路 | 次数 | 费用 / 用量 | 上限 |
|---|---|---|---|
| claude（opus → `claude-opus-5-5`，effort max） | 7 | **$0.6573**（rx-stamp 最贵 $0.319，输出 14 832 token；其余 $0.039–0.089） | ≤ $8；每次 `--max-budget-usd 5` |
| codex（gpt-6-sol，xhigh） | 7 | 输入 62 242、输出 3 277（其中推理 1 701）token | ≤ 20 次 |
| codex（gpt-6-sol，**max**，⑤′ 复核） | 1 | 输入 8 827、输出 675（推理 516）token | 主会话限 ≤ 2 次 |
| vision / mlx / tesseract | 7 / 7 / 7 | 本地，无费用；MLX 每页约 4–6 s | — |

全部 15 次云端调用（claude 7、codex 8）一次成功（exit 0、有结构化答案），没有重试。

## 7. 录制（`tests/recordings/<engine>/<der_sha256>.json`，`recorded: true`）

`vision`、`mlx`、`tesseract`、`claude`、`codex` 各 7 个，共 35 个文件，外加 `codex/<sha>.effort-max.json`（⑤′ 复核）。
**位置（主会话 2026-10-23，方案 B）**：录制最初放在 `tests/fixtures/replay/`，但 `tests/fixtures/` 是生成器产物树——00a-05（只许白名单名字，
无 allowlist）和 00a-02（`gen_all --check` 逐字节核对）都会把真实 OCR 输出判为问题，`gen_all --write` 还会把它们移进 `_trash`。因此移到
`tests/recordings/`（移动，未删除），有自己的 `tests/recordings/MANIFEST.json`（37 项，含 README）和 `README.md`；`tests/fixtures/MANIFEST.json`
恢复为本 WI 之前的内容（逐字节相同）。假 CLI 在没有回放场景时按 `argv_sha`（含程序名的完整 argv）从这里取录制回放
（`tests/contract/test_fake_cli_recordings.py`）。
格式（`schema: klode-kit.replay_recording/1`）：

- 云端两路：`argv`（send 目录记为 `<send_dir>`）、`argv_sha`、`cli_version`、`model`、`effort`、`stdout` / `stderr` / `exit_code`（原样，可直接改写成假 CLI
  场景）、`lines`、`cost_usd`、`tokens`、`guard_log`（仅 claude）。
- vision：`raw` = observation 列表（文字、置信度、像素框），`params`（ocrmac 版本、语言、图像尺寸）、`lines`。
- mlx：`raw` = 模型原始输出（含 `<|det|>` 与 HTML 表格），`params`、`meta`（生成 token、repeat_ratio、degenerate）、`fallback`、`lines`。
  `lines_rederived_at`：修正 189 `strip_layout_tags` 的问题后，`lines` 由录制的 `raw` 重新推导（见 §8 F-1），`raw` 未变。
- tesseract：`raw` = TSV，`words`（词框）、`lines`。

privacy_scan：0 条。原始输出里有 33 处误报（30 条唯一项）——JSON 转义后的占位名 `测试患者甲\"` / `\n` 被 `NAME_RE` 连同反斜杠一起取作名字、Vision
把占位名读成 `測试患者甲`、claude `total_cost_usd` 的二进制浮点尾数恰好 18 位（同 WI-00c1b 遇到的现象）。为了保证回放逐字节一致，录制内容不改写，
这些条目作为已核查的误报加入 `.privacy-allowlist`（带注释）。

## 8. 实测发现

- **F-1（189 缺陷，189 未改）**：189 `src/ocr/engines.py::strip_layout_tags` 先把 `</tr>` 换成换行，再用 `</t[dh]>\s*` 换成空格——后者把刚插入的换行吃掉，
  **整张表塌成一行**。CER 去空白，所以 189 的 CER 看不出来；按行解析字段时，后面行的标志会被错配到前面的行（本次 mlx 关联准确率从 0.28 → 0.75 即为此）。
  本仓的 spike 适配器已改为先处理单元格、再处理行。WI-04a 搬 MLX 适配器时要带上这个修正和对应的单元测试。
- **F-2**：MLX 有时把单位写成 LaTeX（`\( 10^9/L \)`）；WI-04a 的 MLX 适配器需要展平 LaTeX，否则这些字段永远与其他路分歧。
- **F-3**：MLX 对 rx-stamp 按列输出表格（无行结构），这一页 mlx 字段全部未读。
- **F-4**：Vision 在印章压字处把印章文字当作单元格内容；rx-stamp 三个剂量里错两个、丢一个。
- **F-5**：合并结果不满足 r2.7 前三项阈值（§3）。

## 9. 偏离（交主会话审核）

| # | 偏离 | 理由 |
|---|---|---|
| D-1 | 发送的是**未打码**的登记合成图（矩阵 00c3-09 写「打码后」） | 打码器（WI-05a）还没有；只允许发送 MANIFEST 登记的图，打码图不在 MANIFEST 中。图上只有白名单占位名 / 编号 |
| D-2 | 在矩阵的 4 个场景之外加了 `fx-rotated90`（upright derived），共 7 页 | 委派说明点名了 rotated90；成本很小 |
| D-3 | 录制放在 `tests/recordings/<engine>/<der_sha256>.json`，不是计划写的 `tests/fixtures/replay/…` | 主会话决定（方案 B），见 §7；假 CLI 已能按 argv_sha 直接回放 |
| D-4 | spike 闸门与 receipt 的绑定只比 CLI 版本（claude 另比 settings sha），不比 `argv_sha` / `effective_config_sha` | 后两者绑定图像 sha 与随机 send 目录，换一张图必然不同；每次调用前另做完整的 `isolation_check` |
| D-5 | `tests/fakes/bin/_fake_cli.py` 增加 `$HOME/.klode-kit-fake.json` 回退（日志 / 回放目录） | 云端进程只拿到 r5 白名单 env，`KLODE_KIT_FAKE_LOG` 传不进去；不放宽白名单，而是由测试给一个合成 HOME。环境变量存在时仍以环境变量为准 |
| D-6 | 7 页实跑的 codex 是 `xhigh`；`max` 锁定后只在最难页复核 1 次 | §5；主会话限定 ≤ 2 次调用 |
| D-7 | 新建依赖组 `ocr-local`（`ocrmac==1.0.1; darwin`、`mlx-vlm==0.7.1; darwin arm64`），改了 `pyproject.toml`、`uv.lock` | G4；不放进默认组，Linux CI 的 `uv sync --frozen` 不受影响。注意：之后不带 `--group ocr-local` 的 `uv sync` 会把它们从 `.venv` 卸掉 |
| D-8 | `.privacy-allowlist` 追加 31 条已核查误报（路径为 `tests/recordings/…`） | §7。改前副本在 `_trash/wi-00c3-preinstall/` |
| D-9 | R3-05 判定规则在看过第一版结果后改过（IoU ≥ 0.5 → 单段 + 外扩 6 px 包含） | §4；第一版的 17 个 approx 全是短字形框松几个像素，不是跨格 |
| D-10 | `eval_fields` 自定口径：自动通过 = 四路读数相同（不看几何）；合并 = 严格多数；`parse_rows` 按项目名 + 位置解析 | r2.7 没有给这几处的定义 |

## 10. 残余风险

- **RR-11**：R3-05 的 0% 人工比例只对宽列距的合成表格成立；真实单据需要在阶段 6 重测。
- **RR-12（已定案）**：r2.7 前三项阈值针对人工确认后的值；裁决前合并值只是人工工作量的参考（§3）。人工确认后的指标要等审计流程（WI-08 起）才能测。
- **RR-13**：只覆盖 macOS + claude 2.1.285 + codex-cli 0.163.0 + ocrmac 1.0.1 + mlx-vlm 0.7.1；升级任一项需要重跑（`KLODE_KIT_SPIKE_REDO=1`）。
