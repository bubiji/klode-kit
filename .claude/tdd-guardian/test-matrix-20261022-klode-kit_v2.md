# Test Matrix v2 — klode-kit（plan-20261022-klode-kit，M0–M3）

> **v2 说明**：本版按 `.claude/tdd-guardian/spec-attack-20261022-r1.md`（spec-adversary 第 1 轮，结论 BREACHED，共 16 个缺口）修订。
> - 原文件 `test-matrix-20261022-klode-kit.md` 保持不动。
> - 本版补齐了 P0（缺口 1–8）、P1（缺口 9–12）、P2（缺口 13–16），并按设计层提示收窄了 env 白名单。
> - 逐条对照见 §9。
>
> 依据：
> - `.claude/tdd-guardian/plan-20261022-klode-kit.md`（下称「计划」）
> - `docs/pipeline/20261022-klode-kit/04-需求摘要与usercase.md`（U1–U8 验收基准，确认版）
> - `02-概要设计.md` 的 r2–r4 段：r2.2 来源链 / 打码 / 字段对应、r2.3 状态机、r2.6 退出码、r3.1 三段不变量、r3.2 键分层与发布、r3.3、r3.5、r4.1–r4.4
>
> 未读私有项目 002。本文件只做测试设计，没有改任何源码或测试文件。仓库现状：`src/`、`tests/` 下只有 `.gitkeep`，是全新仓库，各 lane 都处于 bootstrap 状态。

## 0. 读法与约定

### 0.1 每条用例的字段

| 列 | 含义 |
|---|---|
| ID | 普通用例为 `<WI>-<序号>`；WI-31 的验收用例用 `U<n>-<序号>`、`P-<序号>`、`UC-<序号>` |
| 测试名 | pytest 函数名。计划「必需测试」里已有的名字原样保留，新增的按同一风格命名 |
| 类 | 五类之一：**成功**、**边界**、**非法**（非法输入、守卫）、**边缘**（失败路径、状态迁移、幂等、崩溃恢复，以及在生成域上的性质）、**并发** |
| lane | `unit` / `contract_replay` / `cli_integration` / `model_integration` / `host_acceptance` / `manual`，与计划 §0.1 的 pytest 标记一致。子标记（`macos`、`macos_mlx`、`needs_node`）写在 lane 后面 |
| fixture | `tests/fixtures/` 下的合成场景名（见计划 WI-00a 清单）。`hyp:` 表示 hypothesis 生成域；`tmp:` 表示在 tmp 里现场构造；标「新增」的见 §7 |
| 操作 | 被测入口。只走公开入口：`bin/klode-kit <子命令>`、模块公开函数，或真实子进程 |
| 断言 | 可观察的结果：返回值、退出码、文件内容或 sha、目录树 diff、stdout、进程状态。**没有一条只断言「被调用」**。用到替身调用日志时，断言的是日志内容或条目数（例如「零条」），并且一定同时有一个 L1–L5 的结果断言 |
| L·S | 断言级别 L1–L7 · 规格级别 S1–S6（定义见 policy-core） |
| 闸门 | 需要手动闸门 G1–G5 时标出；空着表示全自动 |

**格式说明**：policy 模板要求每条用例写「替身边界 / 配对的集成用例 / 什么重构会打破它」。这三项在同一个 WI 内对所有用例都一样，所以写在每个 WI 的表头，不逐行重复；个别例外在该行断言里注明。每个 WI 都必须回答 Law 问题。

### 0.2 替身边界（只用计划 §0.4 列出的四类）

| 边界 | 替身 | 本矩阵中的配对真实用例 |
|---|---|---|
| `claude`/`codex` 子进程 | `tests/fakes/bin/{claude,codex}` 读取 `tests/fixtures/replay/` | 00c2-06/07/08、00c3-09、00d2-01/02、06-11/12、11b-09、12-04、U5-02 |
| Vision / MLX / tesseract | 原始引擎输出回放 | 04a-10/11/12、05a-07、U2-02 |
| `klode` CLI（只在 replay 全链里） | 录制的 stdout / JSON | 01a-10、01b-07、02a-08、02c-01、02d-06、09b-09、10a-01/02/09、11a-01、U4-01、U7-01、P-02 |
| 时钟 | 注入 `now()` | 不需要配对 |

自己写的模块一律不 mock。另有两处替身不在上面四类里，它们是宿主运行时的边界，已在对应行注明并给出配对：
- `claude mcp get` 的录制输出：00d2-08，配对 00d2-06。
- Workflow 宿主的 `agent()` 桩：14-03/05，配对 14-04。

### 0.3 崩溃注入与暂停点（需要实现者确认）

以下用例需要在指定位置崩溃或暂停：00b1-11、04b-13、08c-13、09d-02 要求「在写点崩溃」；05b-08 要求「在检查之后暂停」。在不 mock 自有模块的前提下，可用两种办法：

1. **真子进程配合 `SIGKILL` 或暂停。**
   - 生产代码里放 `crashpoint("<name>")`，默认什么也不做。
   - 测试设 `KLODE_KIT_CRASH_AT=<point>` 时，子进程在该点自杀。
   - 测试设 `KLODE_KIT_PAUSE_AT=<point>` 时，子进程在该点阻塞，等测试写入放行文件（`$KLODE_KIT_PAUSE_DIR/<point>.go`）后继续。这个暂停点是 v2 为缺口 6 新增的，用来把 TOCTOU 测试变成确定性的。
2. **随机时刻 `SIGKILL`。** 由 hypothesis 生成延时，事后只检查不变量。

本矩阵两种都用：方式 1 枚举全部写点和暂停点（S5），方式 2 防止有写点被漏掉（S4）。方式 1 会在生产代码里留下测试缝，与 policy 第 13 条有张力，需要在 WI-00s 记一次决定（§7-F9）。

---

## 1. 关键不变量 → 覆盖用例

| # | 不变量（出处） | 覆盖用例（最强的放前面） | 最高规格 |
|---|---|---|---|
| K1 | **四路全齐且一致才 auto-agree / 自动 verified**（r2.2-D、r2.3 ①、04 U3） | 07c-05（随机四路网格，双向蕴含）；08c-10（v2 起链从 align 开始，生成域包含仅 approx 的行、缺行、拼行）；07b-12（对齐必须恢复已知的对应关系）；08c-17（`no_geometry` 必须传到 gate）；08a-07；U3-02（metamorphic）；07c-03/04/09/12；07b-07 | S6 |
| K2 | **uncertain 非空不得 verified**（04 §2、r2.3、r3.3） | 08a-07（性质）；08c-11（伪造 choices 也不能把页级 uncertain 的记录点成 verified）；08c-17；08c-18（只点了部分字段）；08c-19（选了缺失 cell 上的路）；08a-05；08c-03；U8-03；09b-06 | S4 |
| K3 | **effective 只由 writeback 推进；候选失败不动旧 verified**（r4.1） | 08c-08（v2 加强：每次推进后 effective 与被提升的候选逐字段相等，且旧候选带 `superseded_by`）；08c-09；08c-16（其他所有阶段都不改 `_by_id` 字节）；08d-06 / U6-01；09b-05；12-03 | S6 |
| K4 | **来源链三段 + 版本绑定**（r3.1） | 00b3-12（v2：损坏域扩到 r3.1 全部不变量 × 任意一路）；00b3-10（apply/unapply 往返）；00b3-14…19（v2 新增的 6 个变体）；00b3-01…09/11；01a-07；P-01；P-02；01a-03/04；01a-10 | S5 |
| K5 | **发布指针完整，且只指向经过校验的 build；prune 永不移动受保护 build**（r3.2、r4.2） | 09d-02（v2：目标必须通过 MANIFEST 和 check 校验，崩溃点覆盖 build→check→publish 全段）；09d-17（拒绝未经校验的目标）；09d-03；09d-08；09d-09；09d-11；09d-07；09d-16；09d-18；09b-12（build 树整体只读） | S5 |
| K6 | **外发只认 MANIFEST sha，失败零调用**（r2.2-B、r3.5、r4.3） | 05b-07（性质）；05b-08（v2：确定性 TOCTOU）；05b-15（实际执行的 argv 就是审查过的 argv）；05b-03；05b-02/04/06/14；00c1-18/19（argv 的值、重复项、`--settings` 文件都纳入审查）；00c3-10；U2-03 | S4 |
| K7 | **同一 MCP 进程 A→B 切换，且每一类请求都读新 build**（r4.2） | 11a-11（切换后第一条请求就是查询，必须拿到 B；正常和两级降级三条路径都参数化）；11a-01（S5）；11a-02（v2 law：kb_version 等于上一条查询实际使用的 build）；11a-09；11a-06/07；11a-12；11b-07/08 | S5 |
| K8 | **13 步固定顺序；`builds/` 之外零写入**（r2.6、r3.2） | 09b-03（任一步 k 失败：k 之后的步不执行、指针不动、外部零写入）；09b-02（树快照 diff）；09b-01；00b1-04（schema 层就拒绝非 13 项顺序）；09b-07 | S5 |

---

## 2. M0 · spike + 契约

### WI-00s：仓库与测试脚手架

**Law**：bootstrap 例外是单调的——lane 一旦出现过 ≥1 个用例，之后任何一次 0 用例都判 FAIL，例外不会恢复。除此之外都是守卫，没有 law。
**替身 / 配对**：`claude` 假可执行文件本身就是被测对象（00s-05/06），不需要配对。**重构敏感点**：只通过 `run_lane` CLI、`privacy_scan` CLI 和 pytest 子会话断言。
**并发**：00s-13。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00s-01 | test_run_lane_zero_tests_fails | 非法 | `unit` | tmp：空用例目录 + 非空的 lane 历史 | `run_lane unit` | exit ≠ 0；stdout 含 `0 tests discovered`；`work/lanes/unit.json` 的 status=FAIL | L1·S3 | |
| 00s-02 | test_run_lane_bootstrap_warns_only_when_never_had_tests | 边界 | `unit` | tmp：lane 历史为空 | `run_lane unit` | exit 0；stderr 有 bootstrap 警告；receipt 记 `bootstrap:true`，status 不是 PASS | L1·S2 | |
| 00s-03 | test_bootstrap_exception_permanently_ends | 边缘 | `unit` | hyp：每次运行的用例数序列（含 0） | 依次 `run_lane` | 第一次出现 ≥1 之后，所有 0 用例的运行都 FAIL；在那之前只 WARN | L1·S5 | |
| 00s-04 | test_socket_blocked | 非法 | `unit` | 无 | 在用例里连 127.0.0.1 | 抛 `SocketBlockedError` | L1·S3 | |
| 00s-05 | test_fake_claude_unconfigured_errors | 非法 | `contract_replay` | `tests/fakes/bin/claude`，未配置场景 | 子进程 `claude -p x` | exit ≠ 0；stderr 含 `no replay scenario`；stdout 为空 | L3·S3 | |
| 00s-06 | test_fake_claude_replays_configured_scenario | 成功 | `contract_replay` | `replay/claude/<sha>.json` | 子进程 `claude -p …` | stdout 与录制逐字节相同；调用日志新增一条，含 argv sha | L3·S1 | |
| 00s-07 | test_gate_cloud_without_ack_usage_error | 非法 | `unit` | pytester 子会话，含一个 `gate_cloud` 用例 | `pytest -m model_integration`，不提供 ACK | 子会话 exit 2；执行的用例数为 0（不是 skip） | L1·S3 | |
| 00s-08 | test_privacy_scan_boundaries | 边界 | `unit` | 内联：17/18/19 位证号（含末位 X）、10/11/12 位手机号、白名单假名 | `privacy_scan` | 只命中 18 位证号和 11 位手机号；17/19 位、10/12 位和白名单都不命中 | L1·S2 | |
| 00s-09 | test_privacy_scan_name_label_nonwhitelist | 非法 | `unit` | `姓名：张三` 与 `姓名：测试患者甲` | `privacy_scan` | 命中前者，不命中后者；exit ≠ 0 | L1·S3 | |
| 00s-10 | test_privacy_scan_unlisted_image | 非法 | `unit` | tmp 仓库：未登记的 png + `docs/wua/x.svg` | `privacy_scan` | exit ≠ 0；报告列出该 png，不列 svg | L2·S3 | |
| 00s-11 | test_privacy_scan_detection_monotone | 边缘 | `unit` | hyp：任意文本 + 在任意位置插入一个合成证号 | 插入前后各扫一次 | 插入后的命中数 ≥ 插入前 + 1（metamorphic） | L1·S6 | |
| 00s-12 | test_launcher_version | 成功 | `cli_integration` | 已建好的 `.venv` | `bin/klode-kit --version` | stdout == `importlib.metadata.version("klode-kit")` | L3·S1 | |
| 00s-13 | test_lane_receipts_concurrent_lanes_no_clobber | 并发 | `unit` | tmp `work/lanes/` | 两个 `run_lane` 进程并发（unit、contract_replay） | 两个 receipt 都存在，各自是合法 JSON，并通过 `lane_receipt` schema | L2·S3 | |

### WI-00a：合成 fixtures 生成器

**Law**：生成是确定的，同种子得到同字节（S5）；swap 注入器保持值的多重集合不变，且恰好 2 个位置不同（S4）；MANIFEST 与 fixtures 树双向一致。
**替身 / 配对**：无。**重构敏感点**：断言的是生成产物的字节和 MANIFEST。
**并发**：不适用。
**lane 调整**：计划把 `test_generation_deterministic` 放在 unit，但这条用例只能在 macOS 上再生成，放在 unit 会在 Linux CI 上被 skip，等于用 skip 冒充 PASS。因此移到 `cli_integration` 的 `macos` 子集。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00a-01 | test_generation_deterministic | 边缘 | `cli_integration` macos | 固定字体和种子 | `gen_all` 分别生成到两个 tmp 目录 | 两边逐文件 sha 相同 | L2·S5 | |
| 00a-02 | test_gen_all_check_matches_manifest | 成功 | `cli_integration` macos | 全部场景 | `gen_all --check` | exit 0；与 `MANIFEST.json` 逐字节一致 | L2·S1 | |
| 00a-03 | test_manifest_complete | 边界 | `unit` | `tests/fixtures/` 整棵树 | 遍历树并读 MANIFEST | 树中每个文件都在 MANIFEST 里且 sha 正确；MANIFEST 没有多余条目（双向一致） | L2·S2 | |
| 00a-04 | test_manifest_detects_tampered_file | 非法 | `unit` | tmp 拷贝后改 1 个字节 | MANIFEST 校验 | 报出被改的文件，exit ≠ 0 | L1·S3 | |
| 00a-05 | test_names_allowlist | 非法 | `unit` | 全部文本和真值 | 抽取人名和编号 | 全部属于白名单；`privacy_scan` 0 命中 | L1·S3 | |
| 00a-06 | test_swap_injector_property | 边缘 | `unit` | hyp：任意表格 × 任意相邻两行 | `inject_swap` | 值的多重集合不变；恰好 2 个位置不同；其余行不变 | L1·S4 | |
| 00a-07 | test_colshift_injector_property | 边缘 | `unit` | hyp：任意表格 × 任意一行 | `inject_colshift` | 该行的数值出现在单位列，数值列为空；其余行不变 | L1·S4 | |
| 00a-08 | test_missingrow_injector_property | 边缘 | `unit` | hyp | `inject_missingrow(route)` | 目标路的行数比原来少 1；其余三路不变 | L1·S4 | |
| 00a-09 | test_degenerate_injector_ratio_boundary | 边界 | `unit` | 构造 `repeat_ratio` 为 0.30 和 0.31 的输出 | `repeat_ratio()` + 判定 | 0.31 判为 degenerate，0.30 不判（阈值是严格大于） | L1·S2 | |
| 00a-10 | test_truth_designed_ge20_all_agree_fields | 边界 | `unit` | 结构化真值 | 统计设计上四路一致的字段 | ≥ 20 | L1·S2 | |
| 00a-11 | test_fixture_scenarios_exist_and_described | 成功 | `unit` | 计划里的场景清单 | 列目录、读 README | 每个场景目录都存在；README 为每个场景写了说明，并声明全部为合成数据 | L2·S1 | |

### WI-00b1：契约 schema 与 receipt 写入

**Law**：receipt 写入再读出要相等（往返，S5）；写入是原子的，崩溃后看到的要么是完整的旧内容，要么是完整的新内容（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是 `jsonschema` 校验结果、磁盘文件和异常类型。
**并发**：00b1-13。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00b1-01 | test_schema_examples_valid | 成功 | `unit` | `tests/contract/schema_examples/*/valid/` | 参数化：16 个 schema 逐一 validate | 所有合法样例都通过 | L1·S2 | |
| 00b1-02 | test_schema_examples_invalid | 非法 | `unit` | `*/invalid/` | 参数化 validate | 每个非法样例都被拒，错误路径正是样例声明的那个字段 | L1·S3 | |
| 00b1-03 | test_choices_partial_rejected | 非法 | `unit` | choices 样例，`kind=partial` | validate | 被拒，错误路径为 `items[0].choice.kind`（r3.3） | L1·S3 | |
| 00b1-04 | test_build_steps_order_exactly_13 | 边界 | `unit` | `build_receipt`：12 项、14 项、13 项但换了顺序、正确的 13 项 | validate | 只有正确的 13 项顺序通过（K8） | L1·S2 | |
| 00b1-05 | test_geometry_enum | 边界 | `unit` | `geometry` 取 exact、approx、""、null | validate | 只接受 exact 和 approx | L1·S2 | |
| 00b1-06 | test_record_status_equals_effective_or_latest_candidate | 边缘 | `unit` | hyp：有或没有 effective 的 record，加上候选序列 | validate | 接受 ⟺ 顶层 `status == effective.status`；没有 effective 时，接受 ⟺ `status == candidates[-1].status`（R4-01） | L1·S4 | |
| 00b1-07 | test_export_map_value_audited_and_transforms_required | 非法 | `unit` | 字段名写成 `value_raw`；缺 `transforms` | validate | 两种都被拒 | L1·S3 | |
| 00b1-08 | test_isolation_receipt_required_bindings | 非法 | `unit` | 分别缺 `cli_version`、`effective_config_sha`、`argv_sha` | validate | 三种各自被拒 | L1·S3 | |
| 00b1-09 | test_lane_receipt_release_sha_required | 非法 | `unit` | `env_fingerprint` 缺 `release_sha` | validate | 被拒 | L1·S3 | |
| 00b1-10 | test_receipt_write_invalid_no_file | 非法 | `unit` | tmp 目录 | `receipts.write(非法内容)` | 抛 `ReceiptInvalid`；目录列表与写入前相同（没有生成文件，也没有 `.tmp`） | L2·S3 | |
| 00b1-11 | test_receipt_crash_no_partial | 边缘 | `unit` | tmp 目录，里面已有旧 receipt | 子进程在 `KLODE_KIT_CRASH_AT=receipts.before_replace` 处被 SIGKILL | 目标文件仍是完整的旧内容，并能通过 schema；不存在写了一半的文件 | L2·S5 | |
| 00b1-12 | test_receipt_roundtrip_property | 边缘 | `unit` | hyp：按 schema 生成的合法 receipt | 写入后读出 | 读出 == 写入 | L1·S5 | |
| 00b1-13 | test_receipt_concurrent_writers_whole_file | 并发 | `unit` | tmp 目录 | 8 个进程同时向同一路径写入不同的合法 receipt | 最终文件恰好等于其中一份的完整内容，并能通过 schema；没有混写 | L2·S5 | |

### WI-00b2：键、指纹与配置

**Law**：
- `batch_id` 不随输入顺序变化（S4）；
- `rec_id` 与日期无关（S6）；
- `cfg_fp` 对每个相关输入都敏感（S4），对等价的配置写法不敏感（S6）；
- `attempt_id` 唯一且连续（S5）。

**替身 / 配对**：时钟注入，不需要配对。**重构敏感点**：断言的是公开函数的返回值和退出码。
**并发**：00b2-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00b2-01 | test_batch_id_permutation_invariant | 边缘 | `unit` | hyp：sha 列表 | 打乱顺序后再算；增删一项后再算 | 打乱后相同；增删一项后不同 | L1·S4 | |
| 00b2-02 | test_batch_id_duplicate_image | 边界 | `unit` | 同一张图出现 1 次和 2 次 | `batch_id()` | **规格待定（§7-F3）**：期望按集合语义处理（重复不改变 id），并在 manifest 中记 `duplicate` | L1·S2 | |
| 00b2-03 | test_rec_id_date_metamorphic | 边缘 | `unit` | hyp：记录 | 只改日期；或只改 doc_id、页、`record_idx` 中的一项 | 只改日期时 rec_id 不变；改后三者中任一项时 rec_id 改变 | L1·S6 | |
| 00b2-04 | test_id_formats | 成功 | `unit` | 固定输入 + 注入时钟 | 生成全部 id | doc_id、page_id、rec_id、build_id、decision_id 都符合规定的正则 | L1·S1 | |
| 00b2-05 | test_cfg_fp_sensitivity | 边缘 | `unit` | hyp：从 r4.4 列出的输入中任选一项变异 | `cfg_fp()` | fp 改变 | L1·S4 | |
| 00b2-06 | test_cfg_fp_canonical | 边缘 | `unit` | 打乱 TOML 键序、增删空白；只改 `config_version` | `cfg_fp()` | fp 不变 | L1·S6 | |
| 00b2-07 | test_cfg_fp_der_sha_order_invariant | 边界 | `unit` | der_sha 的不同排列 | `cfg_fp()` | 结果相同 | L1·S4 | |
| 00b2-08 | test_attempt_concurrent_unique | 并发 | `unit` | tmp 文件系统 | 8 个真进程同时分配 attempt | 拿到的恰好是 `t1…t8`，连续且互不相同；8 个目录都存在 | L2·S5 | |
| 00b2-09 | test_attempt_monotonic_across_redo | 边缘 | `unit` | tmp：已有 `t1` 且为 done | 连续两次 `--redo` | 依次得到 `t2`、`t3` | L2·S5 | |
| 00b2-10 | test_attempt_scoped_by_engine_and_fp | 边界 | `unit` | 两个 engine × 两个 fp | 各分配一次 | 每个 (engine, fp) 组合都从 `t1` 开始 | L2·S2 | |
| 00b2-11 | test_config_override_applies | 成功 | `unit` | `synth_project/klode-kit.toml` 设 `route_retry=5` | load | `route_retry == 5`；其他键保持默认值 | L1·S1 | |
| 00b2-12 | test_config_unknown_key_exit40 | 非法 | `unit` | 配置里有未知键 `routez` | load | 退出码 40；消息中包含 `routez` | L1·S3 | |
| 00b2-13 | test_config_missing_default_key_exit40 | 非法 | `unit` | defaults 缺 `line_sim_tau` | load | 退出码 40 | L1·S3 | |
| 00b2-14 | test_config_wrong_type_exit40 | 非法 | `unit` | `route_retry="two"` | load | 退出码 40 | L1·S3 | |
| 00b2-15 | test_build_id_uses_injected_clock | 边界 | `unit` | 两个不同的时刻，inputs 相同 | `build_id()` | 时间段等于注入值；hash 段相同 | L1·S2 | |

### WI-00b3：来源链校验器 + roundtrip

**Law**：
- `unapply(apply(t, x)) == x`（S5）。
- 校验器对 **r3.1 表中的全部不变量** 完备：任意一条不变量在任意一路上被单点破坏，都能检出（S4）。v2 把损坏域从原来固定的 6 类扩大到全表（缺口 9）。
- rev 前进后，旧 build 仍然自洽（S6）。

**替身 / 配对**：无。**重构敏感点**：断言的是违例 code 的集合。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00b3-01 | test_clean_chain_zero_violations | 成功 | `contract_replay` | `fx-chain` | `provenance.validate()` | 返回 `[]` | L1·S1 | |
| 00b3-02 | test_variant_span_text_ne_candidate | 非法 | `contract_replay` | `fx-chain/broken-1` | validate | 恰好一个违例：`span_mismatch` | L1·S3 | |
| 00b3-03 | test_variant_der_parent_mismatch | 非法 | `contract_replay` | `fx-chain/broken-2` | validate | 恰好一个违例：`der_parent_mismatch` | L1·S3 | |
| 00b3-04 | test_variant_manual_missing_region | 非法 | `contract_replay` | `fx-chain/broken-3` | validate | 恰好一个违例：`manual_evidence_missing` | L1·S3 | |
| 00b3-05 | test_variant_two_effective_decisions | 非法 | `contract_replay` | `fx-chain/broken-4` | validate | 恰好一个违例：`multiple_effective_decisions` | L1·S3 | |
| 00b3-06 | test_variant_alias_on_numeric | 非法 | `contract_replay` | `fx-chain/broken-5` | validate | 恰好一个违例：`transform_on_numeric` | L1·S3 | |
| 00b3-07 | test_variant_export_rev_ne_binding | 非法 | `contract_replay` | `fx-chain/broken-6` | validate | 恰好一个违例：`rev_binding_mismatch` | L1·S3 | |
| 00b3-08 | test_manual_fix_passes_without_any_route_correct | 边界 | `contract_replay` | `fx-chain/legal-manual` | validate | 0 违例；段 ① 对 10.5 仍然成立 | L1·S2 | |
| 00b3-09 | test_legal_variants_pass | 边界 | `contract_replay` | `fx-chain/legal-{dateshift,alias,oldbuild,pdftext_dpi}` | 参数化 validate | 4 个变体都是 0 违例 | L1·S2 | |
| 00b3-10 | test_transforms_apply_unapply_identity | 边缘 | `unit` | hyp：transform 序列 × 修订值 | 先 apply 再 unapply | 回到原值；alias 只作用于 `item_slug`；数值和单位上没有任何 transform | L1·S5 | |
| 00b3-11 | test_advance_rev_keeps_old_build_valid | 边缘 | `contract_replay` | `fx-chain` 追加 rev 2 | 分别对旧 build 校验、对错绑的版本校验 | 旧 build 0 违例；错绑时报 `rev_binding_mismatch` | L1·S6 | |
| 00b3-12 | test_single_corruption_always_detected | 边缘 | `contract_replay` | hyp：`fx-chain` 中任选一条记录 × **r3.1 全部不变量之一**（span、der parent、manual evidence、恰有一条生效决定、route 值等于候选值、记录值等于决定值、导出文本等于 apply 的结果、pdftext 的 dpi、rev 绑定、alias 只作用于 item_slug）× **任选一路（包括两条云端路）** | 损坏后 validate | 至少报出一个对应 code 的违例，没有漏检 | L1·S4 | |
| 00b3-13 | test_validator_is_readonly | 边缘 | `contract_replay` | tmp 拷贝的 `fx-chain` | validate | 校验前后树的 sha 相同 | L2·S1 | |
| 00b3-14 | test_variant_verified_field_no_decision | 非法 | `contract_replay` | 新增 `fx-chain/broken-7`：一个 verified 字段没有任何决定 | validate | 恰好一个违例：`missing_effective_decision`（防止「恰有一条」被实现成 `<= 1`） | L1·S3 | |
| 00b3-15 | test_variant_route_value_ne_candidate | 非法 | `contract_replay` | 新增 `broken-8`：route 决定写入的是★推荐值，而不是 `candidates[route]` 的值 | validate | 恰好一个违例：`route_value_mismatch` | L1·S3 | |
| 00b3-16 | test_variant_record_value_ne_decision | 非法 | `contract_replay` | 新增 `broken-9`：记录 `rev_new` 中的字段值不等于决定的 value | validate | 恰好一个违例：`record_value_mismatch` | L1·S3 | |
| 00b3-17 | test_variant_export_text_ne_applied | 非法 | `contract_replay` | 新增 `broken-10`：导出行的文本在声明位置上不等于 `apply(transforms, 修订值)` | validate | 恰好一个违例：`export_text_mismatch` | L1·S3 | |
| 00b3-18 | test_variant_pdftext_bbox_wrong_dpi | 非法 | `contract_replay` | 新增 `broken-11`：pdftext 路的 bbox 按 72dpi 存储，没有按 `page_map.dpi` 换算 | validate | 恰好一个违例：`pdftext_dpi_mismatch` | L1·S3 | |
| 00b3-19 | test_variant_cloud_route_span_mismatch | 非法 | `contract_replay` | 新增 `broken-12`：codex 路 `bbox_px:null`，且 span 处的文本 ≠ 候选值 | validate | 恰好一个违例：`span_mismatch`，engine=codex（防止只检查有 bbox 的路） | L1·S3 | |

### WI-00c1：云端 argv 构建 + 确定性隔离审查

**Law**：审查对 argv 是单调的——对必含项做任何删除、**改值或重复**，或者以任何写法（包括 `--x=v`）加入禁用项，都会 FAIL（S4，v2 按缺口 7 扩展）；builder 生成的任何 argv 都能通过 checker（S4）；receipt 的有效性绑定在 `(cli_version, effective_config_sha, argv_sha)` 上。
**替身 / 配对**：假 claude/codex 只用来回报 `--version`，配对 00c2-06/07。**重构敏感点**：断言的是 checker 每一项的布尔结果，以及 hook 进程的退出码。
**并发**：00c1-17。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00c1-01 | test_claude_argv_contract | 成功 | `unit` | 默认配置 | `cloud_argv.claude(...)` | 包含全部必含项；与禁用集合（含 `--bare` 等）没有交集 | L1·S1 | |
| 00c1-02 | test_codex_argv_contract | 成功 | `unit` | 默认配置 | `cloud_argv.codex(...)` | 包含 `-C <tmp> -s read-only --ephemeral --ignore-user-config --ignore-rules -c mcp_servers={}`；不含 `--full-auto`、`-s danger*` | L1·S1 | |
| 00c1-03 | test_builder_output_always_passes_checker | 边缘 | `unit` | hyp：model、effort、tmp 名、der_sha | 先 build 再 check | 所有检查项都为 True | L1·S4 | |
| 00c1-04 | test_required_flag_removal_fails | 边缘 | `unit` | hyp：必含项的任意非空子集 | 删除该子集后 check | FAIL；被删的每一项都为 False | L1·S4 | |
| 00c1-05 | test_forbidden_flag_added_fails | 边缘 | `unit` | hyp：在任意位置插入一个禁用项 | check | FAIL，并点名该项 | L1·S4 | |
| 00c1-06 | test_cwd_inside_project_fails | 非法 | `unit` | cwd 为 `project/sub`；或 cwd 是项目外的符号链接、实际指向项目内 | check | 两种都 FAIL（按 realpath 判断） | L1·S3 | |
| 00c1-07 | test_cwd_sibling_prefix_not_inside | 边界 | `unit` | 项目 `/tmp/x/p`，cwd `/tmp/x/p2` | check | 判为「不在项目内」（§7-F1） | L1·S2 | |
| 00c1-08 | test_env_whitelist | 非法 | `unit` | env 中分别含 `CLAUDE_CONFIG_DIR`、**`ANTHROPIC_BASE_URL`**、白名单外的任意键 | check | 三种都 FAIL；只含**显式白名单键**（`PATH HOME TMPDIR LANG ANTHROPIC_API_KEY`）时 PASS。不接受 `ANTHROPIC_*` 通配（§7-F15） | L1·S3 | |
| 00c1-09 | test_tmpdir_extra_file_or_unlisted_png_fails | 非法 | `unit` | 目录里多一个文件；或 png 的 sha 不在登记表中 | check | 两种都 FAIL | L1·S3 | |
| 00c1-10 | test_cloud_settings_sha_mismatch_fails | 非法 | `unit` | 插件里的 `cloud_settings.json` 被改了 1 个字节 | check | FAIL | L1·S3 | |
| 00c1-11 | test_cli_version_boundary | 边界 | `unit` | 假 `claude --version` 依次输出 2.1.258、2.1.259、2.1.1000 | check | 依次为 FAIL、PASS、PASS | L1·S2 | |
| 00c1-12 | test_receipt_invalid_on_binding_change | 边缘 | `unit` | 已 PASS 的 receipt；三个绑定项中任一项变化 | `receipt_still_valid(env)` | 有任一项变化时为 False；都不变时为 True | L1·S3 | |
| 00c1-13 | test_guard_hook_subprocess_outside_denied | 非法 | `contract_replay` | stdin 中的 `file_path` 位于 cwd 之外 | 真子进程运行 `isolation_guard` | exit 2；日志新增一行 `deny` | L3·S3 | |
| 00c1-14 | test_guard_hook_inside_allowed | 成功 | `contract_replay` | `file_path` 位于 cwd 内 | 同上 | exit 0；日志一行 `allow` | L3·S1 | |
| 00c1-15 | test_guard_hook_malformed_fail_closed | 非法 | `contract_replay` | stdin 为 `{`、空、缺 `file_path` | 同上 | 三种都 exit 2 | L3·S3 | |
| 00c1-16 | test_guard_hook_traversal_and_symlink | 边界 | `contract_replay` | `cwd/../x`；`cwd/link` 指向外部 | 同上 | 两种都 exit 2 | L3·S2 | |
| 00c1-17 | test_guard_concurrent_log_intact | 并发 | `contract_replay` | 20 个 hook 进程并发 | 同上 | 日志恰好 20 行，每行都是完整 JSON | L2·S3 | |
| 00c1-18 | test_required_flag_value_mutation_or_duplicate_fails | 边缘 | `unit` | hyp：三种变异任选其一：① 把某个必含 flag 的值换掉（如 `--tools "Read,Bash"`、`--permission-mode bypassPermissions`）；② 在末尾追加一个同名、值不同的 flag；③ 把禁用项写成 `--x=v` 的形式（如 `--mcp-config=x.json`） | check | FAIL，并点名对应的检查项 | L1·S4 | |
| 00c1-19 | test_settings_arg_must_be_pinned_file | 非法 | `unit` | argv 中 `--settings` 指向一个 tmp 下内容不同的 permissive.json，而插件里的固定文件没有改 | check | FAIL（判断的是 argv 实际指向的文件，而不是插件里的固定路径） | L1·S3 | |
| 00c1-20 | test_codex_version_boundary | 边界 | `unit` | 假 `codex --version` 依次输出：锁定值−1、锁定值、跨位数的值（如 0.9 和 0.10） | check | 依次为 FAIL、PASS，跨位数时按数值比较，不按字典序 | L1·S2 | |

### WI-00c2：隔离实测 spike【G2】

**Law**：判定只看宿主证据，不看模型说了什么。只有所有 canary 都是 denied 且输出中没有 nonce，才算 PASS；「未尝试」和 ENOENT 都判 FAIL（用 S2/S3 表驱动覆盖）。
**替身 / 配对**：`replay/probe-synth/`（新增）是手写的合成事件流，配对 00c2-06/07/08。**重构敏感点**：断言的是分类结果和 receipt。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00c2-01 | test_canary_path_preexists | 非法 | `unit` | 故意少建一个 canary | `isolation_probe.prepare()` | 在任何调用之前就抛异常；假 claude 的调用日志为空 | L2·S3 | |
| 00c2-02 | test_classify_denied_enoent_unattempted | 边界 | `unit` | `probe-synth/{denied,enoent,unattempted,mixed}.jsonl` | `classify` | 前三个分别归为 denied、ENOENT、unattempted；mixed 为 FAIL；只有全部 denied 才 PASS | L1·S2 | |
| 00c2-03 | test_classify_ignores_model_narrative | 非法 | `unit` | 模型文本自称「已拒绝」，但 tool_result 显示读取成功并含 nonce | classify | FAIL | L1·S3 | |
| 00c2-04 | test_classify_codex_json_events | 成功 | `unit` | `codex-denied.jsonl` | classify | PASS | L1·S1 | |
| 00c2-05 | test_canary_cleanup_only_own_files | 边缘 | `unit` | tmp HOME 中预先放一个 `canary-old.txt` | 跑 probe | 只删除本次 nonce 对应的文件；`canary-old.txt` 字节不变 | L2·S3 | |
| 00c2-06 | test_claude_probe_denials | 成功 | `model_integration` | `synth_project` + `$HOME` 下的 canary | 真 claude | 全部 denied；输出中没有 nonce；`cloud_isolation: PASS` | L3·S3 | G2 |
| 00c2-07 | test_codex_probe_denials | 成功 | `model_integration` | 同上 | 真 codex | 同上 | L3·S3 | G2 |
| 00c2-08 | test_startup_context_nonce_absent | 边缘 | `model_integration` | 在祖先目录的 CLAUDE.md 和 `.claude/` 中放 nonce | 真 claude | 模型输出和 init/system 事件中都没有 nonce；拿不到证据时判 inconclusive，按 FAIL 处理 | L3·S3 | G2 |
| 00c2-09 | test_isolation_receipt_written_and_valid | 成功 | `model_integration` | 06/07 的产物 | 读 receipt | 通过 schema；`argv_sha` 与实际执行的 argv 一致；`probe.cost_usd` 非空 | L2·S1 | G2 |
| 00c2-10 | test_route_disabled_when_probe_fails | 边缘 | `unit` | probe-synth 中 codex 读到了 canary | 根据 classify 结果更新配置 | `routes_enabled.codex=false`，claude 不受影响；之后再发给 codex 时 exit 61 | L1·S3 | |

### WI-00c3：`eval_fields` / `cer` + 四路 spike【G2 只在实跑那一步】

**Law**：对真值加扰动，指标单调不变好（S4）；`cer(x,x)=0` 且 `cer ≥ 0`（S4）；数字只做 NFKC，不做别的折叠（S6）。
**替身 / 配对**：00c3-10 用假 claude，配对 00c3-09。**重构敏感点**：断言的是指标数值。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00c3-01 | test_false_auto_pass_zero_samples_fails | 非法 | `unit` | `auto_verified_total=0` | eval | 结果为 `n/a`；FAIL，原因 `no_auto_verified_samples` | L1·S3 | |
| 00c3-02 | test_metrics_perfect_on_truth | 成功 | `unit` | 真值对真值 | eval | 四项指标都是理想值 | L1·S1 | |
| 00c3-03 | test_metrics_on_truth_vs_perturbed | 边缘 | `unit` | hyp：逐步加大的扰动 | eval | 精确率单调不升，缺字段率单调不降 | L1·S4 | |
| 00c3-04 | test_false_auto_pass_one_wrong_fails | 边界 | `unit` | 20 个里错 1 个 | eval | 0.05，FAIL | L1·S2 | |
| 00c3-05 | test_all_agree_fields_19_vs_20 | 边界 | `unit` | 19 个和 20 个 | eval | 19 FAIL，20 PASS | L1·S2 | |
| 00c3-06 | test_cer_selftest_port | 成功 | `unit` | 189 的自测向量 | `cer()` | 结果相等 | L1·S1 | |
| 00c3-07 | test_cer_numeric_not_folded | 边缘 | `unit` | 「10.5」对「1.05」；「１０.５」对「10.5」 | `cer()` | 前者 > 0；后者 == 0 | L1·S6 | |
| 00c3-08 | test_cer_identity_and_nonneg | 边缘 | `unit` | hyp：字符串对 | `cer()` | `cer(x,x)==0`；`cer ≥ 0`；空串时行为确定 | L1·S4 | |
| 00c3-09 | test_hardest_pages_4routes_live | 成功 | `model_integration` | 最难的 4 个场景（打码后） | 四路实跑 | 录制带 `recorded:true`；错误自动通过率为 0；四路一致字段 ≥ 20；spike 文档里有结论 | L3·S1 | G2 |
| 00c3-10 | test_live_requires_isolation_receipt | 非法 | `unit` | 没有有效的隔离 receipt | `KLODE_KIT_REAL=1` | exit 61；假 claude 日志为空 | L2·S3 | |

### WI-00d1：执行桥（确定性部分）

**Law**：任意任务数和并发上限下，同时运行的数量都 ≤ 上限（S4）；超时或取消后，不留任何存活的子孙进程。
**替身 / 配对**：假 claude 的各种场景，配对 00d2-01/02。**重构敏感点**：断言的是进程是否存活、时间戳的重叠和 receipt。
**并发**：00d1-06、00d1-07。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00d1-01 | test_argv_contract | 成功 | `unit` | `agents/*.md` | `build_argv` | 必含参数齐全；cwd 为 `builds/<id>/`；没有 `--force`；内联 JSON 能解析出 klode-card-writer | L1·S2 | |
| 00d1-02 | test_allowed_tools_use_edit_not_write | 非法 | `unit` | 同上 | build_argv | 没有任何 `Write(` 规则；checker 走启动器调用 | L1·S3 | |
| 00d1-03 | test_success_writes_receipt_with_cost | 成功 | `contract_replay` | 假 claude 返回成功 | run | receipt 为 done、通过 schema、`cost_usd` 非空；卡文件存在 | L2·S1 | |
| 00d1-04 | test_timeout_kills_process_group | 边缘 | `contract_replay` | 假 claude 永久 sleep，并派生一个孙进程 | run，timeout=1s | 子进程和孙进程都已不存在（ESRCH）；receipt 为 timeout | L4·S3 | |
| 00d1-05 | test_timeout_boundary | 边界 | `contract_replay` | 假 claude sleep 0.5s 和 1.5s，timeout=1s | run | 前者 done，后者 timeout | L2·S2 | |
| 00d1-06 | test_concurrency_bound | 并发 | `contract_replay` | hyp：任务数 1–12、并发上限 1–4 | run | 最大重叠数 ≤ 上限；任务数 ≥ 上限时，重叠数恰好达到上限 | L2·S4 | |
| 00d1-07 | test_cancel_propagates | 并发 | `contract_replay` | 10 个任务正在运行 | 向父进程发 SIGINT | 所有子进程都已不存在；receipt 为 `cancelled`；没有写了一半的卡 | L4·S3 | |
| 00d1-08 | test_malformed_retry_exhausted | 边缘 | `contract_replay` | 假 claude 总是输出畸形 JSON | run | 尝试次数 == 3；`parse_error` | L1·S3 | |
| 00d1-09 | test_malformed_then_success | 边缘 | `contract_replay` | 第一次畸形，第二次正常 | run | done；尝试 2 次；只有一张卡 | L1·S2 | |
| 00d1-10 | test_exit1_failure_recorded | 非法 | `contract_replay` | 假 claude exit 1 | run | failed，`exit_code=1`；重试次数不超过上限 | L1·S3 | |
| 00d1-11 | test_zero_tasks | 边界 | `unit` | 空任务列表 | run | 立即返回，receipt 中任务数为 0；假 claude 日志为空 | L1·S2 | |

### WI-00d2：宿主 spike【G2、G5】

**Law**：No law：这是对宿主行为的一次性取证。
**替身 / 配对**：00d2-08 用录制的输出，配对 00d2-06。**重构敏感点**：断言的是宿主侧的产物。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00d2-01 | test_bridge_writes_one_card_check_pass | 成功 | `model_integration` | `fx-book-aurora` 第 1 章 | 通过 CLI 桥真实写卡 | 卡文件存在；check_cards PASS；`cost_usd` 非空 | L3·S1 | G2 |
| 00d2-02 | test_bridge_edit_library_denied | 非法 | `model_integration` | 同上，任务中要求写 `library/x` | 同上 | `permission_denials` 中包含该路径；`library/x` 不存在 | L3·S3 | G2 |
| 00d2-03 | test_workflow_stage1_exit10_awaiting | 成功 | `host_acceptance` | `fx-u3-mixed`，`_lab` | `/klode-kit:kb-pipeline` | exit 10；状态为 `awaiting_confirm` | L3·S1 | G2 G5 |
| 00d2-04 | test_workflow_resume_hits_cache | 边缘 | `host_acceptance` | 接着 03 | `args.resume` | 第二段完成；前三个阶段的 receipt sha 不变 | L3·S1 | G2 G5 |
| 00d2-05 | test_trust_probe | 边界 | `host_acceptance` | `_lab` 和新建的 tmp 目录 | `hostenv.trusted()` | 依次为 True、False；无法判断时返回 `undetectable` | L3·S2 | G5 |
| 00d2-06 | test_mcp_get_env_key_connected | 边缘 | `host_acceptance` | `.mcp.json` 中带 env 键 | 真 `claude mcp get` | 结果为 connected | L3·S1 | G5 |
| 00d2-07 | test_kb_check_entry_plugin_fields | 成功 | `host_acceptance` | `tests/fixtures/kb/fx-labs-3x` | `claude -p --plugin-dir . "/klode-kit:kb-check …"` | `plugin.{name,version,entry}` 正确 | L3·S1 | G2 G5 |
| 00d2-08 | test_hostenv_parse_mcp_get_variants | 边界 | `unit` | 录制的 connected、Pending approval、failed 三种输出 | parse | 依次解析为 connected、pending、failed | L1·S2 | |

### WI-00e：三张 WUA 图

**Law**：No law：只做静态检查。
**替身 / 配对**：无。**重构敏感点**：断言的是 `.wf.json` 的节点、边和退出码。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00e-01 | test_wfviz_renders | 成功 | `cli_integration` | `docs/wua/*.wf.json` | 运行 wfviz | 全部 exit 0；SVG 非空且是合法 XML | L3·S1 | |
| 00e-02 | test_scenarios_exactly_6 | 边界 | `unit` | scenarios | 解析 | 恰好 6 个场景 | L1·S2 | |
| 00e-03 | test_usecases_cover_u1_u8 | 边界 | `unit` | usecases | 解析 | 集合等于 {U1…U8} | L1·S2 | |
| 00e-04 | test_pipeline_stages_and_single_human_step | 成功 | `unit` | pipeline | 解析 | 7 个阶段齐全；人工步骤只有「点选」 | L1·S2 | |
| 00e-05 | test_loops_back_edges | 边缘 | `unit` | 同上 | 解析 | 每条 loop 的 from 都在 to 之后 | L1·S2 | |
| 00e-06 | test_state_machine_drawn | 成功 | `unit` | 同上 | 解析 | 状态节点和边齐全，并有 effective / candidate 说明节点 | L1·S1 | |
| 00e-07 | test_no_forbidden_keys_and_all_artifacts_referenced | 非法 | `unit` | 同上 | 解析 | 没有禁用键；每个工件都被引用过 | L1·S3 | |
| 00e-08 | test_wfviz_src_missing_fails | 非法 | `cli_integration` | 不设置 `WFVIZ_SRC` | pytester | 结果为 failed，而不是 skipped | L1·S3 | |

---

## 3. M1 · OCR + 审计

### WI-04a：本机引擎 + 几何

**Law**：坐标往返误差 ≤ 1px（S5）。
**替身 / 配对**：引擎回放，配对 04a-10/11/12。**重构敏感点**：断言的是 `ocr_line` 的输出。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 04a-01 | test_geometry_roundtrip | 边缘 | `unit` | hyp：图尺寸 × 框 × 三种坐标系 | 转过去再转回来 | 误差 ≤ 1px | L1·S5 | |
| 04a-02 | test_vision_bottom_left_flip | 边界 | `unit` | y=0 和 y=1 | `vision_to_px` | 分别落在图底和图顶 | L1·S2 | |
| 04a-03 | test_mlx_0_999_bounds | 边界 | `unit` | 0 和 999 | `mlx_to_px` | 不越界 | L1·S2 | |
| 04a-04 | test_engine_replay_conforms | 成功 | `unit` | `fx-labs-1x` 三个引擎的回放 | 转换 | 通过 schema；bbox 是 4 个整数 | L1·S2 | |
| 04a-05 | test_mlx_tokens_null_block_bbox | 成功 | `unit` | MLX 回放 | 转换 | `tokens is None`；`block_bbox` 非空 | L1·S1 | |
| 04a-06 | test_approx_marked | 成功 | `unit` | observation 级的回放 | 转换 | 全部为 `geometry=approx` | L1·S1 | |
| 04a-07 | test_degenerate_fallback_chain | 边缘 | `unit` | MLX 主、备两次都退化 | `ocr_page` | 最终用 tesseract；记 `degenerate_output`；尝试顺序正确 | L1·S3 | |
| 04a-08 | test_degenerate_threshold_boundary | 边界 | `unit` | 0.30 和 0.31 | `ocr_page` | 0.30 不触发，0.31 触发 | L1·S2 | |
| 04a-09 | test_all_fallbacks_degenerate | 非法 | `unit` | 三个引擎都退化 | `ocr_page` | 页级 `route_failed`；不产出空的 JSONL | L1·S3 | |
| 04a-10 | test_real_ocrmac_bounds | 成功 | `cli_integration` macos | 全部 fx 图 | 真 ocrmac | 所有 bbox 都在图内 | L3·S4 | G4 |
| 04a-11 | test_real_tesseract_tsv_bounds | 成功 | `cli_integration` | 全部 fx 图 | 真 tesseract | 所有 bbox 在图内，且为 exact | L3·S4 | G4 |
| 04a-12 | test_real_mlx_smoke | 成功 | `cli_integration` macos_mlx | `fx-labs-1x` | 真 MLX | 通过 schema；缺权重时 FAIL（不是 skip） | L3·S1 | G4 |

### WI-04b：`run_batch` + 方向转正

**Law**：页守恒（S5）；redo 不改动旧的 attempt（S5）；续跑不产生重复（S5）。
**替身 / 配对**：引擎回放 + 假 claude/codex，配对 04a-10 和 06-11/12。**重构敏感点**：断言的是目录树、receipt 和替身日志的条目数。
**并发**：04b-12。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 04b-01 | test_rotate_receipt_u2 | 成功 | `contract_replay` | `fx-rotated90` | `kb-ocr --batch` | 每页 `rotate: 90`；derived 中有 rotate 记录，parent 正确 | L2·S1 | |
| 04b-02 | test_orient_all_four | 边界 | `unit` | tmp：四个方向各一份 | `orient.detect` | 四个方向都判对 | L1·S2 | |
| 04b-03 | test_run_dir_layout | 成功 | `contract_replay` | `fx-labs-1x` | kb-ocr | `ocr/<engine>/<cfg_fp>.t1/p001.jsonl` 存在 | L2·S1 | |
| 04b-04 | test_page_conservation | 边缘 | `contract_replay` | hyp：页 × 路 × 失败位置 | kb-ocr | 每个 (页, 路) 恰好有一个终态 | L2·S5 | |
| 04b-05 | test_resume_after_kill | 边缘 | `contract_replay` | `fx-u1-3labs` | 跑完第 1 页后 SIGKILL，再续跑 | 第 1 页只被调用了 1 次；没有重复行 | L2·S5 | |
| 04b-06 | test_redo_new_attempt_old_untouched | 边缘 | `contract_replay` | `fx-redo` | `--redo` | 生成 `t2`；`t1` 的树 sha 不变 | L2·S5 | |
| 04b-07 | test_resume_reuses_only_three_hash_match | 边界 | `contract_replay` | `t1` 的某个产物被改动 | resume | 新建 `t2` | L2·S3 | |
| 04b-08 | test_route_retry_then_failed | 非法 | `contract_replay` | 假 codex 总是 exit 1 | kb-ocr | 调用次数为 1 + route_retry；记 `route_failed` | L1·S3 | |
| 04b-09 | test_missing_page_explicit | 非法 | `contract_replay` | `fx-redo` | kb-ocr | 记 `route_missing_page:codex:p001` | L2·S3 | |
| 04b-10 | test_empty_inbox | 边界 | `unit` | tmp 空目录 | kb-ocr | **规格待定（§7-F4）**：期望 exit 2，不建任何目录 | L1·S3 | |
| 04b-11 | test_non_image_file_listed | 非法 | `unit` | inbox 里有一个 `.txt` | kb-ocr | **规格待定（§7-F4）**：期望出现在 `skipped[]` 中 | L1·S3 | |
| 04b-12 | test_concurrent_runs_same_batch_locked | 并发 | `contract_replay` | `fx-labs-1x` | 两个真进程同时跑 | 第二个 exit 40；第一个的产物完整，没有交错 | L2·S3 | |
| 04b-13 | test_atomic_page_write | 边缘 | `contract_replay` | `fx-labs-1x` | 在 CRASH_AT 点 SIGKILL，再续跑 | 没有写了一半的 jsonl；续跑后结果与正常跑逐字节相同 | L2·S5 | |

### WI-04c：文字层 PDF 分流

**Law**：page_map 往返（S5）；`px = pt × dpi / 72`（S4）。
**替身 / 配对**：云端路用回放，配对 06-11/12。**重构敏感点**：断言的是分类结果和产物。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 04c-01 | test_classify_boundaries | 边界 | `unit` | 49/50 个字符；乱码率 0.05 与 0.0501 | `classify_page` | 49 判 empty，50 判 text；0.05 判 text，0.0501 判 garbled | L1·S2 | |
| 04c-02 | test_bbox_pt_to_px | 边缘 | `unit` | hyp | 换算 | 等于 `pt*dpi/72` | L1·S4 | |
| 04c-03 | test_whole_pagemap_roundtrip | 边缘 | `unit` | hyp：多页文本 | 合并后反查 | 每一行都能回到原页原文 | L1·S5 | |
| 04c-04 | test_text_pdf_receipt_u4 | 成功 | `cli_integration` | `fx-text-pdf` | 真 pdftotext | `text_layer: true`；跳过四路 | L3·S1 | |
| 04c-05 | test_real_pdftotext_mixed | 成功 | `cli_integration` | `fx-mixed-pdf` | kb-ocr | 每页分类正确；empty 和 garbled 页走四路 | L3·S1 | |
| 04c-06 | test_corrupt_pdf_no_partial | 非法 | `cli_integration` | 新增 `fx-corrupt-pdf` | kb-ocr | exit ≠ 0；记 `pdf_unreadable`；没有任何产物 | L2·S3 | |
| 04c-07 | test_zero_text_pages_all_routed | 边界 | `cli_integration` | 只含扫描页的子集 | kb-ocr | `text_layer: false`；没有跳过任何路 | L2·S2 | |

### WI-05a：抬头打码

**Law**：像素守恒（S4）；MANIFEST 幂等（S5）。
**替身 / 配对**：Vision 回放，配对 05a-07。**重构敏感点**：断言的是像素和 MANIFEST。
**并发**：05a-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 05a-01 | test_mask_pixel_conservation | 边缘 | `unit` | hyp：任意的框 | `mask.apply` | 框内像素全为 0；框外像素与原图完全相同 | L2·S4 | |
| 05a-02 | test_manifest_row_fields | 成功 | `unit` | `fx-rx-stamp` | mask | 每行 5 个字段齐全；sha 和 parent 正确 | L2·S1 | |
| 05a-03 | test_manifest_idempotent | 边缘 | `unit` | 同上 | 跑两次 | 行数不变；png 字节相同 | L2·S5 | |
| 05a-04 | test_mask_box_clipped_at_border | 边界 | `unit` | 框有一半在图外 | mask | 不抛异常；图内部分为 0 | L1·S2 | |
| 05a-05 | test_no_keyword_header_region_masked | 边界 | `unit` | 页面上没有关键词 | mask | 页顶区域被打码 | L1·S2 | |
| 05a-06 | test_review_images_only_in_review_dir | 非法 | `unit` | `fx-rx-stamp` | mask | `_masked/` 下只有 `.m.png` 和 MANIFEST；对照图只在 `_mask_review/` | L2·S3 | |
| 05a-07 | test_masked_text_absent | 成功 | `cli_integration` macos | `fx-u1-3labs` | 用真 Vision 再识别一次 | 识别结果中没有假名和假编号 | L3·S3 | G4 |
| 05a-08 | test_concurrent_mask_manifest | 并发 | `unit` | `fx-u1-3labs` | 两个进程同时打码 | MANIFEST 没有重复行，每行都是完整 JSON | L2·S5 | |

### WI-05b：发送器契约 + `privacy_guard`

**Law（K6）**：发生调用 ⟺ 发送那一刻**实际送出的副本**的 sha 在登记表中，否则 exit 60 且零调用（S4）；实际执行的 argv 就是审查过的那份（v2 新增，缺口 8）。
**替身 / 配对**：假 claude/codex 记录收到图的 sha、完整 argv 和目录列表，配对 06-11/12 和 00c2-06/07。**重构敏感点**：断言的是退出码、假可执行文件的日志内容和文件系统。
**并发**：05b-08（v2 改为确定性用例）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 05b-01 | test_listed_sha_sends | 成功 | `contract_replay` | `_masked` MANIFEST + 假 claude | send | 调用 1 次，收到的 sha 等于登记值；临时目录已删除；receipt 中没有路径 | L2·S1 | |
| 05b-02 | test_unlisted_sha_exit60_no_spawn | 非法 | `contract_replay` | 未登记的 png | send | exit 60；0 次调用 | L2·S3 | |
| 05b-03 | test_listed_path_tampered_bytes | 非法 | `contract_replay` | 路径在登记表中，但字节被改过 | send | exit 60；0 次调用 | L2·S3 | |
| 05b-04 | test_review_dir_rejected | 非法 | `contract_replay` | `_mask_review/` 下 sha 已登记的副本 | send | exit 60；0 次调用 | L2·S3 | |
| 05b-05 | test_fixture_manifest_sha_accepted | 边界 | `contract_replay` | fixtures 中的图 | send | 调用 1 次 | L2·S2 | |
| 05b-06 | test_invalid_isolation_receipt_exit61 | 非法 | `contract_replay` | 过期的 receipt | send | exit 61；0 次调用 | L2·S3 | |
| 05b-07 | test_send_only_manifest_property | 边缘 | `contract_replay` | hyp：文件集 × 登记子集 × 篡改子集 | 逐个 send | 发生调用 ⟺ sha 在登记表中；拒绝时一律 exit 60 且 0 次调用 | L2·S4 | |
| 05b-08 | test_toctou_swap_after_check_deterministic | 并发 | `contract_replay` | 已登记的图；`KLODE_KIT_PAUSE_AT=send.after_source_check` | 在暂停点把源文件换成未登记的字节，然后放行 | 结果是确定的，只有两种：exit 60 且假 claude 日志 0 条；或者假 claude 收到的 sha 在登记表中。连续跑 20 次结果一致。（v2：取代 v1 中靠线程时序的竞态写法，理由见 §7-F16） | L2·S3 | |
| 05b-09 | test_tmpdir_properties | 边界 | `contract_replay` | 假 claude 记录 cwd 的 stat 和目录列表 | send | cwd 在 TMPDIR 下且在项目外；权限 0700；恰好 3 个文件 | L2·S2 | |
| 05b-10 | test_tmpdir_cleanup_on_exception | 边缘 | `contract_replay` | 假 claude 异常退出；或 KeyboardInterrupt | send | 临时目录不存在 | L2·S3 | |
| 05b-11 | test_receipt_no_path_leak | 边缘 | `unit` | hyp：各种路径 | 生成 receipt | 不含路径的任何组成部分 | L1·S4 | |
| 05b-12 | test_hook_subprocess_unlisted_exit2 | 非法 | `contract_replay` | 真 hook，图未登记 | `privacy_guard` | exit 2 | L3·S3 | |
| 05b-13 | test_hook_listed_allows | 成功 | `contract_replay` | 图已登记 | 同上 | exit 0 | L3·S1 | |
| 05b-14 | test_hook_absent_sender_still_enforces | 边缘 | `contract_replay` | 不加载 hooks.json | send 一张未登记的图 | exit 60；0 次调用 | L2·S3 | |
| 05b-15 | test_executed_argv_equals_checked_argv | 边缘 | `contract_replay` | 已登记的图；参数化 claude 和 codex 两路 | send | 假可执行文件记录的完整 argv 的 sha 等于 `isolation_receipt.argv_sha`；用这份 argv 重新跑 `isolation_check`，结果为 PASS | L2·S3 | |

### WI-06a / WI-06b：Claude / Codex 读图适配器

**Law**：任意费用序列下，累计费用 ≤ 上限 + 预留余量；超限后不再发送（S4）。
**替身 / 配对**：回放，配对 06-11/12。**重构敏感点**：断言的是 JSONL、receipt 和退出码。
**并发**：06-10。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 06-01 | test_replay_conforms[claude] | 成功 | `contract_replay` | `replay/claude/*` | 适配器 | 通过 schema；`bbox_px` 和 `tokens` 为 null | L1·S2 | |
| 06-02 | test_replay_conforms[codex] | 成功 | `contract_replay` | `replay/codex/*` | 适配器 | 同上 | L1·S2 | |
| 06-03 | test_timeout_route_failed | 非法 | `contract_replay` | sleep 超过超时时间 | 适配器 | 记 `route_failed`；页级 uncertain | L1·S3 | |
| 06-04 | test_schema_invalid_retry_once_then_parse_error | 边缘 | `contract_replay` | 两次都返回畸形输出 | 适配器 | 调用 2 次；记 `parse_error` | L1·S3 | |
| 06-05 | test_schema_invalid_then_valid | 边缘 | `contract_replay` | 第一次畸形，第二次正常 | 适配器 | 调用 2 次；产出合法 JSONL | L1·S2 | |
| 06-06 | test_budget_stop_exit50 | 边缘 | `contract_replay` | 上限 0.10，每次 0.04 | 发送 5 张 | 调用 3 次后 exit 50 | L2·S3 | |
| 06-07 | test_budget_boundary_exact | 边界 | `contract_replay` | 累计恰好等于 0.10 | 发送 | 不触发 exit 50 | L2·S2 | |
| 06-08 | test_codex_argv_effort_and_output | 成功 | `unit` | 锁定的 effort 值 | 构造 argv | 含 `-o out.json --json -c model_reasoning_effort=<锁定值>` | L1·S1 | |
| 06-09 | test_model_actual_from_response | 成功 | `contract_replay` | 回放中的 model 与配置不同 | 适配器 | `model_actual` 取自响应 | L1·S1 | |
| 06-10 | test_parallel_routes_budget_shared | 并发 | `contract_replay` | hyp：两路并发 | 并发发送 | 累计费用 ≤ 上限 + 预留；超限后两路都不再发 | L2·S4 | |
| 06-11 | test_live_vs_replay_shape[claude] | 成功 | `model_integration` | `fx-labs-1x` 打码后 | 真实调用 | 键和类型与回放一致 | L3·S1 | G2 |
| 06-12 | test_live_vs_replay_shape[codex] | 成功 | `model_integration` | 同上 | 同上 | 同上 | L3·S1 | G2 |

### WI-07a：字段抽取

**Law**：`text[s:e] == value`（S4）；全角与半角等价（S6）。
**替身 / 配对**：无。**重构敏感点**：断言的是字段列表。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 07a-01 | test_span_property | 边缘 | `unit` | hyp：拼接出的行 | extract | 每个字段都满足 `line[s:e]==value` | L1·S4 | |
| 07a-02 | test_fullwidth_metamorphic | 边缘 | `unit` | hyp：行及其全角版本 | extract | NFKC 之后结果相同 | L1·S6 | |
| 07a-03 | test_10_5_vs_1_05_distinct | 非法 | `unit` | 「10.5」与「1.05」 | extract | 两者不相等 | L1·S3 | |
| 07a-04 | test_recognize_number_forms | 成功 | `unit` | 四种数值写法 | extract | 类型和值逐项正确 | L1·S2 | |
| 07a-05 | test_dates_three_forms | 成功 | `unit` | 三种日期写法 | extract | 都归一为 2026-09-18 | L1·S2 | |
| 07a-06 | test_flags_boundary | 边界 | `unit` | H、L、↑、↓、*；以及「HGB」 | extract | 前五个识别为 flag；HGB 里的 H 不算 | L1·S2 | |
| 07a-07 | test_drug_dose | 成功 | `unit` | `示例片 10mg` | extract | 药名和剂量正确 | L1·S1 | |
| 07a-08 | test_empty_and_noise_line | 边界 | `unit` | 空串和 `-----` | extract | `[]` | L1·S2 | |

### WI-07b：单元格对齐 + `items.yaml`

**Law**：对齐结果**恰好恢复已知的对应关系**：对任意已知位置的删行、插行、拼行，得到的 pairs 都等于真实对应，`unaligned` 落在真正缺失的行上（S4，v2 按缺口 3 改写，取代原来「下标对严格递增」这一弱 law，后者现在只是推论）。另外，非基准路的排列顺序不影响结果（S6）。
**替身 / 配对**：引擎回放，配对 04a-10/11。**重构敏感点**：断言的是 cell 集合、pairs 和 uncertain 的位置。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 07b-01 | test_base_route_first_exact | 成功 | `unit` | vision 只有 approx，tesseract 是 exact | align | 基准路为 tesseract | L1·S2 | |
| 07b-02 | test_row_merge_overlap_boundary | 边界 | `unit` | 重叠 49% 和 50% | align | 49% 分成两行，50% 合为一行 | L1·S2 | |
| 07b-03 | test_col_cluster_by_x0 | 成功 | `unit` | `fx-labs-1x` | align | 列 id 与真值一致 | L1·S1 | |
| 07b-04 | test_approx_without_exact_no_geometry | 非法 | `unit` | 只有 approx 的行 | align | 每个 cell 都标 `no_geometry` | L1·S3 | |
| 07b-05 | test_approx_with_exact_corroboration_used | 边界 | `unit` | 同一行另有 exact 的框 | align | 使用 approx 的框 | L1·S2 | |
| 07b-06 | test_alignment_monotonic | 边缘 | `unit` | hyp | align | 下标对严格递增（07b-12 的推论，保留作为快速回归） | L1·S4 | |
| 07b-07 | test_unaligned_and_merged_rows | 非法 | `unit` | `fx-missingrow`；另有一行是基准两行拼成的 | align | **v2 加强**：`unaligned:<engine>` 标在 fixture 声明的那一行（例如第 5 行），其余各行都正确对上；`merged_rows` 恰好标在被拼的两行上 | L1·S3 | |
| 07b-08 | test_duplicate_values_keep_ordinal | 边界 | `unit` | `fx-dup10` | align | 两个 cell 的 ordinal 分别为 0 和 1 | L1·S2 | |
| 07b-09 | test_nonbase_engine_permutation_invariant | 边缘 | `unit` | hyp：排列 | align | cell 集合相同 | L1·S6 | |
| 07b-10 | test_items_alias_normalize_and_new_items | 成功 | `unit` | HGB；以及一个未知项目 | 加载 | 归一化正确；未知项目进入 `new_items` | L1·S2 | |
| 07b-11 | test_items_duplicate_alias_rejected | 非法 | `unit` | 同一个别名挂在两个项目下 | 加载 | 退出码 40 | L1·S3 | |
| 07b-12 | test_alignment_recovers_known_correspondence | 边缘 | `unit` | hyp：生成基准行，在云端路的已知位置做删行、插行或拼行，并记录真实的对应关系（相邻行的值允许相同，覆盖 dup10 形态） | align | pairs 与真实对应**完全相等**；`unaligned` 落在被删的行上；`merged_rows` 落在被拼的行上；不会出现缺行之后整体错位一格 | L1·S4 | |

### WI-07c：票型比对 + 白名单

**Law（K1）**：agree ⟺ 四路齐、值全部相等、该行没有行级 uncertain（S4）；推荐值一定在候选值之中（S4）；结果与路的顺序无关（S6）。
**替身 / 配对**：无。**重构敏感点**：断言的是票型。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 07c-01 | test_dup10_two_cells_agree | 边界 | `contract_replay` | `fields/dup10` | compare | 两个 cell 各自 4/4 agree，不被合并 | L1·S3 | |
| 07c-02 | test_colshift_disputed_with_item_label | 非法 | `contract_replay` | `fields/colshift` | compare | 分歧，且带 `item_label` | L1·S3 | |
| 07c-03 | test_swap_suspect_never_agree | 非法 | `contract_replay` | `fields/swap` | compare | 两个 cell 都是分歧，且标 `swap_suspect` | L1·S3 | |
| 07c-04 | test_missingrow_missing_cell_to_confirm | 边缘 | `contract_replay` | `fields/missingrow` | compare | 全部标 `missing_cell`；没有一个 agree | L1·S3 | |
| 07c-05 | test_auto_agree_iff_all_present_equal | 边缘 | `unit` | hyp：四路网格 | compare | agree ⟺ 四路齐、值全等、无行级 uncertain | L1·S4 | |
| 07c-06 | test_vote_engine_permutation | 边缘 | `unit` | hyp：路的排列 | compare | 票型相同 | L1·S6 | |
| 07c-07 | test_recommended_in_candidates | 边缘 | `unit` | hyp | compare | 推荐值在候选值中，且是多数值 | L1·S4 | |
| 07c-08 | test_tie_no_majority_recommendation | 边界 | `unit` | 2:2 | compare | 判 tie，不给推荐值 | L1·S2 | |
| 07c-09 | test_three_vs_one_not_agree | 边界 | `unit` | 3:1 | compare | 判分歧，推荐值为多数值 | L1·S2 | |
| 07c-10 | test_whitelist_not_applied_to_numeric | 非法 | `unit` | 白名单条目匹配到数值 | compare | 仍然判分歧 | L1·S3 | |
| 07c-11 | test_whitelist_applied_to_text | 成功 | `unit` | 单位文本上的引擎弱点 | compare | 判 agree | L1·S1 | |
| 07c-12 | test_route_disabled_never_agree | 边缘 | `unit` | codex 被禁用 | compare | 全部标 `missing_cell:codex` | L1·S3 | |

### WI-07d：裁图

**Law**：变换链往返（S5）；裁图始终在图内（S4）。
**替身 / 配对**：无。**重构敏感点**：断言的是像素框。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 07d-01 | test_transform_roundtrip | 边缘 | `unit` | hyp：旋转 × crop | 正向映射再反向映射 | 误差 ≤ 1px | L1·S5 | |
| 07d-02 | test_crop_within_bounds | 边缘 | `unit` | hyp | crop | 结果在图像范围内 | L1·S4 | |
| 07d-03 | test_crop_rows_plus_minus_one | 成功 | `unit` | 第 5 行 | crop | 覆盖第 4–6 行 | L1·S1 | |
| 07d-04 | test_first_last_row_boundary | 边界 | `unit` | 首行和末行 | crop | 不越界 | L1·S2 | |
| 07d-05 | test_no_bbox_fallback | 非法 | `unit` | 只有云端路的 cell | crop | 退回整行或半页；记 `no_bbox_crop` | L1·S3 | |
| 07d-06 | test_crop_from_masked_derivative | 边缘 | `unit` | 打码后的衍生图 | crop | 打码区域的像素为 0 | L2·S3 | |

### WI-08a：审计阶段、`audit_rev`、uncertain

**Law（K2）**：候选为 verified ⇒ 无 uncertain、全部 agree、四路齐（S4）；uncertain_log 只追加（S5）。
**替身 / 配对**：OCR 回放，配对 06-11/12。**重构敏感点**：断言的是目录、字节和状态。
**并发**：08a-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 08a-01 | test_audit_rev_new_on_run_change | 成功 | `contract_replay` | `fx-redo` | kb-audit | 生成 `a2`；`a1` 的树 sha 不变 | L2·S1 | |
| 08a-02 | test_audit_rev_reuse_when_same | 边缘 | `contract_replay` | run 不变 | 跑两次 | 仍然是 `a1` | L2·S5 | |
| 08a-03 | test_write_once | 非法 | `contract_replay` | 已有 cells | 再写一次 | 抛异常；字节不变 | L2·S3 | |
| 08a-04 | test_uncertain_log_append_only | 边缘 | `contract_replay` | hyp：增加、解除序列 | 逐步执行 | 每一步之后，已有前缀不变 | L2·S5 | |
| 08a-05 | test_page_level_to_candidate_disputed | 非法 | `unit` | `route_missing_page` | gate | 整页的候选都是 disputed | L1·S3 | |
| 08a-06 | test_uncertain_entry_fields_per_reason | 边界 | `unit` | 每一种原因 | 生成条目 | 四个字段齐全；scope 正确 | L1·S2 | |
| 08a-07 | test_candidate_verified_implies_clean | 边缘 | `unit` | hyp | gate | verified ⇒ 无 uncertain、全部 agree、四路齐 | L1·S4 | |
| 08a-08 | test_concurrent_audit_same_batch | 并发 | `contract_replay` | `fx-u1-3labs` | 两个进程同时跑 | 一个 exit 40；只产生一个 `a<N>` | L2·S3 | |

### WI-08b：确认页 HTML + `choices` 导出

**Law**：No law：这是渲染层。
**替身 / 配对**：无。**重构敏感点**：按语义选择器查元素。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 08b-01 | test_item_structure_u8 | 成功 | `unit` | `fx-u8` | 渲染 | 每一条都有图、四个候选、★、「都不对 / 手输」 | L1·S2 | |
| 08b-02 | test_missing_route_not_clickable | 非法 | `unit` | `fx-redo` a2 | 渲染 | 显示「缺路，不可点选」和修复命令；没有 input | L1·S3 | |
| 08b-03 | test_effective_vs_candidate_label | 成功 | `unit` | 同上 | 渲染 | 文本中有「生效 r1 (verified) / 候选 r2 (disputed: …)」 | L1·S1 | |
| 08b-04 | test_advice_display_only | 边缘 | `unit` | 有 advice | 渲染并导出 | 提示可见；导出结果中不含 advice 的值 | L1·S3 | |
| 08b-05 | test_no_external_urls | 非法 | `unit` | 全部 | 渲染 | 没有任何 http(s) 资源 | L1·S3 | |
| 08b-06 | test_export_js_node | 成功 | `cli_integration` needs_node | `fx-u8` | 用 node 导出 | 通过 schema；`confirm_sha256` 正确；`fingerprints` 齐全 | L3·S1 | |
| 08b-07 | test_zero_disputes_page | 边界 | `unit` | 没有分歧的页 | 渲染并导出 | 显示「无分歧」；`items == []` | L1·S2 | |
| 08b-08 | test_ocr_text_escaped | 非法 | `unit` | 文本中含 `<script>` | 渲染 | 被转义 | L1·S3 | |
| 08b-09 | test_tie_shows_tie_label | 边界 | `unit` | `p002-l07` | 渲染 | 标「平票」，没有 ★ | L1·S2 | |

### WI-08c：写回（唯一提交者）

**Law（K2、K3）**：
- effective.rev 单调递增，并且只有候选为 verified 时才推进；**每次推进后，effective 的 `{rev, audit_rev, status, verified_by}` 与被提升的候选逐字段相等，被替代的候选带 `superseded_by`**（S5，v2 按缺口 10 加强）。
- 候选失败时 effective 不动（S5）。
- 写回幂等（S5）；fixes 只追加（S5）；任何崩溃都满足「要么全提交，要么全回滚」（S5）。
- 其他阶段从不改写 status（S6）。
- 一条记录只有在**所有**分歧字段都有合法决定、且没有一个决定指向缺失的 cell 时，才可能变成 verified（v2，缺口 4、16）。

**替身 / 配对**：无。**重构敏感点**：断言的是 `_by_id`、`_rev`、`fixes` 的字节和退出码。
**并发**：08c-14。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 08c-01 | test_human_choice_commits_verified | 成功 | `contract_replay` | `fx-u8` 的合法 choices | writeback | rev+1；`verified_by: human`；同一个 commit_id | L2·S1 | |
| 08c-02 | test_stale_choices_exit30 | 非法 | `contract_replay` | 参数化四种不一致 | writeback | exit 30；树 sha 不变 | L2·S3 | |
| 08c-03 | test_manual_empty_is_none | 非法 | `contract_replay` | manual 值为空串 | writeback | 结果为 disputed；effective 不变 | L2·S3 | |
| 08c-04 | test_none_not_a_field_dropped | 边界 | `contract_replay` | none + not_a_field | writeback 后导出 | 标 dropped；不导出 | L2·S2 | |
| 08c-05 | test_record_atomic_multi_field | 边缘 | `contract_replay` | A 有一个字段不合格，B 合格 | writeback | A 零写入，exit 30；B 正常提交；`per_record` 正确 | L2·S3 | |
| 08c-06 | test_idempotent_resubmit | 边缘 | `contract_replay` | 同一份 choices | 提交两次 | 没有新 rev；没有重复 decision | L2·S5 | |
| 08c-07 | test_fixes_append_only | 边缘 | `contract_replay` | 多次提交 | 依次提交 | 已有前缀的字节不变 | L2·S5 | |
| 08c-08 | test_effective_rev_monotonic | 边缘 | `contract_replay` | hyp：随机提交序列 | 逐个提交 | effective.rev 单调递增，只在候选为 verified 时变化；旧候选不会覆盖新的。**v2 加强**：每次推进后，effective 的 `{rev, audit_rev, status, verified_by}` 与被提升的候选逐字段相等；被替代的候选带 `superseded_by` | L2·S5 | |
| 08c-09 | test_failed_candidate_never_moves_effective | 边缘 | `contract_replay` | hyp：已有 verified r1，再来失败的候选 | writeback | effective 字节不变；`_rev/X.r1.md` 的 sha 不变 | L2·S5 | |
| 08c-10 | test_auto_verified_iff_all_agree_no_uncertain | 边缘 | `contract_replay` | hyp：四路行几何与文本，**生成域包含：只有 approx 的行、缺行、拼行、值相同的相邻行**，依次走 **align** → compare → gate → writeback | 全链 | 候选为 verified(auto) ⟺ 四路齐、全部 agree、`uncertain==[]`（cell 级的 `no_geometry`、`unaligned`、`merged_rows` 都计入 uncertain）。v2 把链的起点从 compare 前移到 align | L2·S4 | |
| 08c-11 | test_choice_on_page_level_uncertain_rejected | 非法 | `contract_replay` | 伪造的 choices | writeback | exit 30；始终不会变成 verified | L2·S3 | |
| 08c-12 | test_rev_files_readonly | 非法 | `contract_replay` | 提交之后 | 以写模式打开 `_rev` 文件 | 抛 `PermissionError` | L2·S3 | |
| 08c-13 | test_crash_injection_consistency | 边缘 | `contract_replay` | 枚举写点 + 随机 SIGKILL | 崩溃后重启 | 要么全提交，要么全回滚；`status==effective.status` | L2·S5 | |
| 08c-14 | test_concurrent_writeback_same_choices | 并发 | `contract_replay` | `fx-u8` | 两个进程同时提交 | 只产生一个新 rev；另一个 exit 40 或什么都不做 | L2·S5 | |
| 08c-15 | test_same_field_different_choice_new_rev | 边界 | `contract_replay` | 同一字段换一个值，rev_base 为当前值 | writeback | 产生 rev N+1 | L2·S2 | |
| 08c-16 | test_writeback_sole_writer_of_status | 边缘 | `contract_replay` | `fx-u8` 写回之后 | 依次跑视图、build、verifier、渲染、check | `_by_id/**` 的字节不变 | L2·S6 | |
| 08c-17 | test_no_geometry_row_never_auto_verified | 非法 | `contract_replay` | 新增 `fx-approx-only`：某一行只有 approx 几何，四路恰好读得完全一致 | align → compare → gate → writeback 全链 | 该记录的候选为 `pending_confirm`；`uncertain` 中有 `no_geometry` 条目；没有创建 `effective` | L2·S3 | |
| 08c-18 | test_partial_choices_record_not_verified | 非法 | `contract_replay` | `fx-u1-3labs`：同一条记录有 3 处分歧，choices 只覆盖了其中 2 处 | writeback | **规格待定（推荐，§7-F14）**：exit 30，并在 stderr 和 `per_record[]` 中列出漏掉的 field_id；该记录零写入（`effective` 字节不变；`fixes.jsonl` 中没有它的 commit；也没有新的候选 rev） | L2·S3 | |
| 08c-19 | test_choice_on_missing_route_cell_rejected | 非法 | `contract_replay` | 伪造的 choices：`choice.route` 指向在该 cell 上值为 None 的路 | writeback | exit 30；该记录零写入 | L2·S3 | |

### WI-08d：兼容视图 + U6/U8 行为

**Law**：视图重建幂等，且与顺序无关（S5、S6）。
**替身 / 配对**：OCR 回放，配对 06-11/12。**重构敏感点**：断言的是链接目标和 sha。
**并发**：08d-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 08d-01 | test_view_symlink_points_effective | 成功 | `contract_replay` | `fx-u8` 写回之后 | 重建 | 链接指向 effective 对应的 rev；verified 链接存在 | L2·S1 | |
| 08d-02 | test_same_day_suffix | 边界 | `contract_replay` | `fx-u1-3labs` | 重建 | 依次为 `-lab`、`-2`、`-3` | L2·S2 | |
| 08d-03 | test_first_ingest_pending_view_r4_01 | 边界 | `contract_replay` | `fx-u3-mixed` | 重建 | records 视图指向最新候选；`verified/` 中没有它 | L2·S2 | |
| 08d-04 | test_view_rebuild_idempotent | 边缘 | `contract_replay` | 任意 | 重建两次，或删除后重建 | 链接集合相同 | L2·S5 | |
| 08d-05 | test_disputed_not_in_verified | 非法 | `contract_replay` | 选了「都不对」的记录 | 重建 | `verified/` 中没有它 | L2·S3 | |
| 08d-06 | test_u6_redo_keeps_verified | 边缘 | `contract_replay` | `fx-redo` | r4.1 的 ①–④ 加续段 | 全部成立；`r1.md` 的 sha 不变 | L2·S5 | |
| 08d-07 | test_view_rebuild_order_independent | 边缘 | `contract_replay` | hyp：写入顺序 | 重建 | 链接相同 | L2·S6 | |
| 08d-08 | test_concurrent_view_rebuild | 并发 | `contract_replay` | `fx-u1-3labs` | 两个进程同时重建 | 结果与单次重建相同；没有悬空链接 | L2·S3 | |

---

## 4. M2 · build / check / 发布 / MCP

### WI-01a：`records_to_txt` + `export_map`

**Law**：行由构造保证唯一（S4）；与 cells 无关（S6）；来源链成立（S5）；**整行**（不只是值）都由 `audit_bindings` 绑定的那个 rev 投影出来（v2，缺口 11）。
**替身 / 配对**：无。**重构敏感点**：断言的是导出文本和 sha。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 01a-01 | test_line_format | 成功 | `unit` | `fx-labs-1x` | 导出 | 行格式完全一致 | L1·S1 | |
| 01a-02 | test_only_verified_exported | 非法 | `unit` | 状态混杂的记录 | 导出 | 只导出 verified | L1·S3 | |
| 01a-03 | test_values_from_binding_rev | 边缘 | `unit` | **v2**：effective 为 r1、候选为 r2，两者的 item、value、date、flag **四列全都不同** | 导出 | 整行等于由 r1 投影出的那一行（四列都取自 r1） | L1·S3 | |
| 01a-04 | test_cells_independence | 边缘 | `contract_replay` | `fx-u8` | 删除或打乱 cells | 输出的 sha 不变 | L2·S6 | |
| 01a-05 | test_lines_unique_by_construction | 边缘 | `unit` | hyp | 导出 | 行两两不同 | L1·S4 | |
| 01a-06 | test_fx_labs_3x_three_distinct | 边界 | `unit` | `fx-labs-3x` | 导出 | 3 行互不相同 | L1·S2 | |
| 01a-07 | test_export_map_provenance | 边缘 | `contract_replay` | `fx-chain` 全链 | 用 00b3 校验 | 0 违例；一一对应 | L1·S5 | |
| 01a-08 | test_empty_record_rejected | 非法 | `unit` | 空记录 | 导出 | 进入 excluded；不产出任何行 | L1·S3 | |
| 01a-09 | test_two_shelves | 成功 | `unit` | `fx-labs-3x` | 导出 | 两个书架的文件都存在 | L1·S2 | |
| 01a-10 | test_u5_after_manual_fix | 边缘 | `cli_integration` | `fx-manualfix` | 真 klode verify | 1.05 返回 VERIFIED，10.5 返回 NOT FOUND | L3·S3 | |
| 01a-11 | test_export_deterministic_rerun | 边缘 | `unit` | 任意 | 导出两次 | 字节相同 | L2·S5 | |

### WI-01b：`records_to_l1` + `items_index`

**Law**：锚点 ↔ 行是双射（S4）。
**替身 / 配对**：无。**重构敏感点**：断言的是卡片内容。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 01b-01 | test_front_matter_kind_deterministic | 成功 | `unit` | `fx-labs-1x` | 生成 | `kind: deterministic` | L1·S1 | |
| 01b-02 | test_coverage_equals_rows | 边缘 | `unit` | hyp | 生成 | 锚点数等于行数；一一对应 | L1·S4 | |
| 01b-03 | test_thin_latest_and_trend | 成功 | `unit` | `fx-labs-3x` | 生成 | 显示最新值，趋势正确 | L1·S1 | |
| 01b-04 | test_single_observation | 边界 | `unit` | `fx-labs-1x` | check | 1 个锚点，PASS | L1·S2 | |
| 01b-05 | test_missing_anchor_fails_check | 非法 | `unit` | 删掉一个锚点 | check | FAIL，覆盖率 2/3 | L1·S3 | |
| 01b-06 | test_records_shelf_cards_generated | 成功 | `unit` | `fx-labs-3x` | 生成 | 每个 records 源都有同名卡 | L1·S1 | |
| 01b-07 | test_fx_labs_3x_real_klode | 成功 | `cli_integration` | `fx-labs-3x` | 真 klode | 三条都 VERIFIED；`zoom full` 显示齐全 | L3·S1 | |

### WI-01c：`deident` 两档

**Law**：假名稳定（S5）；dateshift 保持日期先后和间隔（S5）；只有一个偏移量（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是输出文本和密钥文件的位置。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 01c-01 | test_pseudonym_stable_and_injective | 边缘 | `unit` | hyp：姓名 | 跑两次 | 结果稳定，且是单射 | L1·S5 | |
| 01c-02 | test_pseudonym_dates_untouched | 成功 | `unit` | `fx-labs-3x` | pseudonym | 日期不变 | L1·S1 | |
| 01c-03 | test_dateshift_preserves_order_and_intervals | 边缘 | `unit` | hyp：日期 | dateshift | 先后和间隔都不变 | L1·S5 | |
| 01c-04 | test_dateshift_single_offset_across_views | 边缘 | `unit` | `fx-labs-3x` | 三个视图 | 偏移量相同 | L1·S5 | |
| 01c-05 | test_key_only_in_private | 非法 | `unit` | tmp 项目 | dateshift | 密钥只出现在 `_private` 中 | L2·S3 | |
| 01c-06 | test_pii_zero_hits | 非法 | `unit` | 全部 fx | 脱敏后扫描 | 0 命中 | L1·S3 | |
| 01c-07 | test_salt_change_changes_pseudonym | 边界 | `unit` | 两个不同的盐 | pseudonym | 结果不同 | L1·S2 | |
| 01c-08 | test_dateshift_calendar_boundaries | 边界 | `unit` | 年末、闰日 | dateshift | 结果都是合法日期 | L1·S2 | |

### WI-02a：vendoring 第一批

**Law**：切开再拼回等于原文（S5）；B5 重复 apply 幂等（S5）；变体集合是超集（S6）。
**替身 / 配对**：无。**重构敏感点**：断言的是文件内容。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 02a-01 | test_split_concat_equals_original | 边缘 | `unit` | hyp | 切开 | 拼回后与原文逐字节相同 | L1·S5 | |
| 02a-02 | test_cn_heads | 成功 | `unit` | 三种中文章头 | HEAD_RE | 都能识别 | L1·S2 | |
| 02a-03 | test_head_re_from_config | 边界 | `unit` | 自定义正则 | 切开 | 按自定义正则切 | L1·S2 | |
| 02a-04 | test_b5_reapply_no_dup | 边缘 | `unit` | aurora 拷贝 | apply 两次 | 只出现一次（先确认是红的） | L2·S5 | |
| 02a-05 | test_b1_longest_match | 非法 | `unit` | `"reset -- this"` | fix_anchors | 结果包含 `"reset — this"`（先确认是红的） | L1·S3 | |
| 02a-06 | test_fix_anchors_variants_superset | 边缘 | `unit` | hyp | 新旧两版对比 | 新版是旧版的超集 | L1·S6 | |
| 02a-07 | test_policy_from_source_not_kind | 非法 | `unit` | items 源但卡片上写 authored | check | 按 deterministic 判 | L1·S3 | |
| 02a-08 | test_check_cards_json_output | 成功 | `cli_integration` | aurora 的卡 | `--json` | 通过 schema；与普通输出一致 | L3·S1 | |
| 02a-09 | test_no_hardcoded_paths | 非法 | `unit` | src、prompts | 扫描 | 没有私有路径 | L1·S3 | |

### WI-02b：`write_card` 门禁 + `check_published`

**Law**：写卡幂等（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是卡的字节和 receipt。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 02b-01 | test_b2_fail_no_write | 非法 | `unit` | 不合格的卡 | write_card | exit 1；字节不变（先确认是红的） | L2·S3 | |
| 02b-02 | test_pass_writes_card | 成功 | `unit` | 合格的卡 | write_card | 正文写到标记线以下 | L2·S1 | |
| 02b-03 | test_forced_receipt_fails | 非法 | `unit` | `--force` | write_card | `forced=true`；步骤失败 | L2·S3 | |
| 02b-04 | test_published_folded_detected | 非法 | `cli_integration` | 含 `--` 的锚点 | check_published | FAIL，并点名 | L3·S3 | |
| 02b-05 | test_stale_receipt_detected | 边缘 | `unit` | 改过的卡 | check_published | 判 stale | L2·S3 | |
| 02b-06 | test_write_card_idempotent | 边缘 | `unit` | 合格的卡 | 写两次 | 字节相同 | L2·S5 | |

### WI-02c：书桌

**Law**：`cue_suggest` 与留出集无关（S6）。
**替身 / 配对**：无。**重构敏感点**：断言的是分数和退出码。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 02c-01 | test_b3_json_parse | 非法 | `cli_integration` | 维度 `safety` | 真 klode diagnose | 判对 | L3·S3 | |
| 02c-02 | test_b4_cn_action_head | 非法 | `unit` | 纯中文的加粗动作头 | check_synthesis | 报 S4 | L1·S3 | |
| 02c-03 | test_tune_pass_holdout_fail | 非法 | `unit` | 调优集 5/5，留出集 3/5 | verify | FAIL | L1·S3 | |
| 02c-04 | test_holdout_threshold_boundary | 边界 | `unit` | 4/5、3/5、只有 4 题 | verify | PASS、FAIL、FAIL | L1·S2 | |
| 02c-05 | test_holdout_frozen_sha | 非法 | `unit` | 改了留出集但没有 refreeze；以及正常 refreeze | verify | 前者报错；后者通过并记下 reason | L2·S3 | |
| 02c-06 | test_cue_suggest_ignores_holdout | 边缘 | `unit` | hyp：改留出集 | cue_suggest | 输出不变 | L1·S6 | |
| 02c-07 | test_separate_scores_printed | 成功 | `unit` | aurora | verify | 两行分开显示的成绩 | L1·S1 | |

### WI-02d：`gen_agent` + `smoke_mcp`

**Law**：No law：模板渲染。
**替身 / 配对**：无。**重构敏感点**：断言的是生成的文本。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 02d-01 | test_first_line_frontmatter | 非法 | `unit` | 医疗模板 | gen_agent | 第一行是 `---`；注释在 frontmatter 之后 | L1·S3 | |
| 02d-02 | test_zh_template_no_english_claim | 非法 | `unit` | 医疗模板 | gen_agent | 没有「原文是英文」 | L1·S3 | |
| 02d-03 | test_params_rendered | 成功 | `unit` | 四个参数 | gen_agent | 每个参数都在应在的位置 | L1·S2 | |
| 02d-04 | test_example_from_desk_tests | 成功 | `unit` | desk tests | gen_agent | example 取自 tests 的第一项 | L1·S1 | |
| 02d-05 | test_empty_desk_tests | 边界 | `unit` | tests 为空 | gen_agent | **规格待定（§7-F4）**：期望 exit 20 | L1·S2 | |
| 02d-06 | test_smoke_6_pass | 成功 | `cli_integration` | `fx-labs-3x` | 真 smoke_mcp | 6 项 PASS | L3·S1 | |
| 02d-07 | test_smoke_default_query_first_alias | 边界 | `unit` | aliases | smoke | query 等于 aliases[0] | L1·S1 | |
| 02d-08 | test_prompts_no_private_paths | 非法 | `unit` | prompts | 扫描 | 没有私有路径 | L1·S3 | |

### WI-03a：`init_kb` + `gen_manifest`

**Law**：init 幂等（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是目录树和 toml。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 03a-01 | test_init_kb_toml_fields | 成功 | `cli_integration` | tmp | init 后用 klode 读 | 字段齐全，可以加载 | L3·S1 | |
| 03a-02 | test_init_idempotent | 边缘 | `unit` | tmp | 跑两次 | 树 sha 相同；`.gitignore` 没有重复行 | L2·S5 | |
| 03a-03 | test_init_preserves_existing_gitignore | 边缘 | `unit` | 已有自定义行 | init | 原有行保留，新内容追加在后 | L2·S3 | |
| 03a-04 | test_lt2_heads_single_source | 边界 | `unit` | 0、1、2 个章头 | gen_manifest | 前两种是单一来源，最后一种切开 | L1·S2 | |
| 03a-05 | test_split_selfcheck_fail_aborts | 非法 | `unit` | 拼不回原文的切法 | gen_manifest | 报错，不写任何文件 | L1·S3 | |
| 03a-06 | test_explicit_manifest_overrides | 成功 | `unit` | `--manifest` | gen_manifest | 按显式指定的划分 | L1·S1 | |

### WI-03b：`gen_desk`

**Law**：留出题 ≥ 5 道且互不相同（S4）；只引用实际存在的维度（S4）。
**替身 / 配对**：无。**重构敏感点**：断言的是生成的内容。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 03b-01 | test_dimension_merge_gt8 | 边界 | `unit` | 9 章、20 章 | gen_desk | 维度 ≤ 8 个，只合并相邻的 | L1·S2 | |
| 03b-02 | test_lt2_one_dimension | 边界 | `unit` | 1 章 | gen_desk | 只有一个维度 | L1·S2 | |
| 03b-03 | test_holdout_ge5_distinct | 边缘 | `unit` | hyp | gen_desk | ≥ 5 道且互不相同 | L1·S4 | |
| 03b-04 | test_holdout_insufficient_exit20 | 非法 | `unit` | 维度和项目都极少 | gen_desk | exit 20 | L1·S3 | |
| 03b-05 | test_medical_holdout_only_existing_dimensions | 边缘 | `unit` | hyp：items 子集 | gen_desk | 引用的维度都存在 | L1·S4 | |
| 03b-06 | test_explicit_desk_overrides | 成功 | `unit` | `--desk` | gen_desk | 与显式指定的一致 | L1·S1 | |

### WI-10b：doctor / preflight

**Law**：No law：逐项检查。
**替身 / 配对**：10b-03 用录制的探测结果，配对 00d2-05。**重构敏感点**：断言的是退出码和修复命令。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 10b-01 | test_missing_binary_exit40 | 非法 | `cli_integration` | PATH 中去掉 klode | doctor | exit 40，并给出修复命令 | L3·S3 | |
| 10b-02 | test_tracked_settings_local_exit40 | 非法 | `cli_integration` | settings.local.json 被 git 跟踪 | doctor | exit 40 | L2·S3 | |
| 10b-03 | test_untrusted_project_exit40 | 非法 | `unit` | 录制的「未信任」 | doctor | exit 40，并给出提示 | L1·S3 | |
| 10b-04 | test_all_present_exit0 | 成功 | `cli_integration` | 依赖齐全 | doctor | exit 0；0 个缺项 | L3·S1 | |
| 10b-05 | test_mcp_import_degrade_level | 边界 | `cli_integration` | 正常环境；影子包 | doctor | 降级级别分别为 0 和 1 | L3·S2 | |
| 10b-06 | test_no_download | 边缘 | `cli_integration` | tmp HOME | doctor | 各个缓存目录的树不变 | L2·S3 | |
| 10b-07 | test_gitignore_missing_entries_exit40 | 边界 | `unit` | 缺 `_mask_review/` | doctor | exit 40，并点名 | L1·S2 | |

### WI-09a：pipeline 内核

**Law**：三个 hash 都匹配才复用（S5）；resume 幂等（S5）。
**替身 / 配对**：回放，配对 06-11 和 00d2-01。**重构敏感点**：断言的是 receipt 和退出码。
**并发**：09a-03。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09a-01 | test_stage_order | 成功 | `contract_replay` | `fx-u3-mixed` | 第一段 | 依次经过三个阶段；exit 10 | L2·S1 | |
| 09a-02 | test_done_reuse_requires_three_hashes | 边缘 | `contract_replay` | hyp：改动子集 | 再跑 | 重跑 ⟺ 子集非空 | L2·S5 | |
| 09a-03 | test_lock_exit40 | 并发 | `contract_replay` | `fx-u1-3labs` | 两个进程 | 第二个 exit 40 | L2·S3 | |
| 09a-04 | test_lock_released_after_crash | 边缘 | `contract_replay` | kill -9 持锁进程 | 再跑 | 能拿到锁 | L2·S3 | |
| 09a-05 | test_crash_recovery_dead_pid | 边缘 | `contract_replay` | running 状态但 pid 已死 | 再跑 | 重跑该步，最后为 done | L2·S5 | |
| 09a-06 | test_running_live_pid_not_stolen | 边界 | `contract_replay` | running 且 pid 还活着 | 再跑 | exit 40 | L2·S3 | |
| 09a-07 | test_exit_code_table | 边界 | `unit` | 每种原因 | 映射 | 退出码全集与映射都正确 | L1·S2 | |
| 09a-08 | test_env_errors_skip_loops | 非法 | `contract_replay` | 认证失败 | build | exit 40；不进入回路 | L2·S3 | |
| 09a-09 | test_usage_error_exit2 | 非法 | `unit` | 不存在的 stage | pipeline | exit 2 | L1·S3 | |
| 09a-10 | test_resume_idempotent | 边缘 | `contract_replay` | 已全部完成 | resume 两次 | 不产生新的 receipt | L2·S5 | |

### WI-09b：13 步 build（`builds/<build_id>/`）

**Law（K8）**：任意一步失败后，后续步骤不执行，指针不动，build 目录之外零写入（S5）；`build_id` 变化 ⟺ inputs 变化（S4）；完成后 build 树**整体递归**只读（v2，缺口 12）。
**替身 / 配对**：writer 回放和 klode 录制，配对 09b-09 和 00d2-01。**重构敏感点**：断言的是树快照、receipt 和日志。
**并发**：09b-10。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09b-01 | test_steps_fixed_order_all_pass | 成功 | `contract_replay` | `fx-labs-3x` | build | 13 项顺序与枚举一致，全部 pass；started 时间单调递增 | L2·S1 | |
| 09b-02 | test_no_writes_outside_builds | 边缘 | `contract_replay` | 全量快照 | build | diff 全部落在 `builds/<id>/` 内 | L2·S5 | |
| 09b-03 | test_failure_at_step_k_stops | 边缘 | `contract_replay` | hyp：k ∈ 1..13 | build | k 之后的步没有 receipt；current 不变；外部零写入 | L2·S5 | |
| 09b-04 | test_build_dir_readonly_and_manifest | 成功 | `contract_replay` | 同 09b-01 | build 后（测试先自己 chmod u+w）改一个文件 | MANIFEST 校验先 PASS，改动后 FAIL（只读权限本身由 09b-12 覆盖） | L2·S3 | |
| 09b-05 | test_audit_bindings_effective_only | 边缘 | `contract_replay` | `fx-redo` | build | 绑定到 `{1, a1}`；X 在 stale_candidates 中 | L2·S3 | |
| 09b-06 | test_excluded_not_in_inputs_u8 | 非法 | `contract_replay` | `fx-u8` 中选 none 的记录 | build | 在 excluded 中，且不在任何输入里 | L2·S3 | |
| 09b-07 | test_pipeline_never_passes_force | 非法 | `contract_replay` | 门禁失败 | build | 日志中没有 `--force`；exit 20 | L2·S3 | |
| 09b-08 | test_build_id_changes_iff_inputs_change | 边缘 | `unit` | hyp | build_id | 变化 ⟺ inputs 变化 | L1·S4 | |
| 09b-09 | test_build_u7_real_klode | 成功 | `cli_integration` | `fx-book-aurora` | 真 klode | 13 步全部 PASS；strict 0E | L3·S1 | |
| 09b-10 | test_same_build_concurrent_exit40 | 并发 | `contract_replay` | 相同 inputs | 两个进程 | 一个 exit 40；只生成一个目录 | L2·S3 | |
| 09b-11 | test_zero_verified_records_build | 边界 | `contract_replay` | 全部 disputed | build | **规格待定（§7-F4）**：期望 exit 20 | L2·S2 | |
| 09b-12 | test_build_tree_fully_readonly | 边缘 | `contract_replay` | `fx-labs-3x` | build 完成后遍历 `builds/<id>/`（`.readers/` 除外） | 每个文件和目录都没有任何写权限位；以写模式打开任意文件都抛 `PermissionError`；在任意子目录中新建文件也会失败 | L2·S3 | |

### WI-09c：三条回路

**Law**：轮数有上限，一定会终止（S4）；预算先预留再派发（S4）。
**替身 / 配对**：回放，配对 00d2-01。**重构敏感点**：断言的是回路记录和花费。
**并发**：09c-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09c-01 | test_l1_chain | 成功 | `contract_replay` | 留出集未达标 | build | 依次经过 cue → apply → verify | L2·S1 | |
| 09c-02 | test_l2_chain | 成功 | `contract_replay` | 门禁 1 先 FAIL 后 PASS | build | 重新派发，S5 失效 | L2·S1 | |
| 09c-03 | test_l3_chain | 成功 | `contract_replay` | citation-rot | build | 修复链完成 | L2·S1 | |
| 09c-04 | test_l3_escalates_l2 | 边缘 | `contract_replay` | 修完后仍有残留 | build | 升级到 L2 | L2·S3 | |
| 09c-05 | test_l2_l3_simultaneous_l3_first | 边界 | `contract_replay` | 同时触发 | build | L3 先执行 | L2·S2 | |
| 09c-06 | test_no_progress_terminates | 边缘 | `contract_replay` | hash 连续两轮相同 | build | exit 50；receipt 记下花费和剩余问题 | L2·S3 | |
| 09c-07 | test_rounds_monotone_bounded | 边缘 | `unit` | hyp | 调度 | 轮数 ≤ max；一定终止 | L1·S4 | |
| 09c-08 | test_budget_reservation_before_dispatch | 并发 | `contract_replay` | 预算只够 2 个 | build | 只派发 2 个；花费 ≤ 预算 | L2·S4 | |
| 09c-09 | test_max_rounds_boundary | 边界 | `unit` | 第 6 轮和第 7 轮 | 调度 | 第 6 轮允许，第 7 轮 exit 50 | L1·S2 | |

### WI-10a：`/kb-check` 内核

**Law**：No law：判据表。
**替身 / 配对**：无。**重构敏感点**：断言的是 receipt 和退出码。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 10a-01 | test_stub_card_fails | 非法 | `cli_integration` | stub 卡 | kb-check | FAIL，回到 L2 | L3·S3 | |
| 10a-02 | test_folded_anchor_fails | 非法 | `cli_integration` | FOLDED | kb-check | FAIL，回到 L3 | L3·S3 | |
| 10a-03 | test_forced_receipt_fails_check | 非法 | `contract_replay` | forced | kb-check | exit 20 | L2·S3 | |
| 10a-04 | test_loop_mapping | 边界 | `unit` | 每类失败 | 映射 | 映射正确 | L1·S2 | |
| 10a-05 | test_aliases_boundary | 边界 | `unit` | 9 和 10 | 判据 | 9 FAIL，10 PASS | L1·S2 | |
| 10a-06 | test_authored_anchors_boundary | 边界 | `unit` | 4 和 5 | 判据 | 4 FAIL，5 PASS | L1·S2 | |
| 10a-07 | test_check_reads_build_dir_not_current | 边缘 | `contract_replay` | current 指向 A，B 有缺陷 | check B | 报出 B 的缺陷 | L2·S3 | |
| 10a-08 | test_plugin_field_in_receipt | 成功 | `contract_replay` | 任意 | check | plugin 字段齐全 | L2·S1 | |
| 10a-09 | test_all_pass_receipt | 成功 | `cli_integration` | `fx-labs-3x` | check | exit 0 | L3·S1 | |
| 10a-10 | test_u5_ready_false_on_pending | 边缘 | `unit` | pending | 汇总 | `u5_ready:false`，记为警告 | L1·S3 | |

### WI-09d：发布协议

**Law（K5）**：
- 任意崩溃点之后，`current` 都完整指向某一个 build，**并且这个 build 已经过校验**：MANIFEST.sha256 通过、`check_receipt.status == PASS`、`check_receipt.build_id == basename(target)`（S5，v2 按缺口 5 加强）。
- 并发 readlink 永远不会悬空（S4）。
- prune 永不移动受保护的 build（S4）。
- 只移动不删除，总数守恒（S5）。
- 判断「较新」按 `PUBLISH.log.seq`，不按 build_id 排序（v2，缺口 14）。

**替身 / 配对**：无。**重构敏感点**：断言的是指针、日志和目录树。
**并发**：09d-03、09d-11、09d-13。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09d-01 | test_publish_switches_pointer | 成功 | `contract_replay` | 新增 `fx-publish-ab` | 发布 B | 指针指向 B；日志带 seq 和 target | L2·S1 | |
| 09d-02 | test_pointer_always_complete | 边缘 | `contract_replay` | `fx-publish-ab`；**崩溃点覆盖 `pipeline --resume` 的 build → check → publish 全段**（包括 chmod 之前和 check 之前） | 枚举崩溃点 + 随机 SIGKILL，然后 resume | **v2 加强**：`current` 存在且能解析；它指向的 build 的 MANIFEST.sha256 校验通过、`check_receipt.status == PASS`、`check_receipt.build_id == basename(target)`。从来不会指向没写完或没 check 的 build | L2·S5 | |
| 09d-03 | test_switch_no_window | 并发 | `contract_replay` | 同上 | 切换 200 次，同时持续 readlink | 从来不出现 ENOENT 或悬空 | L2·S4 | |
| 09d-04 | test_stale_replay_exit22 | 非法 | `contract_replay` | current=C 时 replay B | replay | exit 22；登记不变 | L2·S3 | |
| 09d-05 | test_replay_skips_newer_marker | 边缘 | `contract_replay` | 标记指向更新的 build | replay | 记 skipped_newer | L2·S3 | |
| 09d-06 | test_replay_only_failed_actions | 边缘 | `contract_replay` | 部分动作已 done | replay | 只重放失败的那些 | L2·S3 | |
| 09d-07 | test_post_publish_failure_exit21_pointer_kept | 非法 | `contract_replay` | registry 不可写 | 发布 | exit 21；指针已经切到新 build | L2·S3 | |
| 09d-08 | test_prune_never_moves_protected | 边缘 | `contract_replay` | hyp：日志 + 租约 + N | prune | 受保护的不进 _trash，其余的进 | L2·S4 | |
| 09d-09 | test_prune_conserves_builds | 边缘 | `contract_replay` | hyp | prune | 总数守恒 | L2·S5 | |
| 09d-10 | test_prune_cleans_dead_leases | 边缘 | `contract_replay` | 租约对应的进程已死 | prune | 清理租约；该 build 可以被移走 | L2·S3 | |
| 09d-11 | test_lease_vs_prune_race | 并发 | `contract_replay` | 真双进程 | 并发 | 有存活读者的 build 从不被移走 | L2·S5 | |
| 09d-12 | test_rollback | 成功 | `contract_replay` | 当前是 B，prev 是 A | rollback | 指针回到 A；登记重放 | L2·S1 | |
| 09d-13 | test_replay_rollback_share_lock | 并发 | `contract_replay` | 并发执行 | 并发 | 一个 exit 40；最终状态与串行执行一致 | L2·S3 | |
| 09d-14 | test_keep_boundary | 边界 | `contract_replay` | N=0 和 N=1 | prune | 受保护的仍然保留 | L2·S2 | |
| 09d-15 | test_pending_judged_by_latest_status | 边界 | `unit` | 先 failed 后 done | 计算 | 不算待恢复 | L1·S2 | |
| 09d-16 | test_publish_requires_check_pass | 非法 | `contract_replay` | check 为 FAIL | 发布 | 拒绝；指针不变 | L2·S3 | |
| 09d-17 | test_publish_rejects_unverified_target | 非法 | `contract_replay` | 参数化 4 种：① 没有 `check_receipt.json`；② `check_receipt.build_id` ≠ 目标；③ `MANIFEST.sha256` 校验失败；④ build 目录仍然可写 | 发布 | 4 种都拒绝；`readlink(current)` 不变；`PUBLISH.log` 不新增 switch 行 | L2·S3 | |
| 09d-18 | test_skipped_newer_uses_publish_seq | 边缘 | `contract_replay` | 先发布 A，再发布 B（B 的 build_id 字典序更大），然后 rollback 回 A；此时登记产物上的标记是 B（seq 较小） | replay A | 该动作被重放，没有记 `skipped_newer`（判断「较新」按 seq，不按 build_id） | L2·S3 | |

### WI-11a：MCP 包装

**Law（K7）**：在同一个进程里，**无论哪一类请求先到**，切换之后的每一次查询都读当时 `current` 指向的 build。`kb_version.build_id` 等于上一条查询实际使用的 build（用内容判断），同时也等于 readlink 的结果（S4/S5，v2 按缺口 1 改写）。
**替身 / 配对**：无，用真 klode 0.5.0。**重构敏感点**：断言的是 JSON-RPC 响应的内容。
**fixture 要求**：`fx-publish-ab` 的 A 和 B 各有一条只属于自己的标记行（v2）。
**并发**：11a-09。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 11a-01 | test_same_process_A_to_B | 边缘 | `cli_integration` | `fx-publish-ab` | 同一个进程：A 上 kb_version 加查询 → 切到 B → 再来一遍 | build_id 和内容依次对应 A、B；pid 不变 | L3·S5 | |
| 11a-02 | test_kb_version_equals_readlink | 边缘 | `cli_integration` | hyp：切换序列，以及请求类型的交错（查询在前、kb_version 在前、只有查询） | 每次切换后按生成的顺序发请求 | **v2 law**：每条查询返回的标记行属于当时的 readlink 目标；随后的 `kb_version.build_id` 既等于 readlink，也等于上一条查询实际使用的 build | L3·S4 | |
| 11a-03 | test_missing_current_iserror | 非法 | `cli_integration` | 没有 current | 调用 → 发布 → 再调用 | 先返回 isError，进程存活；发布后正常 | L3·S3 | |
| 11a-04 | test_tools_list_includes_kb_version | 成功 | `cli_integration` | 任意 | tools/list | 等于 TOOLS 加上 kb_version | L3·S1 | |
| 11a-05 | test_handle_none_one_response_per_request | 边界 | `cli_integration` | initialize、tools/list、ping | 读 stdout | 每个请求恰好一行响应 | L3·S2 | |
| 11a-06 | test_degrade_subprocess_proxy | 边缘 | `cli_integration` | 影子包 | A→B | 成立 | L3·S3 | |
| 11a-07 | test_degrade_cli_mode | 边缘 | `cli_integration` | 两级都失败 | A→B | 成立；输出一致 | L3·S3 | |
| 11a-08 | test_lease_cleanup_on_switch_and_exit | 边缘 | `cli_integration` | `fx-publish-ab` | 切换后 EOF | A 的租约消失；EOF 后全部消失。**v2 加强**：租约文件的 mtime 早于 pool 加载的时刻（先写租约再加载）；写完租约后，目标 build 不在 `_trash` 中 | L2·S3 | |
| 11a-09 | test_switch_mid_call_uses_old | 并发 | `cli_integration` | 慢查询 | 查询过程中切换 | 这一次用 A，下一次用 B | L3·S3 | |
| 11a-10 | test_malformed_jsonrpc | 非法 | `cli_integration` | 一行垃圾 | 发送 | 返回 error；进程存活 | L3·S3 | |
| 11a-11 | test_query_first_after_switch_serves_new_build | 边缘 | `cli_integration` | `fx-publish-ab`；参数化三条路径：正常、降级①、降级② | 先在 A 上查询，然后发布 B，**切换后的第一条请求就是 search**（中间不调 kb_version，也不调 tools/list） | 结果只含 B 独有的标记行，不含 A 独有的；之后再调 kb_version，`build_id == B` | L3·S5 | |
| 11a-12 | test_notification_no_response | 边界 | `cli_integration` | 已 initialize | 发送 `notifications/initialized`（没有 id） | stdout 没有新增任何行；下一个请求仍然正常返回 | L3·S2 | |

### WI-11b：登记器 + `u5_ready` 双探针【宿主探针 G2；真实项目 G3】

**Law**：合并只动自己的键（S5）；upsert 幂等（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是合并后的内容。
**G3 守卫**：全部在 `HOME=tmp`、`KLODE_REGISTRY=tmp` 下运行。
**并发**：11b-11。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 11b-01 | test_mcp_json_merge_preserves_others | 边缘 | `unit` | hyp：已有的键 | register 两次 | 其他键不变；自己的键正确；结果幂等 | L2·S5 | |
| 11b-02 | test_mcp_json_absent_created | 边界 | `unit` | 没有 `.mcp.json` | register | 新建，只有自己的键 | L2·S2 | |
| 11b-03 | test_mcp_json_malformed_refuse | 非法 | `unit` | 畸形 JSON | register | 非 0 退出；原文件不变 | L2·S3 | |
| 11b-04 | test_settings_local_append | 成功 | `unit` | 已有列表 | register 两次 | 列表中有且只有一项，不重复 | L2·S1 | |
| 11b-05 | test_registry_upsert_idempotent | 边缘 | `unit` | tmp registry | upsert 两次 | 只有一条 | L2·S5 | |
| 11b-06 | test_expert_written_atomic | 成功 | `unit` | tmp 项目 | 发布 agent | 文件存在，首行带标记 | L2·S1 | |
| 11b-07 | test_direct_probe_build_id | 成功 | `cli_integration` | tmp 项目 | 用同一份 argv 和 cwd 直连 | build_id 等于目标 | L3·S1 | |
| 11b-08 | test_probe_mismatch_exit21 | 非法 | `cli_integration` | 指针指向旧 build | 探针 | `u5_ready:false`；exit 21 | L2·S3 | |
| 11b-09 | test_host_probe_tool_result | 成功 | `model_integration` | `_lab` | stream-json | 按 tool_use id 取到的 build_id 等于目标 | L3·S1 | G2 G5 |
| 11b-10 | test_never_touches_real_paths | 边缘 | `unit` | `HOME=tmp` | 全流程 | 所有写入都在 tmp 下 | L2·S3 | |
| 11b-11 | test_concurrent_registry_upsert | 并发 | `unit` | tmp | 两个进程分别 upsert | 两条都在；TOML 合法 | L2·S5 | |

---

## 5. M3 · med-intake、workflow、打包、文档

### WI-12：agents【G2】

**Law**：verifier 不改 status（S3；跨阶段的性质由 08c-16 覆盖）。
**替身 / 配对**：回放，配对 12-04。**重构敏感点**：断言的是 frontmatter 和记录字节。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 12-01 | test_frontmatter_static | 成功 | `unit` | `agents/*.md` | 解析 | 合法；不含三项禁用键 | L1·S2 | |
| 12-02 | test_frontmatter_forbidden_keys_rejected | 非法 | `unit` | 注入 hooks | 静态检查 | FAIL | L1·S3 | |
| 12-03 | test_verifier_never_changes_status | 边缘 | `contract_replay` | `fx-u8` | 运行 verifier | `_by_id` 不变；advice 通过 schema | L2·S3 | |
| 12-04 | test_real_writer_card_check_pass | 成功 | `model_integration` | aurora | 真实写卡 | PASS | L3·S1 | G2 |
| 12-05 | test_host_discovers_expert | 成功 | `host_acceptance` | `_lab` | 列出 agents | 有 expert | L3·S1 | G2 G5 |
| 12-06 | test_verifier_tools_readonly | 非法 | `unit` | frontmatter | 解析 | 没有 Edit、Write、Bash | L1·S3 | |

### WI-13b：`/med-intake` 编排

**Law**：别名稳定（S5）。
**替身 / 配对**：回放，配对 06-11/12。**重构敏感点**：断言的是 stdout、退出码和链接。
**并发**：13b-07。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 13b-01 | test_u1_stdout_and_receipt | 成功 | `contract_replay` | `fx-u1-3labs` | med-intake | 两行原文；exit 10；receipt 齐全 | L2·S1 | |
| 13b-02 | test_alias_symlink_stable | 边缘 | `contract_replay` | 同上 | 重跑 | 链接目标不变 | L2·S5 | |
| 13b-03 | test_type_by_header_keyword | 边界 | `unit` | 三种抬头 | 判断类型 | 依次为 lab、rx、exit 2 | L1·S2 | |
| 13b-04 | test_explicit_type_overrides | 成功 | `unit` | `--type rx` | 判断类型 | rx | L1·S1 | |
| 13b-05 | test_alias_collision_same_date | 边缘 | `contract_replay` | 同一天第二批 | med-intake | **规格待定（§7-F4）**：不能静默覆盖 | L2·S3 | |
| 13b-06 | test_empty_inbox_exit2 | 非法 | `contract_replay` | 空的 inbox | med-intake | exit 2 | L1·S3 | |
| 13b-07 | test_concurrent_med_intake_exit40 | 并发 | `contract_replay` | 两个 inbox | 并发 | 一个 exit 40，另一个完整 | L2·S3 | |

### WI-13a：五个 SKILL.md【G2】

**Law**：No law。
**替身 / 配对**：无。**重构敏感点**：断言的是文本。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 13a-01 | test_skills_static | 成功 | `unit` | SKILL.md | 解析 | 入口和白名单正确 | L1·S2 | |
| 13a-02 | test_skills_no_business_logic | 非法 | `unit` | 同上 | 扫描 | 没有内联脚本 | L1·S3 | |
| 13a-03 | test_known_pitfalls_and_iron_rules | 边界 | `unit` | 同上 | 计数 | 12 条加 2 条 | L1·S2 | |
| 13a-04 | test_kb_check_entry | 成功 | `host_acceptance` | `fx-labs-3x` | `--plugin-dir .` | root_sha 等于 release_sha | L3·S1 | G2 G5 |

### WI-14：Workflow 薄壳【G2】

**Law**：No law。
**替身 / 配对**：`agent()` 桩，配对 14-04。**重构敏感点**：断言的是阶段序列。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 14-01 | test_workflow_static | 非法 | `unit` | kb-pipeline.js | 扫描 | 没有被禁用的 API | L1·S3 | |
| 14-02 | test_one_agent_per_stage_with_schema | 成功 | `unit` | 同上 | 解析 | 每个阶段一次 `agent` 调用并带 schema | L1·S2 | |
| 14-03 | test_stage1_ends_at_audit | 边界 | `cli_integration` needs_node | 桩 | node | 在 audit 处结束 | L5·S2 | |
| 14-04 | test_workflow_two_stage | 成功 | `host_acceptance` | `fx-u3-mixed` | 宿主实跑 | 两段都通过 | L3·S1 | G2 G5 |
| 14-05 | test_resume_sequence | 边界 | `cli_integration` needs_node | resume | node | 依次为 writeback、build、check、publish | L5·S2 | |

### WI-15a：打包与安装【G4】

**Law**：重装幂等（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是启动器的输出。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 15a-01 | test_install_outside_repo | 成功 | `cli_integration` | tmp HOME 和 tmp cwd | install.sh，再 --version | 输出版本号 | L3·S1 | G4 |
| 15a-02 | test_reinstall_idempotent | 边缘 | `cli_integration` | 已安装 | 再装一次 | lock 不变；仍然能用 | L2·S5 | G4 |
| 15a-03 | test_klode_import_smoke | 成功 | `cli_integration` | 已安装 | import | 0.5.0 | L3·S1 | |
| 15a-04 | test_launcher_python_override | 边界 | `cli_integration` | `KLODE_KIT_PYTHON` | 启动器 | 用的是指定的解释器 | L3·S2 | |
| 15a-05 | test_missing_venv_message | 非法 | `cli_integration` | 没有 `.venv` | 启动器 | exit 40，并给出提示 | L3·S3 | |

### WI-15b：`release_manifest` + `release_check`

**Law**：sha 变化 ⟺ 清单内的文件发生变化（S4/S6）。
**替身 / 配对**：时钟注入。**重构敏感点**：断言的是 sha 和退出码。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 15b-01 | test_release_sha_changes_iff_file_changes | 边缘 | `unit` | hyp：文件树 | manifest | sha 变化 ⟺ 改动在清单内 | L1·S4 | |
| 15b-02 | test_template_only_change_invalidates | 非法 | `unit` | 只改一个模板 | check | 旧 receipt 失效 | L2·S3 | |
| 15b-03 | test_missing_lane_receipt_fails | 非法 | `unit` | 缺一条 lane | check | FAIL，并点名 | L1·S3 | |
| 15b-04 | test_dirty_tree_fails | 非法 | `unit` | 工作区 dirty | check | FAIL | L2·S3 | |
| 15b-05 | test_receipt_age_boundary | 边界 | `unit` | 正好 7 天；7 天加 1 秒 | check | PASS / FAIL | L1·S2 | |
| 15b-06 | test_manifest_covers_all_runtime_globs | 边界 | `unit` | 每个 glob | manifest | 每个 glob 都有命中；没有命中就 FAIL | L1·S2 | |
| 15b-07 | test_all_green_passes | 成功 | `unit` | 5 份 PASS | check | exit 0 | L1·S1 | |
| 15b-08 | test_rename_same_content_changes_sha | 边缘 | `unit` | 改名但内容不变 | manifest | sha 变化 | L1·S6 | |

### WI-30：文档

**Law**：No law。
**替身 / 配对**：无。**重构敏感点**：断言的是文档与文件系统的对应关系。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 30-01 | test_doc_paths_exist | 成功 | `unit` | docs | 抽取路径 | 都存在 | L2·S2 | |
| 30-02 | test_docs_no_private_ids | 非法 | `unit` | 同上 | 扫描 | 没有私有编号 | L1·S3 | |
| 30-03 | test_readme_original_untouched | 边缘 | `unit` | git HEAD | 比对 | README.md 未改；`_v2` 存在 | L2·S3 | |
| 30-04 | test_directory_doc_covers_top_dirs | 边界 | `unit` | 目录结构.md | 比对 | 每个顶层目录都有说明 | L1·S2 | |

### WI-31：U1–U8 验收套件【含 G2】

**Law**：来源链在真实产物上同样成立（S5）。
**替身 / 配对**：replay 版的配对是同一张表里的 cli、model、host 版本。**重构敏感点**：只走用户入口。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| U1-01 | test_u1_med_intake | 成功 | `contract_replay` | `fx-u1-3labs` | med-intake | stdout 两行原文；exit 10；awaiting_confirm；receipt 四路各有耗时，失败页 0 | L2·S1 | |
| U1-02 | test_u1_rerun_idempotent | 边缘 | `contract_replay` | 同上 | 重跑 | 不产生新调用；stdout 相同 | L2·S5 | |
| U1-03 | test_u1_dispute_count_tracks_fixture | 边界 | `contract_replay` | 两个 fixture | 分别跑 | 分歧数等于各自真值 | L2·S2 | |
| U2-01 | test_u2_kb_ocr_rotated | 成功 | `contract_replay` | `fx-rotated90` | kb-ocr 四路 | rotate 90；抬头全黑；对照图在 `_mask_review/`；bbox_px 是 4 个整数 | L2·S1 | |
| U2-02 | test_u2_kb_ocr_rotated_macos | 成功 | `cli_integration` macos | 同上 | 真 Vision | 同上 | L3·S1 | G4 |
| U2-03 | test_u2_unmasked_never_sent | 非法 | `contract_replay` | 跳过打码 | kb-ocr | exit 60；0 次调用 | L2·S3 | |
| U3-01 | test_u3_pipeline_first_stage | 成功 | `contract_replay` | `fx-u3-mixed` | 第一段 | 状态为 pending_confirm 且 `uncertain: []`；没有分歧的页为 verified | L2·S1 | |
| U3-02 | test_u3_auto_verified_only_when_all_agree | 边缘 | `contract_replay` | 翻转一个值 | 第一段 | verified 变为 pending_confirm | L2·S6 | |
| U3-03 | test_u3_workflows_host | 成功 | `host_acceptance` | `fx-u3-mixed` | `/klode-kit:kb-pipeline` | `/workflows` 中可见 | L3·S1 | G2 G5 |
| U4-01 | test_u4_guide_pdf | 成功 | `cli_integration` | `fx-text-pdf` | kb-ocr + kb-build | text_layer 生效；13 步全部 PASS；`OK: 0 errors, 0 warning(s)` | L3·S1 | |
| U4-02 | test_u4_mixed_pdf_not_skipped | 边界 | `cli_integration` | `fx-mixed-pdf` | kb-ocr | 只跳过 text 页 | L3·S2 | |
| U5-01 | test_u5_verify_cli | 成功 | `cli_integration` | 医疗库 | verify + 包装 | VERIFIED，且两者一致 | L3·S1 | |
| U5-02 | test_u5_expert_qa | 成功 | `model_integration` | `_lab` | 提问 | 每条回答带锚点，且逐条 VERIFIED | L3·S1 | G2 G5 |
| U5-03 | test_u5_expert_not_found | 非法 | `model_integration` | 同上 | 问库里没有的药 | 回答「查不到」；不给任何数字 | L3·S3 | G2 G5 |
| U6-01 | test_u6_redo_keeps_verified_acceptance | 边缘 | `contract_replay` | `fx-redo` | redo + audit | 生成新 fp 目录；旧目录不动；fixes 只追加；verified 不变 | L2·S5 | |
| U7-01 | test_u7_book_build | 成功 | `cli_integration` | `fx-book-aurora` | kb-build | 拼回等于原文；3/3、0E、5/5；3 张卡；`.mcp.json` 有新条目；smoke 6 项 | L3·S1 | |
| U7-02 | test_u7_mcp_json_other_entries_preserved | 边缘 | `cli_integration` | 已有其他条目 | 同上 | 其他条目等价 | L2·S5 | |
| U8-01 | test_u8_confirm_writeback | 成功 | `contract_replay` | `fx-u8` | 渲染 → 点选 → 写回 | 小图、四路、★都在；verified | L2·S1 | |
| U8-02 | test_u8_export_via_node | 成功 | `cli_integration` needs_node | 同上 | node 导出 → 写回 | 同上 | L3·S1 | |
| U8-03 | test_u8_none_disputed_excluded | 非法 | `contract_replay` | 同上 | 写回 + build | disputed；`p002-l07`；不进入 build 输入 | L2·S3 | |
| U8-04 | test_u8_resubmit_same_choices | 边缘 | `contract_replay` | 同上 | 再提交一次 | 不产生新 rev | L2·S5 | |
| P-01 | test_provenance_roundtrip_on_pipeline_output | 边缘 | `contract_replay` | `fx-u8` 全链 | 00b3 校验（v2 的完备校验器） | 0 违例 | L1·S5 | |
| P-02 | test_provenance_roundtrip_on_cli_output | 边缘 | `cli_integration` | U7 和 U5 的产物 | 00b3 校验 | 0 违例 | L1·S5 | |
| UC-01 | test_u1_two_inboxes_concurrent | 并发 | `contract_replay` | 两个 inbox | 并发 | 一个 exit 40；另一个干净完成 | L2·S3 | |

### WI-19（可选）：回修用户 skill【G1】

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 19-01 | manual_b1_b5_repro_flip | 成功 | `manual` | 03 §5 的复现命令 | 用户批准后执行 | 结果翻转为预期 | L2·S1 | G1 |
| 19-02 | manual_backup_before_write | 非法 | `manual` | — | 写入之前检查 | 备份、批准都已记录；缺任何一项就不写 | L2·S3 | G1 |

---

## 6. U1–U8 用户感知层验收表（对应计划 §3）

| U | 计划 §3 中的对应行 | 用户能直接看到的东西 | 覆盖用例（主用例；补强用例） | lane | 闸门 |
|---|---|---|---|---|---|
| U1 | `test_u1_med_intake.py` | stdout 两行原文、exit 10、awaiting_confirm、receipt | U1-01；U1-02、U1-03、UC-01、13b-01/02、08c-18 | contract_replay | — |
| U2 | `test_u2_kb_ocr_rotated.py` | rotate 90、抬头全黑、`_mask_review/`、bbox_px | U2-01、U2-02；U2-03、04b-01、05a-01/07、05b-08、05b-15 | contract_replay、cli_integration macos | G4（U2-02） |
| U3 | `test_u3_pipeline_first_stage.py` + `test_u3_workflows_host.py` | pending_confirm、`uncertain: []`、verified、`/workflows` | U3-01、U3-03；U3-02、08c-17、08d-03、00d2-03/04 | contract_replay、host_acceptance | G2 G5（U3-03） |
| U4 | `test_u4_guide_pdf.py` | text_layer、13 步、`OK: 0 errors` | U4-01；U4-02、04c-04、09b-01 | cli_integration | — |
| U5 | `test_u5_verify_cli.py` + `test_u5_expert_qa.py` | VERIFIED、锚点、「查不到」 | U5-01、U5-02、U5-03；01a-10、11a-01、11a-11、11b-07/09 | cli_integration、model_integration | G2 G5（U5-02/03） |
| U6 | `test_u6_redo_keeps_verified.py` | 新 fp、旧目录不动、fixes 只追加、verified 保持 | U6-01；08d-06、04b-06、08c-09、09b-05 | contract_replay | — |
| U7 | `test_u7_book_build.py` | 拼回、3/3、0E、5/5、`.mcp.json`、smoke | U7-01；U7-02、02a-01、09b-09、02d-06 | cli_integration | — |
| U8 | `test_u8_confirm_writeback.py` | 小图、四路、★、verified、disputed、`p002-l07` | U8-01、U8-02、U8-03；U8-04、08b-01/09、08c-11/18/19、09b-06 | contract_replay、cli_integration needs_node | — |

U1–U8 每一条都至少有一个从用户入口发起的用例。

---

## 7. 缺口与规格问题

| # | 问题 | 处理 |
|---|---|---|
| F1 | r4.3 用 `startswith` 判断路径前缀，存在误判，也能被符号链接绕过 | 00c1-06/07 |
| F2 | 版本号按字典序比较 | 00c1-11（claude）、00c1-20（codex） |
| F3 | 重复图片时 `batch_id` 怎么算，没有定义 | 00b2-02，规格待定 |
| F4 | 空 inbox、非图片文件、零 verified 的 build、同一天第二批、空的 desk tests 都没有规定行为 | 04b-10/11、09b-11、13b-05、02d-05，规格待定 |
| F5 | auto-agree 原先只在 compare 一层验证 | 08a-07、08c-10（v2 起点前移到 align）、U3-02 |
| F6 | 伪造 choices 可以绕过页级 uncertain | 08c-11 |
| F7 | 需要保证 writeback 是 status 的唯一写入者 | 08c-16、12-03 |
| F8 | 发送存在 TOCTOU 窗口 | 05b-08（v2 改为确定性） |
| F9 | 崩溃点和暂停点这类测试缝与 policy 第 13 条有张力 | §0.3；在 WI-00s 记一次决定 |
| F10 | 生成确定性测试放在 unit lane，在 Linux 上只能 skip | 移到 cli_integration 的 macos |
| F11 | 预算「超过」的边界是 > 还是 ≥ | 06-07 |
| F12 | R-2 要求的对照图目录 | U2-01 |
| F13 | WI 数量是 53 还是 52 | WI-17 不计入（Deferred） |
| F14（v2） | **choices 只覆盖记录中的部分分歧字段时该怎么处理**（缺口 4） | **规格待定（推荐）**：exit 30，并列出漏掉的 field_id，该记录零写入（不写 fixes，不产生候选 rev，effective 不变）。由 08c-18 断言。需要主会话确认后写入 02 的 r2.3 写回校验 |
| F15（v2） | **env 白名单**：r4.3 写的是 `ANTHROPIC_*`，这会放行 `ANTHROPIC_BASE_URL`，导致打码后的图能被转发到任意端点 | 设计层：收窄为显式键 `PATH HOME TMPDIR LANG ANTHROPIC_API_KEY`（如需其他认证键，逐个列出）；00c1-08 把 `ANTHROPIC_BASE_URL` 列为非法样例。需要在 02 中补一条修订 |
| F16（v2） | 05b-08 改名并替换了断言方式（policy 第 12 条要求写明理由） | 理由：v1 的写法依赖线程碰巧落在「检查」和「复制」之间的几微秒窗口里，对「复制后不再校验」的错误实现几乎总是绿的，属于会放行的 flaky 测试。v2 用 `KLODE_KIT_PAUSE_AT` 把窗口固定住。此时还没有任何实现，所以这次替换不影响已记录的 red receipt |

需要新增的 fixture：
- `fx-corrupt-pdf`（04c-06）。
- `tests/fixtures/replay/probe-synth/*.jsonl`（00c2）。
- `fx-publish-ab`：在 tmp 中构建；v2 要求 A 和 B 各有一条独有的标记行（11a-02/11）。
- `fx-approx-only`（v2，08c-17）。
- `fx-chain`：要显式包含 5 个 `legal-*` 目录，以及 v2 新增的 `broken-7…12`（00b3-14…19）。

以上入库的 fixture 都要登记进 MANIFEST。

---

## 8. 统计

**总数：509**（v1 为 490，v2 新增 19 条；05b-08 是原地替换，不计入新增）

| lane | 数量 |
|---|---|
| `unit` | 258 |
| `contract_replay` | 168 |
| `cli_integration` | 59 |
| `model_integration` | 13 |
| `host_acceptance` | 9 |
| `manual` | 2 |

| 五类 | 数量 |
|---|---|
| 成功 | 119 |
| 边界 | 91 |
| 非法 | 130 |
| 边缘 | 146 |
| 并发 | 23 |

| 里程碑 | 用例 | unit | contract_replay | cli | model | host | manual |
|---|---|---|---|---|---|---|---|
| M0 | 138 | 88 | 33 | 5 | 7 | 5 | 0 |
| M1 | 149 | 69 | 69 | 9 | 2 | 0 | 0 |
| M2 | 157 | 79 | 47 | 30 | 1 | 0 | 0 |
| M3 | 65 | 22 | 19 | 15 | 3 | 4 | 2 |

需要手动闸门的用例（v2 没有变化）：

| 闸门 | 用例 |
|---|---|
| G1 | 19-01、19-02 |
| G2 | 00c2-06/07/08/09、00c3-09、00d2-01/02/03/04/07、06-11/12、11b-09、12-04/05、13a-04、14-04、U3-03、U5-02/03，共 20 条 |
| G3 | 没有自动用例会触及；11b-10 断言不碰真实路径 |
| G4 | 04a-10/11/12、05a-07、15a-01/02、U2-02 |
| G5 | 00d2-03/04/05/06/07、11b-09、12-05、13a-04、14-04、U3-03、U5-02/03 |

---

## 9. v2 变更对照（spec-attack-20261022-r1）

| 缺口 | 优先级 | 关闭它的用例 |
|---|---|---|
| 1 只有 kb_version 刷新 pool（K7） | P0 | 新增 11a-11（三条路径参数化）；改写 11a-02 的 law；K7 行已更新 |
| 2 `no_geometry` 没有传到 gate（K1/K2） | P0 | 新增 08c-17；08c-10 起点前移到 align，并扩大生成域 |
| 3 对齐属性在「什么都不对齐」时也成立（K1） | P0 | 新增 07b-12；加强 07b-07 |
| 4 只覆盖部分字段的 choices（K2） | P0 | 新增 08c-18（规格待定，推荐 exit 30 并列出 field_id、零写入；§7-F14） |
| 5 发布未经校验的 build（K5） | P0 | 新增 09d-17；加强 09d-02（目标校验 + 崩溃点覆盖 build→check→publish 全段） |
| 6 TOCTOU 测试不确定 | P0 | 05b-08 替换为确定性的 `test_toctou_swap_after_check_deterministic`；§0.3 增加 `PAUSE_AT`；§7-F16 |
| 7 checker 不看 flag 的值、重复项和 `--settings` 文件 | P0 | 新增 00c1-18、00c1-19 |
| 8 实际执行的 argv 不是审查过的那份 | P0 | 新增 05b-15 |
| 9 校验器只对 6 类损坏完备（K4） | P1 | 新增 00b3-14…19；00b3-12 的损坏域扩大到 r3.1 全部不变量 × 任意一路 |
| 10 effective 只推进了一半（K3） | P1 | 加强 08c-08（逐字段相等 + `superseded_by`） |
| 11 值以外的列取自最新候选 | P1 | 改写 01a-03（四列都不同，断言整行投影） |
| 12 只读权限没有递归 | P1 | 新增 09b-12；09b-04 的断言改为只看 MANIFEST |
| 13 codex 版本下限 | P2 | 新增 00c1-20 |
| 14 `skipped_newer` 按字典序判断 | P2 | 新增 09d-18 |
| 15 对 notification 也回响应 | P2 | 新增 11a-12 |
| 16 choices 选了缺失 cell 上的路 | P2 | 新增 08c-19 |
| 设计提示：env 白名单 | — | 改写 00c1-08（显式白名单键；`ANTHROPIC_BASE_URL` 列为非法样例）；§7-F15 |
| 附带风险：租约写入的窗口 | — | 加强 11a-08（先写租约再加载；写完后确认目标不在 `_trash` 中） |

## Deferred

- **WI-17**（ask-arbiter，可选）：没有 lane，也没有自动测试，由人工核对。
- 计划中 Deferred 清单里的各项都不设计测试。
- mutation 是对 criticalPaths 的度量，不另设用例。
