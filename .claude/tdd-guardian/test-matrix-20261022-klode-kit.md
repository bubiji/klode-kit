# Test Matrix — klode-kit（plan-20261022-klode-kit，M0–M3）

> 依据：`.claude/tdd-guardian/plan-20261022-klode-kit.md`（下称「计划」）；`docs/pipeline/20261022-klode-kit/04-需求摘要与usercase.md`（U1–U8 验收基准，确认版）；`02-概要设计.md` 的 r2–r4 段（r2.2 来源链/打码/字段对应、r2.3 状态机、r2.6 退出码、r3.1 三段不变量、r3.2 键分层与发布、r3.3、r3.5、r4.1–r4.4）。未读私有项目 002。
> 本文件只做测试设计，没有改任何源码或测试文件。仓库现状：`src/`、`tests/` 只有 `.gitkeep`（全新仓库，各 lane 处于 bootstrap 状态）。

## 0. 读法与约定

### 0.1 每条用例的字段

| 列 | 含义 |
|---|---|
| ID | `<WI>-<序号>`；WI-31 的验收用例用 `U<n>-<序号>`、`P-<序号>`、`UC-<序号>` |
| 测试名 | pytest 函数名。计划「必需测试」里已有的名字原样保留，新增的按同一风格命名 |
| 类 | 五类之一：**成功**、**边界**、**非法**（非法输入/守卫）、**边缘**（失败路径、状态迁移、幂等、崩溃恢复，以及生成域上的性质）、**并发** |
| lane | `unit` / `contract_replay` / `cli_integration` / `model_integration` / `host_acceptance` / `manual`，与计划 §0.1 的 pytest 标记一致；子标记（`macos`、`macos_mlx`、`needs_node`）写在 lane 后面 |
| fixture | `tests/fixtures/` 下的合成场景名（计划 WI-00a 清单）；`hyp:` 表示 hypothesis 生成域；`tmp:` 表示在 tmp 里现场构造；标「新增」的见 §7 |
| 操作 | 被测入口。一律走公开入口：`bin/klode-kit <子命令>`、模块公开函数或真实子进程 |
| 断言 | 可观察的结果：返回值、退出码、文件内容/sha、目录树 diff、stdout、进程状态。**没有一条只断言「被调用」**。用到替身调用日志时，断言的是日志里的内容或条目数（如「零条」），并且一定同时有一个 L1–L5 的结果断言 |
| L·S | 断言级别 L1–L7 · 规格级别 S1–S6（policy-core） |
| 闸门 | 需要手动闸门 G1–G5 时标出；空着表示全自动 |

**格式说明**：policy 模板要求每条用例写「替身边界 / 配对的集成用例 / 什么重构会打破它」。这三项在同一个 WI 里对所有用例都一样，所以写在每个 WI 的表头，不逐行重复；个别例外在该行断言里注明。每个 WI 都必须回答 Law 问题。

### 0.2 替身边界（只用计划 §0.4 列出的四类）

| 边界 | 替身 | 本矩阵里的配对真实用例 |
|---|---|---|
| `claude`/`codex` 子进程 | `tests/fakes/bin/{claude,codex}` 读 `tests/fixtures/replay/` | 00c2-06/07/08、00c3-09、00d2-01/02、06-11/12、11b-09、12-04、U5-02 |
| Vision / MLX / tesseract | 原始引擎输出回放 | 04a-10/11/12、05a-07、U2-02 |
| `klode` CLI（只在 replay 全链里） | 录制的 stdout/JSON | 01a-10、01b-07、02a-08、02c-01、02d-06、09b-09、10a-01/02/09、11a-01、U4-01、U7-01、P-02 |
| 时钟 | 注入 `now()` | 不需要配对 |

自己写的模块一律不 mock。另有两处替身不在上面四类里，都是宿主运行时边界，已在对应行注明并给出配对：`claude mcp get` 的录制输出（00d2-08，配对 00d2-06）；Workflow 宿主的 `agent()` 桩（14-03/05，配对 14-04）。

### 0.3 崩溃注入的方式（要实现者确认）

00b1-11、04b-13、08c-13、09d-02 都要求「在写点崩溃」。在不 mock 自有模块的前提下，可选两种做法：

1. **真子进程 + `SIGKILL`**。子进程运行到 `KLODE_KIT_CRASH_AT=<point>` 指定的点时自杀；点名由生产代码里的 `crashpoint("<name>")` 提供，默认什么也不做。
2. **随机时刻 `SIGKILL`**。用 hypothesis 生成延时，事后只检查不变量。

本矩阵两种都用：做法 1 枚举全部写点（S5），做法 2 防止漏掉写点（S4）。做法 1 会在生产代码里留一个测试缝，与 policy 第 13 条有张力，需要在 WI-00s 记录一次决定。

---

## 1. 关键不变量 → 覆盖用例

| # | 不变量（出处） | 覆盖用例（最强的放前面） | 最高规格 |
|---|---|---|---|
| K1 | **四路全齐且一致才 auto-agree / 自动 verified**（r2.2-D、r2.3 ①、04 U3） | 07c-05（随机四路网格，双向蕴含）、08a-07、08c-10（跨 compare→gate→writeback 的性质）、U3-02（metamorphic：翻转一路一个值，页从 verified 变为 pending_confirm）、07c-03/04/09/12 | S6 |
| K2 | **uncertain 非空不得 verified**（04 §2、r2.3、r3.3） | 08a-07（性质）、08c-11（伪造的 choices 也不能把页级 uncertain 的记录点成 verified）、08a-05、08c-03、U8-03、09b-06 | S4 |
| K3 | **effective 只由 writeback 推进，候选失败不动旧 verified**（r4.1） | 08c-08（随机提交序列下 rev 单调）、08c-09（失败候选下 effective 字节不变）、08c-16（其他所有阶段运行前后 `_by_id` 字节不变）、08d-06 / U6-01、09b-05、12-03 | S6 |
| K4 | **来源链三段 + 版本绑定**（r3.1） | 00b3-12（任一单点损坏都能检出）、00b3-10（apply/unapply 往返）、00b3-01…09/11、01a-07、P-01（流水线回放产物）、P-02（真 klode 产物）、01a-04、01a-10 | S5 |
| K5 | **发布指针始终完整；prune 永不移动受保护 build**（r3.2、r4.2） | 09d-02（任意崩溃点）、09d-03（切换 200 次期间并发 readlink 从不悬空）、09d-08（随机日志 + 租约）、09d-09（builds + _trash 数量守恒，不删）、09d-11（真双进程租约竞态）、09d-07、09d-16 | S5 |
| K6 | **外发只认 MANIFEST sha，失败零调用**（r2.2-B、r3.5、r4.3） | 05b-07（性质：发生调用 ⟺ sha ∈ 登记表）、05b-08（TOCTOU：检查后改字节）、05b-03（路径在登记表里但字节被改）、05b-02/04/06/14、00c3-10、U2-03 | S4 |
| K7 | **同一 MCP 进程 A→B 切换**（r4.2） | 11a-01（S5）、11a-02（任意切换序列下每次 `kb_version` == readlink）、11a-09（调用中途切换）、11a-06/07（降级路径同样成立）、11b-07/08 | S5 |
| K8 | **13 步固定顺序；`builds/` 之外零写入**（r2.6、r3.2） | 09b-03（任一步 k 失败：k 之后不执行、指针不动、外部零写入）、09b-02（树快照 diff）、09b-01、00b1-04（schema 层拒绝非 13 项顺序）、09b-07 | S5 |

---

## 2. M0 · spike + 契约

### WI-00s：仓库与测试脚手架

**Law**：bootstrap 例外是单调的——lane 一旦出现过 ≥1 个用例，之后任何 0 用例都 FAIL，例外不再恢复。除此之外都是守卫，没有 law。
**替身 / 配对**：`claude` 假可执行文件本身就是被测对象（00s-05/06），不需要配对。**重构敏感点**：只通过 `run_lane` CLI、`privacy_scan` CLI 和 pytest 子会话断言，改内部函数名不会打破。
**并发**：两条 lane 并行写 receipt（00s-13）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00s-01 | test_run_lane_zero_tests_fails | 非法 | `unit` | tmp：空用例目录 + 非空 lane 历史 | `run_lane unit` | exit ≠ 0；stdout 含 `0 tests discovered`；`work/lanes/unit.json` 的 status=FAIL | L1·S3 | |
| 00s-02 | test_run_lane_bootstrap_warns_only_when_never_had_tests | 边界 | `unit` | tmp：lane 历史为空 | `run_lane unit` | exit 0；stderr 有 bootstrap 警告；receipt `bootstrap:true`，status 不是 PASS | L1·S2 | |
| 00s-03 | test_bootstrap_exception_permanently_ends | 边缘 | `unit` | hyp：每次运行的用例数序列（含 0） | 依次 `run_lane` | 第一次出现 ≥1 之后，所有 0 用例的运行都 FAIL；在那之前只 WARN | L1·S5 | |
| 00s-04 | test_socket_blocked | 非法 | `unit` | 无 | 在用例里连 127.0.0.1 | 抛 `SocketBlockedError` | L1·S3 | |
| 00s-05 | test_fake_claude_unconfigured_errors | 非法 | `contract_replay` | `tests/fakes/bin/claude`，不配场景 | 子进程 `claude -p x` | exit ≠ 0；stderr 含 `no replay scenario`；stdout 为空 | L3·S3 | |
| 00s-06 | test_fake_claude_replays_configured_scenario | 成功 | `contract_replay` | `replay/claude/<sha>.json` | 子进程 `claude -p …` | stdout 与录制逐字节相同；调用日志多一条，含 argv sha | L3·S1 | |
| 00s-07 | test_gate_cloud_without_ack_usage_error | 非法 | `unit` | pytester 子会话，含一个 `gate_cloud` 用例 | `pytest -m model_integration`，不给 ACK | 子会话 exit 2；执行的用例数为 0（不是 skip） | L1·S3 | |
| 00s-08 | test_privacy_scan_boundaries | 边界 | `unit` | 内联：17/18/19 位证号（含末位 X）、10/11/12 位手机号、白名单假名 | `privacy_scan` | 只命中 18 位证号和 11 位手机号；17/19/10/12 位和白名单都不命中 | L1·S2 | |
| 00s-09 | test_privacy_scan_name_label_nonwhitelist | 非法 | `unit` | `姓名：张三` 与 `姓名：测试患者甲` | `privacy_scan` | 命中前者，不命中后者；exit ≠ 0 | L1·S3 | |
| 00s-10 | test_privacy_scan_unlisted_image | 非法 | `unit` | tmp 仓库：未登记的 png + `docs/wua/x.svg` | `privacy_scan` | exit ≠ 0；报告列出该 png，不列 svg | L2·S3 | |
| 00s-11 | test_privacy_scan_detection_monotone | 边缘 | `unit` | hyp：任意文本 + 在任意位置插入一个合成证号 | 插入前后各扫一次 | 插入后的命中数 ≥ 插入前 + 1（metamorphic） | L1·S6 | |
| 00s-12 | test_launcher_version | 成功 | `cli_integration` | 已建好的 `.venv` | `bin/klode-kit --version` | stdout == `importlib.metadata.version("klode-kit")` | L3·S1 | |
| 00s-13 | test_lane_receipts_concurrent_lanes_no_clobber | 并发 | `unit` | tmp `work/lanes/` | 两个 `run_lane` 进程并发（unit、contract_replay） | 两个 receipt 都在，各自是合法 JSON 且通过 `lane_receipt` schema | L2·S3 | |

### WI-00a：合成 fixtures 生成器

**Law**：生成是确定的，同种子得到同字节（S5）；swap 注入器保持值的多重集合，且恰好 2 个位置不同（S4）；MANIFEST 与 fixtures 树双向一致。
**替身 / 配对**：无。**重构敏感点**：断言的是生成产物的字节和 MANIFEST，改生成器内部结构不会打破。
**并发**：不适用（生成器单进程运行，没有共享状态）。
**lane 调整**：计划把 `test_generation_deterministic` 放在 unit，但又规定只在 macOS 上再生成。这样在 Linux CI 上它会被 skip，等于拿 skip 冒充 PASS，所以移到 `cli_integration` 的 `macos` 子集（00a-01/02）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00a-01 | test_generation_deterministic | 边缘 | `cli_integration` macos | 固定字体和种子 | `gen_all` 生成到两个 tmp 目录 | 两边逐文件 sha 相同 | L2·S5 | |
| 00a-02 | test_gen_all_check_matches_manifest | 成功 | `cli_integration` macos | 全部场景 | `gen_all --check` | exit 0；与 `MANIFEST.json` 逐字节一致 | L2·S1 | |
| 00a-03 | test_manifest_complete | 边界 | `unit` | `tests/fixtures/` 整棵树 | 遍历树并读 MANIFEST | 树里每个文件都在 MANIFEST 中且 sha 正确；MANIFEST 没有多余条目（双向） | L2·S2 | |
| 00a-04 | test_manifest_detects_tampered_file | 非法 | `unit` | tmp 拷贝后改 1 字节 | MANIFEST 校验 | 报出被改的文件，exit ≠ 0 | L1·S3 | |
| 00a-05 | test_names_allowlist | 非法 | `unit` | 全部文本和真值 | 抽取人名和编号 | 全部 ∈ 白名单（测试患者甲、T-0000001…、示例片）；`privacy_scan` 0 命中 | L1·S3 | |
| 00a-06 | test_swap_injector_property | 边缘 | `unit` | hyp：任意表格 × 任意相邻两行 | `inject_swap` | 值的多重集合不变；恰好 2 个位置不同；其他行不变 | L1·S4 | |
| 00a-07 | test_colshift_injector_property | 边缘 | `unit` | hyp：任意表格 × 任意行 | `inject_colshift` | 该行的数值出现在单位列，数值列为空；其他行不变 | L1·S4 | |
| 00a-08 | test_missingrow_injector_property | 边缘 | `unit` | hyp | `inject_missingrow(route)` | 目标路行数 = 原行数 − 1；其余三路不变 | L1·S4 | |
| 00a-09 | test_degenerate_injector_ratio_boundary | 边界 | `unit` | 构造 `repeat_ratio` 为 0.30、0.31 的输出 | `repeat_ratio()` + 判定 | 0.31 判为 degenerate，0.30 不判（严格大于） | L1·S2 | |
| 00a-10 | test_truth_designed_ge20_all_agree_fields | 边界 | `unit` | 结构化真值 | 统计设计中四路一致的字段数 | ≥ 20 | L1·S2 | |
| 00a-11 | test_fixture_scenarios_exist_and_described | 成功 | `unit` | 计划 WI-00a 的场景清单 | 列目录、读 README | 清单里每个场景目录都存在；README 每个场景都有描述，并声明「全部合成」 | L2·S1 | |

### WI-00b1：契约 schema 与 receipt 写入

**Law**：receipt 往返——写入再读出相等（S5）；原子性——崩溃后看到的要么是完整的旧内容，要么是完整的新内容（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是 `jsonschema` 校验结果、磁盘文件和异常类型。
**并发**：8 个写者同时写同一个 receipt（00b1-13）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00b1-01 | test_schema_examples_valid | 成功 | `unit` | `tests/contract/schema_examples/*/valid/` | 参数化：16 个 schema 逐一 validate | 全部合法样例通过 | L1·S2 | |
| 00b1-02 | test_schema_examples_invalid | 非法 | `unit` | `*/invalid/` | 参数化 validate | 每个非法样例都被拒，错误路径就是样例声明的那个字段 | L1·S3 | |
| 00b1-03 | test_choices_partial_rejected | 非法 | `unit` | choices 样例，`kind=partial` | validate | 被拒，错误路径为 `items[0].choice.kind`（r3.3） | L1·S3 | |
| 00b1-04 | test_build_steps_order_exactly_13 | 边界 | `unit` | `build_receipt`：12 项、14 项、13 项换序、正确的 13 项 | validate | 只有正确的 13 项顺序通过（K8） | L1·S2 | |
| 00b1-05 | test_geometry_enum | 边界 | `unit` | `geometry` 取 exact、approx、""、null | validate | 只接受 exact 和 approx | L1·S2 | |
| 00b1-06 | test_record_status_equals_effective_or_latest_candidate | 边缘 | `unit` | hyp：有或没有 effective 的 record + 候选序列 | validate | 接受 ⟺ 顶层 `status == effective.status`；没有 effective 时 ⟺ `status == candidates[-1].status`（R4-01） | L1·S4 | |
| 00b1-07 | test_export_map_value_audited_and_transforms_required | 非法 | `unit` | 用 `value_raw` 当字段名；缺 `transforms` | validate | 两种都被拒 | L1·S3 | |
| 00b1-08 | test_isolation_receipt_required_bindings | 非法 | `unit` | 分别缺 `cli_version`、`effective_config_sha`、`argv_sha` | validate | 三种各自被拒 | L1·S3 | |
| 00b1-09 | test_lane_receipt_release_sha_required | 非法 | `unit` | `env_fingerprint` 缺 `release_sha` | validate | 被拒 | L1·S3 | |
| 00b1-10 | test_receipt_write_invalid_no_file | 非法 | `unit` | tmp 目录 | `receipts.write(非法内容)` | 抛 `ReceiptInvalid`；目录列表前后相同（没有生成文件，也没有 `.tmp`） | L2·S3 | |
| 00b1-11 | test_receipt_crash_no_partial | 边缘 | `unit` | tmp 目录，已有旧 receipt | 子进程在 `KLODE_KIT_CRASH_AT=receipts.before_replace` 处被 SIGKILL | 目标文件仍是完整的旧内容，能通过 schema；目标路径上没有半个文件 | L2·S5 | |
| 00b1-12 | test_receipt_roundtrip_property | 边缘 | `unit` | hyp：按 schema 生成的合法 receipt | 写入后读出 | 读出 == 写入（law：往返） | L1·S5 | |
| 00b1-13 | test_receipt_concurrent_writers_whole_file | 并发 | `unit` | tmp 目录 | 8 个进程同时把不同的合法 receipt 写到同一路径 | 最终文件恰好等于其中某一份完整内容，能通过 schema；没有混写 | L2·S5 | |

### WI-00b2：键、指纹与配置

**Law**：`batch_id` 与输入顺序无关（S4）；`rec_id` 与日期无关（S6）；`cfg_fp` 对 r4.4 列出的每一项输入都敏感（S4），对 TOML 的规范化差异不敏感（S6）；`attempt_id` 唯一且连续（S5）。
**替身 / 配对**：时钟注入（00b2-04/15），不需要配对。**重构敏感点**：只断言公开函数的返回值和 `ConfigError` 的退出码。
**并发**：8 个进程同时分配 attempt（00b2-08）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00b2-01 | test_batch_id_permutation_invariant | 边缘 | `unit` | hyp：sha 列表 | 打乱顺序后再算；增删一项后再算 | 打乱后相同；增删一项后不同 | L1·S4 | |
| 00b2-02 | test_batch_id_duplicate_image | 边界 | `unit` | 同一张图出现 1 次和 2 次 | `batch_id()` | **规格待定（§7-F3）**：期望按集合语义（重复图不改变 id），manifest 记 `duplicate`。实现前要先把这个决定记下来 | L1·S2 | |
| 00b2-03 | test_rec_id_date_metamorphic | 边缘 | `unit` | hyp：记录 | 只改日期；或只改 doc_id/页/`record_idx` 中的一项 | 只改日期时 `rec_id` 不变；改后三者任一项时 `rec_id` 变化 | L1·S6 | |
| 00b2-04 | test_id_formats | 成功 | `unit` | 固定输入，注入时钟 2026-10-22T00:00:00Z | 生成全部 id | `doc_id` 匹配 `^d-[0-9a-f]{12}$`；`page_id` 形如 `<doc>/p001`；`rec_id` 匹配 `^r-[0-9a-f]{12}$`；`build_id` 匹配 `^b-20261022T000000Z-[0-9a-f]{8}$`；`decision_id` 形如 `<batch>/<seq>` | L1·S1 | |
| 00b2-05 | test_cfg_fp_sensitivity | 边缘 | `unit` | hyp：从 r4.4 的输入（生效配置、prompt_sha、schema_sha、model、effort、adapter_code_version、der_sha 集合）中任选一项变异 | `cfg_fp()` | fp 改变 | L1·S4 | |
| 00b2-06 | test_cfg_fp_canonical | 边缘 | `unit` | 打乱 TOML 键序、增删空白；只改 `config_version` | `cfg_fp()` | fp 不变 | L1·S6 | |
| 00b2-07 | test_cfg_fp_der_sha_order_invariant | 边界 | `unit` | 同一组 der_sha 的不同排列 | `cfg_fp()` | 相同 | L1·S4 | |
| 00b2-08 | test_attempt_concurrent_unique | 并发 | `unit` | tmp 文件系统 | 8 个真进程同时 `allocate_attempt(batch, engine, fp)` | 拿到的正好是 `t1…t8`，连续且不重复；8 个目录都存在，目录名带 attempt | L2·S5 | |
| 00b2-09 | test_attempt_monotonic_across_redo | 边缘 | `unit` | tmp：已有 `t1` 且为 done | 连续两次 `--redo` 分配 | 依次得到 `t2`、`t3`，不复用 `t1` | L2·S5 | |
| 00b2-10 | test_attempt_scoped_by_engine_and_fp | 边界 | `unit` | 同一 batch 下两个 engine、两个 fp | 各分配一次 | 每个 `(engine, fp)` 都从 `t1` 开始 | L2·S2 | |
| 00b2-11 | test_config_override_applies | 成功 | `unit` | `synth_project/klode-kit.toml` 设 `route_retry=5` | `config.load(project)` | `route_retry==5`，其他键等于 defaults | L1·S1 | |
| 00b2-12 | test_config_unknown_key_exit40 | 非法 | `unit` | 覆盖文件里有未知键 `routez` | 经 `bin/klode-kit` 加载 | `ConfigError`，退出码 40，消息含 `routez` | L1·S3 | |
| 00b2-13 | test_config_missing_default_key_exit40 | 非法 | `unit` | tmp 拷贝的 defaults 删掉 `line_sim_tau` | 加载 | 退出码 40，消息含键名 | L1·S3 | |
| 00b2-14 | test_config_wrong_type_exit40 | 非法 | `unit` | `route_retry="two"` | 加载 | 退出码 40 | L1·S3 | |
| 00b2-15 | test_build_id_uses_injected_clock | 边界 | `unit` | 两个不同的注入时刻，inputs 相同 | `build_id()` | 时间段等于注入值；inputs 相同时 hash 段相同 | L1·S2 | |

### WI-00b3：来源链校验器 + roundtrip

**Law**：`unapply(apply(t, x)) == x`（S5）；校验器完备——任意单点损坏都能检出（S4）；rev 前进后旧 build 仍然自洽（S6）。
**替身 / 配对**：无（只读）。**重构敏感点**：断言的是违例 code 的集合，改展示文本不会打破。
**并发**：不适用（校验器只读）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00b3-01 | test_clean_chain_zero_violations | 成功 | `contract_replay` | `fx-chain` | `provenance.validate()` | 返回 `[]` | L1·S1 | |
| 00b3-02 | test_variant_span_text_ne_candidate | 非法 | `contract_replay` | `fx-chain/broken-1` | validate | 恰好一个违例 `span_mismatch`，没有别的 | L1·S3 | |
| 00b3-03 | test_variant_der_parent_mismatch | 非法 | `contract_replay` | `fx-chain/broken-2` | validate | 恰好一个 `der_parent_mismatch` | L1·S3 | |
| 00b3-04 | test_variant_manual_missing_region | 非法 | `contract_replay` | `fx-chain/broken-3` | validate | 恰好一个 `manual_evidence_missing` | L1·S3 | |
| 00b3-05 | test_variant_two_effective_decisions | 非法 | `contract_replay` | `fx-chain/broken-4` | validate | 恰好一个 `multiple_effective_decisions` | L1·S3 | |
| 00b3-06 | test_variant_alias_on_numeric | 非法 | `contract_replay` | `fx-chain/broken-5` | validate | 恰好一个 `transform_on_numeric` | L1·S3 | |
| 00b3-07 | test_variant_export_rev_ne_binding | 非法 | `contract_replay` | `fx-chain/broken-6` | validate | 恰好一个 `rev_binding_mismatch` | L1·S3 | |
| 00b3-08 | test_manual_fix_passes_without_any_route_correct | 边界 | `contract_replay` | `fx-chain/legal-manual`（四路都读成 10.5，人工改为 1.05） | validate | 0 违例；段 ① 对 10.5 仍然成立 | L1·S2 | |
| 00b3-09 | test_legal_variants_pass | 边界 | `contract_replay` | `fx-chain/legal-{dateshift,alias,oldbuild,pdftext_dpi}` | 参数化 validate | 4 个都是 0 违例 | L1·S2 | |
| 00b3-10 | test_transforms_apply_unapply_identity | 边缘 | `unit` | hyp：transform 序列 × 修订值 | 先 `apply` 再 `unapply` | 回到原值；`alias` 只改 `item_slug`；数值和单位上的 transform 集合为空 | L1·S5 | |
| 00b3-11 | test_advance_rev_keeps_old_build_valid | 边缘 | `contract_replay` | `fx-chain` + 追加 rev 2 | 校验旧 build；再把旧 export_map 绑到新 rev 上校验 | 旧 build 0 违例；错绑时报 `rev_binding_mismatch` | L1·S6 | |
| 00b3-12 | test_single_corruption_always_detected | 边缘 | `contract_replay` | hyp：`fx-chain` 里任选一条记录 × 6 种损坏之一 | 损坏后 validate | 至少报出一个该类违例，没有漏检 | L1·S4 | |
| 00b3-13 | test_validator_is_readonly | 边缘 | `contract_replay` | tmp 拷贝的 `fx-chain` | validate | 前后树 sha 相同 | L2·S1 | |

### WI-00c1：云端 argv 构建 + 确定性隔离审查

**Law**：审查对 argv 是单调的——删掉任何一个必含项、或加入任何一个禁用项都 FAIL（S4）；builder 生成的任何 argv 都能通过 checker（S4）；receipt 的有效性绑定在 `(cli_version, effective_config_sha, argv_sha)` 上。
**替身 / 配对**：假 `claude`/`codex` 只用来回报 `--version`（00c1-11），配对 00c2-06/07。**重构敏感点**：断言的是 checker 的逐项布尔值和 hook 进程的退出码。
**并发**：20 个 hook 进程并发写日志（00c1-17）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00c1-01 | test_claude_argv_contract | 成功 | `unit` | 默认配置 | `cloud_argv.claude(...)` | argv ⊇ r4.3 必含集合；与禁用集合（含 `--bare`、`--add-dir`、`--agents`、`--plugin-dir`、`--mcp-config`、`--dangerously-skip-permissions`）没有交集 | L1·S1 | |
| 00c1-02 | test_codex_argv_contract | 成功 | `unit` | 默认配置 | `cloud_argv.codex(...)` | 含 `-C <tmp> -s read-only --ephemeral --ignore-user-config --ignore-rules -c mcp_servers={}`；不含 `--full-auto`、`-s danger*` | L1·S1 | |
| 00c1-03 | test_builder_output_always_passes_checker | 边缘 | `unit` | hyp：model、effort、tmp 名、der_sha | 先 build 再 `isolation_check` | 所有检查项都为 True | L1·S4 | |
| 00c1-04 | test_required_flag_removal_fails | 边缘 | `unit` | hyp：必含项的任意非空子集 | 删掉后 check | FAIL；`checks[]` 里被删的每一项都是 False | L1·S4 | |
| 00c1-05 | test_forbidden_flag_added_fails | 边缘 | `unit` | hyp：在任意位置插入一个禁用项 | check | FAIL，并点名该项 | L1·S4 | |
| 00c1-06 | test_cwd_inside_project_fails | 非法 | `unit` | tmp 项目：cwd=`project/sub`；cwd 是项目外的符号链接但指向项目内 | check | 两种都 FAIL（按 realpath 判断） | L1·S3 | |
| 00c1-07 | test_cwd_sibling_prefix_not_inside | 边界 | `unit` | 项目 `/tmp/x/p`，cwd `/tmp/x/p2` | check | 判定为「不在项目内」。r4.3 字面写的是 `startswith`，这条用例逼出按路径分量比较（§7-F1） | L1·S2 | |
| 00c1-08 | test_env_whitelist | 非法 | `unit` | env 含 `CLAUDE_CONFIG_DIR` 或白名单外的键 | check | FAIL；只有白名单键时 PASS | L1·S3 | |
| 00c1-09 | test_tmpdir_extra_file_or_unlisted_png_fails | 非法 | `unit` | 临时目录里多一个文件；或 png 的 sha 不在 MANIFEST | check | 两种都 FAIL | L1·S3 | |
| 00c1-10 | test_cloud_settings_sha_mismatch_fails | 非法 | `unit` | tmp 拷贝的 `cloud_settings.json` 改 1 字节 | check | FAIL | L1·S3 | |
| 00c1-11 | test_cli_version_boundary | 边界 | `unit` | 假 `claude --version` 依次输出 2.1.258、2.1.259、2.1.1000 | check | 依次为 FAIL、PASS、PASS（防止按字典序比较） | L1·S2 | |
| 00c1-12 | test_receipt_invalid_on_binding_change | 边缘 | `unit` | 一份 PASS 的 receipt；env 里 cli_version、配置 sha、argv sha 之一变化 | `receipt_still_valid(env)` | 任一项变化都返回 False；全不变时返回 True | L1·S3 | |
| 00c1-13 | test_guard_hook_subprocess_outside_denied | 非法 | `contract_replay` | 真 python 子进程，stdin 是 `file_path` 在 cwd 外的 JSON | 运行 `isolation_guard` | exit 2；日志新增一行，含该路径和 `deny` | L3·S3 | |
| 00c1-14 | test_guard_hook_inside_allowed | 成功 | `contract_replay` | `file_path` 在 cwd 内 | 同上 | exit 0；日志一行，含 `allow` | L3·S1 | |
| 00c1-15 | test_guard_hook_malformed_fail_closed | 非法 | `contract_replay` | stdin 为 `{`、空、缺 `file_path` | 同上 | 三种都 exit 2 | L3·S3 | |
| 00c1-16 | test_guard_hook_traversal_and_symlink | 边界 | `contract_replay` | `cwd/../x`；`cwd/link` 指向外部 | 同上 | 两种都 exit 2 | L3·S2 | |
| 00c1-17 | test_guard_concurrent_log_intact | 并发 | `contract_replay` | 20 个 hook 进程并发 | 同上 | 日志恰好 20 行，每行都是完整 JSON | L2·S3 | |

### WI-00c2：隔离实测 spike【G2】

**Law**：判定只看宿主证据。只有每个 canary 都「已拒绝」且输出里没有 nonce 才算 PASS；「未尝试」和 ENOENT 都判 FAIL。这是分类函数上的全序约束，用 S2/S3 表驱动覆盖。
**替身 / 配对**：00c2-02/03/04/10 使用手写的合成事件流 `replay/probe-synth/`（新增），配对 00c2-06/07/08。**重构敏感点**：断言的是分类结果和 receipt。
**并发**：不适用（探针串行执行）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00c2-01 | test_canary_path_preexists | 非法 | `unit` | tmp `synth_project` + tmp HOME，故意少建一个 canary | `isolation_probe.prepare()` | 在任何调用之前就抛异常；假 claude 的调用日志为空 | L2·S3 | |
| 00c2-02 | test_classify_denied_enoent_unattempted | 边界 | `unit` | `replay/probe-synth/{denied,enoent,unattempted,mixed}.jsonl`（新增） | `classify(events)` | 依次为 denied、ENOENT、unattempted；mixed 判 FAIL；只有全部 denied 才 PASS | L1·S2 | |
| 00c2-03 | test_classify_ignores_model_narrative | 非法 | `unit` | 事件流里 assistant 文本说「已拒绝」，但 tool_result 是成功并含 nonce | classify | FAIL；证据字段取自 tool_result，不取自模型文本 | L1·S3 | |
| 00c2-04 | test_classify_codex_json_events | 成功 | `unit` | `probe-synth/codex-denied.jsonl` | classify | 每个 canary 都是 denied，PASS | L1·S1 | |
| 00c2-05 | test_canary_cleanup_only_own_files | 边缘 | `unit` | tmp HOME 里事先放一个 `canary-old.txt` | probe 跑完（用假 claude） | 只删掉本次 nonce 对应的文件；`canary-old.txt` 还在且字节不变 | L2·S3 | |
| 00c2-06 | test_claude_probe_denials | 成功 | `model_integration` | `synth_project` + `$HOME` canary | 真 `claude` 运行审查通过的 argv | 每个 canary 都是 denied；输出不含 nonce；receipt 为 `cloud_isolation: PASS` | L3·S3 | G2 |
| 00c2-07 | test_codex_probe_denials | 成功 | `model_integration` | 同上 | 真 `codex exec --json` | 同上 | L3·S3 | G2 |
| 00c2-08 | test_startup_context_nonce_absent | 边缘 | `model_integration` | nonce 放在祖先目录的 CLAUDE.md 和合成项目的 `.claude/` 里 | 真 `claude` | 模型输出和 init/system 事件里都没有 nonce；拿不到证据时记 inconclusive，按 FAIL 处理（R-5） | L3·S3 | G2 |
| 00c2-09 | test_isolation_receipt_written_and_valid | 成功 | `model_integration` | 06/07 的产物 | 读 `isolation_receipt.json` | 通过 schema；`argv_sha` 等于实际执行的 argv 的 sha；`probe.cost_usd` 非空 | L2·S1 | G2 |
| 00c2-10 | test_route_disabled_when_probe_fails | 边缘 | `unit` | `probe-synth`：codex 读到了 canary | 根据 classify 结果更新配置 | `routes_enabled.codex=false`，claude 不受影响；之后往 codex 发送时 exit 61 | L1·S3 | |

### WI-00c3：`eval_fields` / `cer` + 四路 spike【G2 只在实跑那一步】

**Law**：对真值的扰动只会让指标变差或不变（S4）；`cer(x,x)=0` 且 `cer ≥ 0`（S4）；数字只做 NFKC，不做别的折叠（S6）。
**替身 / 配对**：无（00c3-10 用假 claude 只是为了证明零调用），配对 00c3-09。**重构敏感点**：断言的是指标数值。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00c3-01 | test_false_auto_pass_zero_samples_fails | 非法 | `unit` | `auto_verified_total=0` | `eval_fields` | 错误自动通过率为 `n/a`；lane FAIL，原因 `no_auto_verified_samples` | L1·S3 | |
| 00c3-02 | test_metrics_perfect_on_truth | 成功 | `unit` | 真值对真值 | eval | 精确匹配率 1.0、关联准确率 1.0、缺字段率 0、错误自动通过率 0 | L1·S1 | |
| 00c3-03 | test_metrics_on_truth_vs_perturbed | 边缘 | `unit` | hyp：逐步加大的扰动集合 | eval | 精确匹配率单调不升，缺字段率单调不降 | L1·S4 | |
| 00c3-04 | test_false_auto_pass_one_wrong_fails | 边界 | `unit` | 20 个 auto 字段里错 1 个 | eval | 比率为 0.05，FAIL（阈值是 0） | L1·S2 | |
| 00c3-05 | test_all_agree_fields_19_vs_20 | 边界 | `unit` | 运行结果中四路一致的字段为 19 个和 20 个 | eval | 19 FAIL，20 PASS | L1·S2 | |
| 00c3-06 | test_cer_selftest_port | 成功 | `unit` | 189 的自测向量 | `cer()` | 与 189 的期望值相等 | L1·S1 | |
| 00c3-07 | test_cer_numeric_not_folded | 边缘 | `unit` | 「10.5」对「1.05」；「１０.５」对「10.5」 | `cer()` | 前者 > 0；后者 == 0（只做 NFKC） | L1·S6 | |
| 00c3-08 | test_cer_identity_and_nonneg | 边缘 | `unit` | hyp：字符串对，含空串 | `cer()` | `cer(x,x)==0`；`cer ≥ 0`；参考串为空时行为确定，不抛未定义的异常 | L1·S4 | |
| 00c3-09 | test_hardest_pages_4routes_live | 成功 | `model_integration` | `fx-u1-3labs`、`fx-rx-stamp`、`fx-lowres`、`fx-vertical`（打码后） | 四路实跑 | 录制写入 `replay/<engine>/<sha>.json` 并标 `recorded:true`；错误自动通过率 == 0；四路一致字段 ≥ 20；spike 文档里有 R3-05 粒度结论和 ⑤′ 的取值 | L3·S1 | G2 |
| 00c3-10 | test_live_requires_isolation_receipt | 非法 | `unit` | 没有有效的 isolation receipt | `KLODE_KIT_REAL=1` 启动 spike（假 claude） | exit 61；假 claude 调用日志为空 | L2·S3 | |

### WI-00d1：执行桥（确定性部分）

**Law**：任意任务数和并发上限下，同时运行的任务数 ≤ 上限（S4）；超时或取消后不留存活的子孙进程。
**替身 / 配对**：假 `claude` 的场景有成功、永久 sleep、exit 1、畸形 JSON、派生孙进程，配对 00d2-01/02。**重构敏感点**：断言的是进程存活状态（`os.kill(pid,0)`）、时间戳日志的重叠程度和 receipt，不断言线程池的实现。
**并发**：00d1-06、00d1-07。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00d1-01 | test_argv_contract | 成功 | `unit` | `agents/*.md` | `bridge.build_argv(task)` | 含 `--agents <内联 JSON> --agent klode-card-writer --permission-mode dontAsk`、计划列出的 allowed/disallowed 工具、`--output-format json --json-schema --max-budget-usd --no-session-persistence`；cwd 为 `builds/<id>/`；没有 `--force`；内联 JSON 能解析出 klode-card-writer | L1·S2 | |
| 00d1-02 | test_allowed_tools_use_edit_not_write | 非法 | `unit` | 同上 | build_argv | 不出现任何 `Write(` 路径规则（H11）；checker 一律经启动器 `*/bin/klode-kit` 调用 | L1·S3 | |
| 00d1-03 | test_success_writes_receipt_with_cost | 成功 | `contract_replay` | 假 claude：成功 | `bridge.run([task])` | writer receipt 的 status=done 且通过 schema，`cost_usd` 是非空数值；卡文件存在 | L2·S1 | |
| 00d1-04 | test_timeout_kills_process_group | 边缘 | `contract_replay` | 假 claude：永久 sleep，并派生一个写 pid 文件的孙进程 | run，`writer_timeout_s=1` | 超时后子进程和孙进程都 ESRCH；receipt 为 timeout | L4·S3 | |
| 00d1-05 | test_timeout_boundary | 边界 | `contract_replay` | 假 claude 分别 sleep 0.5s、1.5s，timeout=1s | run | 前者 done，后者 timeout | L2·S2 | |
| 00d1-06 | test_concurrency_bound | 并发 | `contract_replay` | hyp：任务数 1–12、并发上限 1–4；假 claude 记录开始和结束时间戳 | run | 最大重叠数 ≤ 上限；任务数 ≥ 上限时重叠数能达到上限（证明不是串行） | L2·S4 | |
| 00d1-07 | test_cancel_propagates | 并发 | `contract_replay` | 10 个任务正在运行 | 给父进程发 SIGINT | 所有子进程都 ESRCH；进行中的任务 receipt 为 `cancelled`；没有写了一半的卡 | L4·S3 | |
| 00d1-08 | test_malformed_retry_exhausted | 边缘 | `contract_replay` | 假 claude 每次都输出畸形 JSON | run | 尝试次数 == `writer_max_rounds`（3）；status=`parse_error` | L1·S3 | |
| 00d1-09 | test_malformed_then_success | 边缘 | `contract_replay` | 第 1 次畸形，第 2 次成功 | run | done；尝试 2 次；只产出一张卡 | L1·S2 | |
| 00d1-10 | test_exit1_failure_recorded | 非法 | `contract_replay` | 假 claude exit 1 | run | status=failed，`exit_code=1`；重试次数不超过上限 | L1·S3 | |
| 00d1-11 | test_zero_tasks | 边界 | `unit` | 空任务列表 | run | 立即返回，receipt 的任务数为 0；没有起任何子进程（假 claude 日志为空） | L1·S2 | |

### WI-00d2：宿主 spike【G2、G5】

**Law**：No law：这是对宿主行为的一次性取证，结论写进 spike 文档，没有可以生成的输入域。
**替身 / 配对**：00d2-08 使用 `claude mcp get` 的录制输出，配对 00d2-06。**重构敏感点**：断言的是宿主产物（文件、`permission_denials`、receipt）。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00d2-01 | test_bridge_writes_one_card_check_pass | 成功 | `model_integration` | `fx-book-aurora` 第 1 章 | 通过 CLI 桥真实写卡 | `cards_content/<ch1>.md` 存在；`check_cards` PASS；`cost_usd` 非空 | L3·S1 | G2 |
| 00d2-02 | test_bridge_edit_library_denied | 非法 | `model_integration` | 同上，任务里要求写 `library/x` | 同上 | `permission_denials` 含该路径；`library/x` 不存在 | L3·S3 | G2 |
| 00d2-03 | test_workflow_stage1_exit10_awaiting | 成功 | `host_acceptance` | `fx-u3-mixed`，在 `tests/host/_lab/` 下运行 | `/klode-kit:kb-pipeline` | 第一段 exit 10；状态 `awaiting_confirm`；receipt 通过 schema | L3·S1 | G2 G5 |
| 00d2-04 | test_workflow_resume_hits_cache | 边缘 | `host_acceptance` | 接着 03 | `args.resume` | 第二段完成；preflight/ocr/audit 的 receipt sha 不变（命中缓存）；能否 headless 记入 spike 文档 | L3·S1 | G2 G5 |
| 00d2-05 | test_trust_probe | 边界 | `host_acceptance` | 已信任的 `_lab` 和新建的 tmp 目录 | `hostenv.trusted()` | 依次为 True、False；无法探测时返回 `undetectable` 并写进文档 | L3·S2 | G5 |
| 00d2-06 | test_mcp_get_env_key_connected | 边缘 | `host_acceptance` | `.mcp.json` 条目带 `env` 键 | 真 `claude mcp get <name>` + 解析 | 解析结果为 connected | L3·S1 | G5 |
| 00d2-07 | test_kb_check_entry_plugin_fields | 成功 | `host_acceptance` | `tests/fixtures/kb/fx-labs-3x` | `claude -p --plugin-dir . "/klode-kit:kb-check --kb …"` | `check_receipt.plugin.{name,version,entry}` 分别等于 plugin.json 的值和 `skill:/klode-kit:kb-check` | L3·S1 | G2 G5 |
| 00d2-08 | test_hostenv_parse_mcp_get_variants | 边界 | `unit` | `claude mcp get` 的录制输出：connected、Pending approval、failed | `hostenv.parse_mcp_get` | 依次解析为 connected、pending、failed | L1·S2 | |

### WI-00e：三张 WUA 图

**Law**：No law：图内容的静态检查。
**替身 / 配对**：无。**重构敏感点**：断言的是 `.wf.json` 的节点和边，以及 wfviz 的退出码。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 00e-01 | test_wfviz_renders | 成功 | `cli_integration` | `docs/wua/*.wf.json` | 运行 wfviz | 三张图都 exit 0；SVG 非空且是合法 XML | L3·S1 | |
| 00e-02 | test_scenarios_exactly_6 | 边界 | `unit` | scenarios.wf.json | 解析 | 场景数 == 6 | L1·S2 | |
| 00e-03 | test_usecases_cover_u1_u8 | 边界 | `unit` | usecases.wf.json | 解析 | 用例集合 == {U1…U8} | L1·S2 | |
| 00e-04 | test_pipeline_stages_and_single_human_step | 成功 | `unit` | pipeline.wf.json | 解析 | 7 个阶段齐全；人工步骤 == [「点选」] | L1·S2 | |
| 00e-05 | test_loops_back_edges | 边缘 | `unit` | 同上 | 解析 | L1–L3 每条 loop 的 from 下标都大于 to 下标 | L1·S2 | |
| 00e-06 | test_state_machine_drawn | 成功 | `unit` | 同上 | 解析 | 节点含 extracted、pending_confirm、verified、disputed；边为 extracted→pending_confirm→{verified, disputed}；有说明 effective/candidate 的节点 | L1·S1 | |
| 00e-07 | test_no_forbidden_keys_and_all_artifacts_referenced | 非法 | `unit` | 同上 | 解析 | 没有禁用键；每个工件至少被一步引用 | L1·S3 | |
| 00e-08 | test_wfviz_src_missing_fails | 非法 | `cli_integration` | 不设 `WFVIZ_SRC` | 用 pytester 跑 test_wua | 结果是 failed，不是 skipped | L1·S3 | |

---

## 3. M1 · OCR + 审计

### WI-04a：本机引擎 + 几何

**Law**：坐标往返——像素 → 引擎原生坐标 → 像素，误差 ≤ 1px（S5）。
**替身 / 配对**：引擎原始输出回放，配对 04a-10/11/12。**重构敏感点**：断言的是 `ocr_line` 输出和 schema。
**并发**：不适用（引擎调用之间没有共享状态，批次级并发见 04b）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 04a-01 | test_geometry_roundtrip | 边缘 | `unit` | hyp：图尺寸 × 框 × 三种坐标系 | `to_px(from_px(b))` | 误差 ≤ 1px | L1·S5 | |
| 04a-02 | test_vision_bottom_left_flip | 边界 | `unit` | 归一化坐标 y=0、y=1 的框 | `vision_to_px` | 分别落在图的底部和顶部 | L1·S2 | |
| 04a-03 | test_mlx_0_999_bounds | 边界 | `unit` | 坐标 0 和 999 | `mlx_to_px` | 分别是 0 和 ≤ 宽−1，不越界 | L1·S2 | |
| 04a-04 | test_engine_replay_conforms | 成功 | `unit` | `fx-labs-1x` 三个引擎的原始输出回放 | 转换 | 通过 `ocr_line` schema；bbox 是 4 个整数 | L1·S2 | |
| 04a-05 | test_mlx_tokens_null_block_bbox | 成功 | `unit` | MLX 回放 | 转换 | `tokens is None`；`block_bbox` 非空 | L1·S1 | |
| 04a-06 | test_approx_marked | 成功 | `unit` | observation 级的 ocrmac 回放 | 转换 | 每个 token 都是 `geometry == "approx"` | L1·S1 | |
| 04a-07 | test_degenerate_fallback_chain | 边缘 | `unit` | MLX 主、备 prompt 都退化（ratio 0.5），tesseract 正常 | `ocr_page` | 最终引擎是 tesseract；记了 `degenerate_output`；尝试顺序为主→备→tesseract | L1·S3 | |
| 04a-08 | test_degenerate_threshold_boundary | 边界 | `unit` | ratio 0.30、0.31 | `ocr_page` | 0.30 不触发兜底，0.31 触发 | L1·S2 | |
| 04a-09 | test_all_fallbacks_degenerate | 非法 | `unit` | 三者都退化 | `ocr_page` | 记页级 `route_failed` 进 uncertain；不输出空 JSONL 冒充成功 | L1·S3 | |
| 04a-10 | test_real_ocrmac_bounds | 成功 | `cli_integration` macos | 全部 fx 图 | 真 ocrmac | 每个 bbox 都在图像边界内 | L3·S4 | G4 |
| 04a-11 | test_real_tesseract_tsv_bounds | 成功 | `cli_integration` | 全部 fx 图 | 真 tesseract（`chi_sim+jpn`） | 同上；token 的 `geometry == exact` | L3·S4 | G4 |
| 04a-12 | test_real_mlx_smoke | 成功 | `cli_integration` macos_mlx | `fx-labs-1x` | 真 MLX | 输出通过 schema；权重缺失时 FAIL（不是 skip），由 doctor 报告 | L3·S1 | G4 |

### WI-04b：`run_batch` + 方向转正

**Law**：页守恒——receipt 里的页集合 == manifest × routes，且每个 (页, 路) 恰好有一个终态（S5）；redo 不改动旧 attempt（S5）；断点续跑不产生重复（S5）。
**替身 / 配对**：引擎回放 + 假 claude/codex，配对 04a-10、06-11/12。**重构敏感点**：断言的是目录树、receipt 和替身调用日志的条目数。
**并发**：04b-12。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 04b-01 | test_rotate_receipt_u2 | 成功 | `contract_replay` | `fx-rotated90` | `bin/klode-kit kb-ocr --batch …` | 每页 receipt 有 `rotate: 90`；`derived.jsonl` 的 `transform.rotate=90`，`parent` 等于原图 sha | L2·S1 | |
| 04b-02 | test_orient_all_four | 边界 | `unit` | tmp：把 `fx-labs-1x` 转成 0/90/180/270 四张 | `orient.detect` | 4 个方向都判对 | L1·S2 | |
| 04b-03 | test_run_dir_layout | 成功 | `contract_replay` | `fx-labs-1x` | kb-ocr | `ocr/<engine>/<cfg_fp>.t1/p001.jsonl` 存在 | L2·S1 | |
| 04b-04 | test_page_conservation | 边缘 | `contract_replay` | hyp：页子集 × 路子集 × 注入失败的位置 | kb-ocr | 每个 (页, 路) 恰好一个终态（done、route_failed 或 route_missing_page）；回放前后页标数相同（E68） | L2·S5 | |
| 04b-05 | test_resume_after_kill | 边缘 | `contract_replay` | `fx-u1-3labs` | 第 1 页完成后 SIGKILL，然后续跑 | 第 1 页没有被再次调用（替身日志里只有 1 条）；JSONL 没有重复行 | L2·S5 | |
| 04b-06 | test_redo_new_attempt_old_untouched | 边缘 | `contract_replay` | `fx-redo` | `kb-ocr --redo` | 出现 `t2` 目录；`t1` 的树 sha 不变 | L2·S5 | |
| 04b-07 | test_resume_reuses_only_three_hash_match | 边界 | `contract_replay` | 把 `t1` 的一个产物改 1 字节 | 普通 resume | 新建 `t2`，不复用 `t1` | L2·S3 | |
| 04b-08 | test_route_retry_then_failed | 非法 | `contract_replay` | 假 codex 每次都 exit 1 | kb-ocr | 调用次数 == 1 + `route_retry`；记 `route_failed` 并进入页级 uncertain | L1·S3 | |
| 04b-09 | test_missing_page_explicit | 非法 | `contract_replay` | `fx-redo`（codex 缺 p001） | kb-ocr | receipt 里有 `route_missing_page:codex:p001`；没有被静默丢掉 | L2·S3 | |
| 04b-10 | test_empty_inbox | 边界 | `unit` | tmp 空目录 | kb-ocr | **规格待定（§7-F4）**：期望 exit 2、消息「no images」，且不建任何目录 | L1·S3 | |
| 04b-11 | test_non_image_file_listed | 非法 | `unit` | tmp inbox 里放一个 `.txt` | kb-ocr | **规格待定（§7-F4）**：期望跳过该文件并在 receipt 的 `skipped[]` 里列出，不静默 | L1·S3 | |
| 04b-12 | test_concurrent_runs_same_batch_locked | 并发 | `contract_replay` | `fx-labs-1x` | 两个真进程同时 kb-ocr | 第二个 exit 40；第一个的产物完整，JSONL 行没有交错 | L2·S3 | |
| 04b-13 | test_atomic_page_write | 边缘 | `contract_replay` | `fx-labs-1x` | 在 `KLODE_KIT_CRASH_AT=run_batch.before_replace` 处 SIGKILL，然后续跑 | 崩溃后目标位置没有写了一半的 `.jsonl`；续跑后的结果与不崩溃时逐字节相同 | L2·S5 | |

### WI-04c：文字层 PDF 分流

**Law**：`page_map` 往返——`_whole.txt` 的任意一行经 `page_map` 都能回到原页的原文（S5）；`px = pt × dpi / 72`（S4）。
**替身 / 配对**：04c-05 里云端两路用回放，配对 06-11/12。**重构敏感点**：断言的是分类结果和产物文件。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 04c-01 | test_classify_boundaries | 边界 | `unit` | 49/50 个字符；乱码率 0.05、0.0501 | `classify_page` | 49 为 empty、50 为 text；0.05 为 text、0.0501 为 garbled | L1·S2 | |
| 04c-02 | test_bbox_pt_to_px | 边缘 | `unit` | hyp：pt 值 × dpi | 换算 | 等于 `pt*dpi/72`，按约定的取整规则 | L1·S4 | |
| 04c-03 | test_whole_pagemap_roundtrip | 边缘 | `unit` | hyp：多页文本 | `text_to_whole` 后经 `page_map` 反查 | 每一行都回到原页的原文 | L1·S5 | |
| 04c-04 | test_text_pdf_receipt_u4 | 成功 | `cli_integration` | `fx-text-pdf` | 用真 pdftotext 跑 kb-ocr | `text_layer: true`；`routes_skipped == [vision,mlx,claude,codex]` | L3·S1 | |
| 04c-05 | test_real_pdftotext_mixed | 成功 | `cli_integration` | `fx-mixed-pdf` | kb-ocr | 每页分类正确；empty 和 garbled 页有四路 JSONL，text 页没有 | L3·S1 | |
| 04c-06 | test_corrupt_pdf_no_partial | 非法 | `cli_integration` | 新增 `fx-corrupt-pdf`（截断字节） | kb-ocr | exit ≠ 0；receipt 记 `pdf_unreadable`；`text/` 下没有任何产物 | L2·S3 | |
| 04c-07 | test_zero_text_pages_all_routed | 边界 | `cli_integration` | `fx-mixed-pdf` 里只含扫描页的子集 | kb-ocr | `text_layer: false`；routes_skipped 为空 | L2·S2 | |

### WI-05a：抬头打码

**Law**：像素守恒——框内全为 0，框外与原图逐像素相同（S4）；MANIFEST 幂等（S5）。
**替身 / 配对**：Vision 行框回放，配对 05a-07。**重构敏感点**：断言的是像素数组和 MANIFEST 行。
**并发**：05a-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 05a-01 | test_mask_pixel_conservation | 边缘 | `unit` | hyp：在 fx 图上任意生成框 | `mask.apply` | 框内像素全为 0；框外与原图逐像素相同 | L2·S4 | |
| 05a-02 | test_manifest_row_fields | 成功 | `unit` | `fx-rx-stamp` | mask | MANIFEST 行含全部 5 个字段；`der_sha256 == sha(文件)`；`parent` 等于原图 sha | L2·S1 | |
| 05a-03 | test_manifest_idempotent | 边缘 | `unit` | 同上 | 跑两次 | MANIFEST 行数不变；`.m.png` 字节相同 | L2·S5 | |
| 05a-04 | test_mask_box_clipped_at_border | 边界 | `unit` | 一个一半在图外的框 | mask | 不抛异常；图内部分为 0；图像尺寸不变 | L1·S2 | |
| 05a-05 | test_no_keyword_header_region_masked | 边界 | `unit` | 合成页，没有关键词行 | mask | 按 `mask_rules` 把页顶区域打码；`mask_boxes` 非空 | L1·S2 | |
| 05a-06 | test_review_images_only_in_review_dir | 非法 | `unit` | `fx-rx-stamp` | mask | `_masked/` 下只有 `*.m.png` 和 MANIFEST；对照图只出现在 `_mask_review/` | L2·S3 | |
| 05a-07 | test_masked_text_absent | 成功 | `cli_integration` macos | `fx-u1-3labs` | 对打码图跑真 Vision | 识别结果里没有「测试患者甲」和「T-0000001」 | L3·S3 | G4 |
| 05a-08 | test_concurrent_mask_manifest | 并发 | `unit` | `fx-u1-3labs` | 两个进程同时给同一批打码 | MANIFEST 没有重复行，每行都是完整 JSON | L2·S5 | |

### WI-05b：发送器契约 + `privacy_guard`

**Law（K6）**：发起云端调用 ⟺ 发送那一刻文件字节的 sha ∈ `_masked/MANIFEST` ∪ fixtures MANIFEST；不满足时 exit 60 且零调用（S4）。
**替身 / 配对**：假 claude/codex 只记录收到的图的 sha 和临时目录的文件列表，配对 06-11/12、00c2-06/07。**重构敏感点**：断言的是退出码、假可执行文件调用日志的条目数和文件系统状态。
**并发**：05b-08（TOCTOU）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 05b-01 | test_listed_sha_sends | 成功 | `contract_replay` | `_masked` MANIFEST + 假 claude | `send(path, expect_sha)` | 调用日志 1 条，收到的图 sha 等于登记值；调用结束后临时目录已删除；receipt 里只有 der_sha，没有路径 | L2·S1 | |
| 05b-02 | test_unlisted_sha_exit60_no_spawn | 非法 | `contract_replay` | 未登记的 png | send | exit 60；调用日志 0 条 | L2·S3 | |
| 05b-03 | test_listed_path_tampered_bytes | 非法 | `contract_replay` | 路径已登记，但字节被改了 1 位 | send | exit 60；0 条（判据是 sha，不是路径） | L2·S3 | |
| 05b-04 | test_review_dir_rejected | 非法 | `contract_replay` | `_mask_review/` 下一个 sha 恰好在登记表里的副本 | send | exit 60；0 条 | L2·S3 | |
| 05b-05 | test_fixture_manifest_sha_accepted | 边界 | `contract_replay` | `tests/fixtures` 里的图 | send | 调用 1 条 | L2·S2 | |
| 05b-06 | test_invalid_isolation_receipt_exit61 | 非法 | `contract_replay` | `cli_version` 已过期的 receipt | send | exit 61；0 条 | L2·S3 | |
| 05b-07 | test_send_only_manifest_property | 边缘 | `contract_replay` | hyp：任意文件集 × 任意登记子集 × 任意篡改子集 | 逐个 send | 发生调用 ⟺ 当前字节的 sha ∈ 登记表；所有拒绝都是 exit 60，且该次 0 条调用 | L2·S4 | |
| 05b-08 | test_toctou_swap_during_send | 并发 | `contract_replay` | 已登记的图 + 一个在检查之后改写源文件的线程 | send | 要么 exit 60，要么假 claude 收到的图 sha ∈ 登记表；永远收不到未登记的字节 | L2·S3 | |
| 05b-09 | test_tmpdir_properties | 边界 | `contract_replay` | 假 claude 在被调用时记录 cwd 的 stat 和文件列表 | send | cwd 在系统 TMPDIR 下且不在数据项目内；权限 0700；恰好 3 个文件 | L2·S2 | |
| 05b-10 | test_tmpdir_cleanup_on_exception | 边缘 | `contract_replay` | 假 claude 异常退出；以及发送过程中 KeyboardInterrupt | send | 两种情况下临时目录都不存在 | L2·S3 | |
| 05b-11 | test_receipt_no_path_leak | 边缘 | `unit` | hyp：路径（含中文和空格） | 生成适配器 receipt | receipt JSON 里不含路径的任何组成部分 | L1·S4 | |
| 05b-12 | test_hook_subprocess_unlisted_exit2 | 非法 | `contract_replay` | 真 hook 进程，stdin 为 `codex exec -i <未登记的图>` | 运行 `privacy_guard` | exit 2 | L3·S3 | |
| 05b-13 | test_hook_listed_allows | 成功 | `contract_replay` | 同上，但图已登记 | 同上 | exit 0 | L3·S1 | |
| 05b-14 | test_hook_absent_sender_still_enforces | 边缘 | `contract_replay` | 不加载 hooks.json | send 未登记的图 | exit 60；0 条 | L2·S3 | |

### WI-06a / WI-06b：Claude / Codex 读图适配器

**Law**：预算台账——任意调用费用序列下，累计花费 ≤ `max_cost_usd_per_batch` 加上预留规则允许的余量；超出后不再发送（S4）。
**替身 / 配对**：假 claude/codex 回放，配对 06-11/12（model，G2）。**重构敏感点**：断言的是 JSONL、receipt 和退出码。
**并发**：06-10（两路共享台账）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 06-01 | test_replay_conforms[claude] | 成功 | `contract_replay` | `replay/claude/*` | 适配器 | 通过 schema；`bbox_px` 和 `tokens` 都是 null | L1·S2 | |
| 06-02 | test_replay_conforms[codex] | 成功 | `contract_replay` | `replay/codex/*` | 适配器 | 同上 | L1·S2 | |
| 06-03 | test_timeout_route_failed | 非法 | `contract_replay` | 假可执行文件 sleep 超过 `cloud.timeout_s`（注入为 1s） | 适配器（两路参数化） | 记 `route_failed`；页级 uncertain | L1·S3 | |
| 06-04 | test_schema_invalid_retry_once_then_parse_error | 边缘 | `contract_replay` | 两次都畸形 | 适配器 | 调用 2 次；记 `parse_error` | L1·S3 | |
| 06-05 | test_schema_invalid_then_valid | 边缘 | `contract_replay` | 第 1 次畸形，第 2 次正常 | 适配器 | 调用 2 次；输出合法 JSONL | L1·S2 | |
| 06-06 | test_budget_stop_exit50 | 边缘 | `contract_replay` | `max_cost=0.10`，每次 0.04 | 批量发 5 张 | 第 3 次后累计超限，exit 50；总共只调用 3 次 | L2·S3 | |
| 06-07 | test_budget_boundary_exact | 边界 | `contract_replay` | 累计恰好等于 0.10 | 发送 | 不触发 exit 50（规则是「超过」） | L2·S2 | |
| 06-08 | test_codex_argv_effort_and_output | 成功 | `unit` | 锁定的 effort | 构造 codex argv | 含 `-o out.json --json -c model_reasoning_effort=<锁定值>` | L1·S1 | |
| 06-09 | test_model_actual_from_response | 成功 | `contract_replay` | 回放里的 model 字段与配置不同 | 适配器 | receipt 的 `model_actual` 取自响应，不是配置值 | L1·S1 | |
| 06-10 | test_parallel_routes_budget_shared | 并发 | `contract_replay` | hyp：两路并发 × 每次的费用 | 并发发送 | 累计花费 ≤ 上限 + 一次预留余量；超限后两路都不再发起新调用 | L2·S4 | |
| 06-11 | test_live_vs_replay_shape[claude] | 成功 | `model_integration` | `fx-labs-1x` 打码图 | 真实调用 | 实跑输出的键和类型与回放相同 | L3·S1 | G2 |
| 06-12 | test_live_vs_replay_shape[codex] | 成功 | `model_integration` | 同上 | 同上 | 同上 | L3·S1 | G2 |

### WI-07a：字段抽取

**Law**：每个抽出的字段都满足 `text[s:e] == value`（S4）；全角数字与半角数字等价（S6）。
**替身 / 配对**：无。**重构敏感点**：断言的是返回的字段列表。
**并发**：不适用（纯函数）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 07a-01 | test_span_property | 边缘 | `unit` | hyp：由数字、单位、日期、药名拼成的行 | `extract(line)` | 每个字段都有 `line[s:e] == value` | L1·S4 | |
| 07a-02 | test_fullwidth_metamorphic | 边缘 | `unit` | hyp：一行文本及其全角版本 | extract | 两者的值序列在 NFKC 后相同 | L1·S6 | |
| 07a-03 | test_10_5_vs_1_05_distinct | 非法 | `unit` | 「10.5」「1.05」 | extract | 两个值不相等 | L1·S3 | |
| 07a-04 | test_recognize_number_forms | 成功 | `unit` | `1.05`、`3.5-5.5`、`4.2×10^9/L`、`<0.1` | extract | 每一项的类型和值都对 | L1·S2 | |
| 07a-05 | test_dates_three_forms | 成功 | `unit` | `2026-09-18`、`2026/9/18`、`2026年9月18日` | extract | 都归一为 2026-09-18，span 指向原文 | L1·S2 | |
| 07a-06 | test_flags_boundary | 边界 | `unit` | H、L、↑、↓、*；「HGB」 | extract | 前 5 个都是 flag；HGB 里的 H 不算 flag | L1·S2 | |
| 07a-07 | test_drug_dose | 成功 | `unit` | `示例片 10mg` | extract | drug=示例片，dose=10mg | L1·S1 | |
| 07a-08 | test_empty_and_noise_line | 边界 | `unit` | `""`、`-----` | extract | 返回 `[]` | L1·S2 | |

### WI-07b：单元格对齐 + `items.yaml`

**Law**：对齐得到的下标对严格递增（S4）；非基准路的排列顺序不影响结果（S6）。
**替身 / 配对**：引擎回放，配对 04a-10/11。**重构敏感点**：断言的是 cell 集合和 uncertain 列表。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 07b-01 | test_base_route_first_exact | 成功 | `unit` | vision 只有 approx，tesseract 有 exact | `align` | 基准路为 tesseract | L1·S2 | |
| 07b-02 | test_row_merge_overlap_boundary | 边界 | `unit` | 两个框竖直重叠 49% 和 50% | align | 49% 分成两行，50% 合并为一行 | L1·S2 | |
| 07b-03 | test_col_cluster_by_x0 | 成功 | `unit` | `fx-labs-1x` 的 vision 回放 | align | 列 id 与真值的列一致 | L1·S1 | |
| 07b-04 | test_approx_without_exact_no_geometry | 非法 | `unit` | 只有 approx 几何的行 | align | 该行每个 cell 都是 `no_geometry` | L1·S3 | |
| 07b-05 | test_approx_with_exact_corroboration_used | 边界 | `unit` | 同一行另有 tesseract 的 exact 框 | align | 采用 approx，不记 `no_geometry` | L1·S2 | |
| 07b-06 | test_alignment_monotonic | 边缘 | `unit` | hyp：基准行序列 + 云端路行（含增删、打乱） | align | 对齐下标对严格递增 | L1·S4 | |
| 07b-07 | test_unaligned_and_merged_rows | 非法 | `unit` | `fx-missingrow`；某一行等于基准两行拼接 | align | 分别记 `unaligned:<engine>` 和 `merged_rows:<engine>` | L1·S3 | |
| 07b-08 | test_duplicate_values_keep_ordinal | 边界 | `unit` | `fx-dup10` | align | 两个 cell 的 ordinal 分别为 0、1，没有合并 | L1·S2 | |
| 07b-09 | test_nonbase_engine_permutation_invariant | 边缘 | `unit` | hyp：非基准路的任意排列 | align | cell 集合相同 | L1·S6 | |
| 07b-10 | test_items_alias_normalize_and_new_items | 成功 | `unit` | `HGB`；未知项目「某某指数」 | items 加载器 | HGB 归一为 hemoglobin；未知项目用 slug，并出现在 `new_items[]` | L1·S2 | |
| 07b-11 | test_items_duplicate_alias_rejected | 非法 | `unit` | tmp `items.yaml`：同一个别名挂在两个项目下 | 加载 | `ConfigError`，退出码 40 | L1·S3 | |

### WI-07c：票型比对 + 白名单

**Law（K1）**：`agree` ⟺ 所有配置的路都在场、值全部相等、该行没有行级 uncertain（S4）；推荐值 ∈ 候选值（S4）；结果与路的顺序无关（S6）。
**替身 / 配对**：无。**重构敏感点**：断言的是每个 cell 的票型。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 07c-01 | test_dup10_two_cells_agree | 边界 | `contract_replay` | `tests/contract/fields/dup10` | `compare` | 两个 cell 都是 4/4 agree，没有合并 | L1·S3 | |
| 07c-02 | test_colshift_disputed_with_item_label | 非法 | `contract_replay` | `fields/colshift` | compare | 该 cell 为分歧并带 `item_label` | L1·S3 | |
| 07c-03 | test_swap_suspect_never_agree | 非法 | `contract_replay` | `fields/swap` | compare | 两个 cell 都是分歧并标 `swap_suspect`，都不是 agree | L1·S3 | |
| 07c-04 | test_missingrow_missing_cell_to_confirm | 边缘 | `contract_replay` | `fields/missingrow` | compare | 该行所有 cell 都是 `missing_cell:<engine>`；即使另外三路一致也不是 agree | L1·S3 | |
| 07c-05 | test_auto_agree_iff_all_present_equal | 边缘 | `unit` | hyp：随机四路网格（含 None 格和行级 uncertain） | compare | agree ⟺ 四路齐、值全相等、没有行级 uncertain（双向） | L1·S4 | |
| 07c-06 | test_vote_engine_permutation | 边缘 | `unit` | hyp：路的顺序的任意排列 | compare | 票型相同 | L1·S6 | |
| 07c-07 | test_recommended_in_candidates | 边缘 | `unit` | hyp | compare | 推荐值 ∈ 候选值，且是多数值 | L1·S4 | |
| 07c-08 | test_tie_no_majority_recommendation | 边界 | `unit` | 2:2 | compare | 标 `tie`，不给多数推荐 | L1·S2 | |
| 07c-09 | test_three_vs_one_not_agree | 边界 | `unit` | 3:1 | compare | 是分歧，推荐值为多数值，不是 agree | L1·S2 | |
| 07c-10 | test_whitelist_not_applied_to_numeric | 非法 | `unit` | 白名单条目恰好能匹配一个数值差异 | compare | 仍然是分歧 | L1·S3 | |
| 07c-11 | test_whitelist_applied_to_text | 成功 | `unit` | 单位文本上已知的引擎弱点 | compare | 被忽略，cell 判 agree | L1·S1 | |
| 07c-12 | test_route_disabled_never_agree | 边缘 | `unit` | `routes_enabled.codex=false` | compare | 所有 cell 都是 `missing_cell:codex`，没有任何 agree | L1·S3 | |

### WI-07d：裁图

**Law**：变换链往返——rotate × crop × mask 回映到原图，误差 ≤ 1px（S5）；裁图区域始终在图内（S4）。
**替身 / 配对**：无。**重构敏感点**：断言的是像素框和图像尺寸。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 07d-01 | test_transform_roundtrip | 边缘 | `unit` | hyp：rotate ∈ {0,90,180,270} × 随机 crop 偏移 | map 再 unmap | 误差 ≤ 1px | L1·S5 | |
| 07d-02 | test_crop_within_bounds | 边缘 | `unit` | hyp：行号（含首行和末行）× 图尺寸 | `crop_for_cell` | 裁图框 ⊆ 图像范围 | L1·S4 | |
| 07d-03 | test_crop_rows_plus_minus_one | 成功 | `unit` | `fx-labs-1x` 第 5 行 | crop | 覆盖第 4–6 行 | L1·S1 | |
| 07d-04 | test_first_last_row_boundary | 边界 | `unit` | 第 0 行和最后一行 | crop | 不出现负坐标，也不越界 | L1·S2 | |
| 07d-05 | test_no_bbox_fallback | 非法 | `unit` | 只有云端路的 cell | crop | 退回整行或半页，并记 `no_bbox_crop` | L1·S3 | |
| 07d-06 | test_crop_from_masked_derivative | 边缘 | `unit` | 打码后的衍生图 | crop | mask 区域内的像素全为 0（裁图从打码图取，不从原图取） | L2·S3 | |

### WI-08a：审计阶段、`audit_rev`、uncertain

**Law（K2）**：候选为 verified ⇒ `uncertain == []`、所有 cell agree、四路齐（S4）；`uncertain_log` 只追加，任何时刻前缀字节都不变（S5）。
**替身 / 配对**：OCR 回放，配对 06-11/12。**重构敏感点**：断言的是目录、文件字节和候选状态。
**并发**：08a-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 08a-01 | test_audit_rev_new_on_run_change | 成功 | `contract_replay` | `fx-redo`：a1 之后出现了新 run | `kb-audit` | 新建 `a2`，`a2/runs.json` 指向新 run；`a1` 的树 sha 不变 | L2·S1 | |
| 08a-02 | test_audit_rev_reuse_when_same | 边缘 | `contract_replay` | run 集合不变 | 连续两次 kb-audit | 仍然是 `a1`，没有新目录 | L2·S5 | |
| 08a-03 | test_write_once | 非法 | `contract_replay` | 已有 `a1/cells.jsonl` | 再写一次 | 抛 `WriteOnceViolation`；文件字节不变 | L2·S3 | |
| 08a-04 | test_uncertain_log_append_only | 边缘 | `contract_replay` | hyp：添加 / 解除 uncertain 的操作序列 | 逐步执行 | 每一步之后，之前的前缀字节都不变；解除操作写入 `cleared_by/at/evidence`，不删旧行 | L2·S5 | |
| 08a-05 | test_page_level_to_candidate_disputed | 非法 | `unit` | 页上有 `route_missing_page` | gate | 该页所有记录的候选都是 disputed | L1·S3 | |
| 08a-06 | test_uncertain_entry_fields_per_reason | 边界 | `unit` | r2.3 原因表里的每个原因 | 生成条目 | 都含 `reason/scope/fix_action/clear_evidence`；scope 与原因表一致 | L1·S2 | |
| 08a-07 | test_candidate_verified_implies_clean | 边缘 | `unit` | hyp：网格 + uncertain 列表 | gate | 候选为 verified ⇒ `uncertain==[]`、所有 cell agree、四路齐 | L1·S4 | |
| 08a-08 | test_concurrent_audit_same_batch | 并发 | `contract_replay` | `fx-u1-3labs` | 两个进程同时 kb-audit | 一个 exit 40；只产生一个 `a<N>` | L2·S3 | |

### WI-08b：确认页 HTML + `choices` 导出

**Law**：No law：渲染检查，用 S2/S3 覆盖结构和转义。导出 JS 对 `confirm_sha256` 的绑定由 08b-06 覆盖。
**替身 / 配对**：无（08b-06 用真 node）。**重构敏感点**：用 `html.parser` 按语义查元素（`data-field-id`、`role`），不依赖 CSS 类名。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 08b-01 | test_item_structure_u8 | 成功 | `unit` | `fx-u8` | 渲染 confirm.html | 每条分歧都有 img、4 个按路标注的候选、★推荐值，以及「都不对」「手输」 | L1·S2 | |
| 08b-02 | test_missing_route_not_clickable | 非法 | `unit` | `fx-redo` 的 a2 | 渲染 | 文本含「缺路，不可点选」和修复命令；该条没有可选的 input | L1·S3 | |
| 08b-03 | test_effective_vs_candidate_label | 成功 | `unit` | `fx-redo` 的 a2 | 渲染 | 文本含「生效 r1 (verified) / 候选 r2 (disputed: codex:route_missing_page)」 | L1·S1 | |
| 08b-04 | test_advice_display_only | 边缘 | `unit` | 有 `advice.json` | 渲染后直接导出（不点选） | 提示可见；导出的 items 里没有 advice 的值 | L1·S3 | |
| 08b-05 | test_no_external_urls | 非法 | `unit` | 全部 fx | 渲染 | 没有指向 `http(s)://` 的 src、href 或 url() | L1·S3 | |
| 08b-06 | test_export_js_node | 成功 | `cli_integration` needs_node | `fx-u8` + 模拟点选 | 用 node 执行导出脚本 | `choices.json` 通过 schema；`confirm_sha256` 等于 html 的 sha；`fingerprints` 齐全 | L3·S1 | |
| 08b-07 | test_zero_disputes_page | 边界 | `unit` | `fx-u3-mixed` 中无分歧的页 | 渲染并导出 | 显示「无分歧」；导出的 `items == []` | L1·S2 | |
| 08b-08 | test_ocr_text_escaped | 非法 | `unit` | OCR 文本里含 `<script>` | 渲染 | 被转义；DOM 中没有 script 节点 | L1·S3 | |
| 08b-09 | test_tie_shows_tie_label | 边界 | `unit` | `fx-u8` 的 `p002-l07` | 渲染 | 标「平票」，没有 ★ | L1·S2 | |

### WI-08c：写回（唯一提交者）

**Law（K2、K3）**：
- effective.rev 单调不降，并且只有候选为 verified 时才推进（S5）；
- 失败的候选不动 effective 的字节（S5）；
- 重复提交是幂等的（S5）；
- fixes 只追加（S5）；
- 在任何写点崩溃后，结果都是要么全部提交、要么全部回滚（S5）；
- 其他阶段从不改写 status（S6）。

**替身 / 配对**：无（纯文件系统）。**重构敏感点**：断言的是 `_by_id`、`_rev`、`fixes.jsonl` 的字节和退出码。
**并发**：08c-14。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 08c-01 | test_human_choice_commits_verified | 成功 | `contract_replay` | `fx-u8` 的合法 choices | `kb-audit --writeback` | effective.rev 加 1；`verified_by: human`；fixes 新增的行共用同一个 `commit_id` | L2·S1 | |
| 08c-02 | test_stale_choices_exit30 | 非法 | `contract_replay` | 参数化：batch_id、fingerprints、rev_base、seen 各错一个 | writeback | exit 30；数据项目的树 sha 不变 | L2·S3 | |
| 08c-03 | test_manual_empty_is_none | 非法 | `contract_replay` | manual 的 value 为 `""` | writeback | 候选为 disputed；effective 不变 | L2·S3 | |
| 08c-04 | test_none_not_a_field_dropped | 边界 | `contract_replay` | `none` + `not_a_field` | writeback 后导出 | 字段标 `dropped`；`records_to_txt` 的输出里没有它 | L2·S2 | |
| 08c-05 | test_record_atomic_multi_field | 边缘 | `contract_replay` | 记录 A 有 3 个字段，其中 1 个不合格；记录 B 全部合格 | writeback | A 零写入，exit 30 并列出出错的 field_id；B 已提交；`per_record[]` 里两条的结果都正确 | L2·S3 | |
| 08c-06 | test_idempotent_resubmit | 边缘 | `contract_replay` | 同一份 choices | 提交两次 | 第二次没有产生新 rev；fixes 里没有重复的 decision | L2·S5 | |
| 08c-07 | test_fixes_append_only | 边缘 | `contract_replay` | 多次提交 | 依次提交 | 每次提交后，之前的前缀字节都不变 | L2·S5 | |
| 08c-08 | test_effective_rev_monotonic | 边缘 | `contract_replay` | hyp：随机提交序列（陈旧、合法、none、重复、旧候选） | 逐个提交 | effective.rev 单调不降；只在候选为 verified 时变化；旧候选永远覆盖不了更新的 rev | L2·S5 | |
| 08c-09 | test_failed_candidate_never_moves_effective | 边缘 | `contract_replay` | hyp：已有 verified r1，随后产生 disputed 或 pending 候选 | writeback | effective 段的字节与 r1 时相同；`_rev/X.r1.md` 的 sha 不变 | L2·S5 | |
| 08c-10 | test_auto_verified_iff_all_agree_no_uncertain | 边缘 | `contract_replay` | hyp：四路网格，依次经过 compare → gate → writeback | 全链 | 候选为 verified(auto) ⟺ 四路齐、全部 agree、`uncertain==[]` | L2·S4 | |
| 08c-11 | test_choice_on_page_level_uncertain_rejected | 非法 | `contract_replay` | 针对有 `route_missing_page` 的记录伪造 choices | writeback | exit 30；该记录保持 disputed，从未变成 verified | L2·S3 | |
| 08c-12 | test_rev_files_readonly | 非法 | `contract_replay` | 提交之后 | 以写模式打开 `_rev/X.r1.md` | `PermissionError`；字节不变 | L2·S3 | |
| 08c-13 | test_crash_injection_consistency | 边缘 | `contract_replay` | 枚举所有写点 `KLODE_KIT_CRASH_AT=…`，再用 hyp 加随机 SIGKILL | 崩溃后重新运行 writeback | 结果要么全部提交、要么全部回滚；`status == effective.status`；有 commit_id 就有对应的提交标记 | L2·S5 | |
| 08c-14 | test_concurrent_writeback_same_choices | 并发 | `contract_replay` | `fx-u8` | 两个进程同时提交同一份 choices | 恰好产生一个新 rev；另一个进程 exit 40 或者是幂等的 no-op | L2·S5 | |
| 08c-15 | test_same_field_different_choice_new_rev | 边界 | `contract_replay` | 第二份 choices 换了值，`rev_base` 等于当前 rev | writeback | 产生新 rev N+1，值为新值 | L2·S2 | |
| 08c-16 | test_writeback_sole_writer_of_status | 边缘 | `contract_replay` | `fx-u8` 写回之后 | 依次运行视图重建、build、kb-verifier 回放、确认页渲染、kb-check | `records/_by_id/**` 的字节前后不变（metamorphic） | L2·S6 | |

### WI-08d：兼容视图 + U6/U8 行为

**Law**：视图重建是幂等的，而且与记录写入顺序无关（S5、S6）；视图只指向 effective（R4-01 例外：没有 effective 时指向最新候选）。
**替身 / 配对**：OCR 回放（fx-redo），配对 06-11/12。**重构敏感点**：断言的是符号链接目标、sha 和文件是否存在。
**并发**：08d-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 08d-01 | test_view_symlink_points_effective | 成功 | `contract_replay` | `fx-u8` 写回之后 | 重建视图 | `records/2026-10-22-lab.md` 指向 `_rev/X.r<eff>.md`；`verified/` 下有对应链接 | L2·S1 | |
| 08d-02 | test_same_day_suffix | 边界 | `contract_replay` | `fx-u1-3labs`（同日 3 张 lab） | 重建 | 依次为 `…-lab.md`、`-2`、`-3`，按 `record_idx` 排序 | L2·S2 | |
| 08d-03 | test_first_ingest_pending_view_r4_01 | 边界 | `contract_replay` | `fx-u3-mixed` 第一段之后 | 重建 | records 视图指向最新候选，状态为 pending_confirm；`verified/` 下没有它 | L2·S2 | |
| 08d-04 | test_view_rebuild_idempotent | 边缘 | `contract_replay` | 任意状态 | 重建两次；或删光视图后重建 | 链接集合（名字和目标）相同 | L2·S5 | |
| 08d-05 | test_disputed_not_in_verified | 非法 | `contract_replay` | `fx-u8` 里选了「都不对」的记录 | 重建 | `verified/` 下没有它 | L2·S3 | |
| 08d-06 | test_u6_redo_keeps_verified | 边缘 | `contract_replay` | `fx-redo` | 按 r4.1 的 ①–④ 和续段执行 | 四条断言和续段全部成立；`_rev/X.r1.md` 的 sha 全程不变 | L2·S5 | |
| 08d-07 | test_view_rebuild_order_independent | 边缘 | `contract_replay` | hyp：记录写入顺序 | 重建 | 链接名和目标相同 | L2·S6 | |
| 08d-08 | test_concurrent_view_rebuild | 并发 | `contract_replay` | `fx-u1-3labs` | 两个重建进程并发 | 最终链接集合等于单次重建的结果；没有悬空链接 | L2·S3 | |

---

## 4. M2 · build / check / 发布 / MCP

### WI-01a：`records_to_txt` + `export_map`

**Law**：行的唯一性由构造保证（S4）；输出与 `cells.jsonl` 无关（S6）；export_map 的来源链成立（S5）；重跑结果相同（S5）。
**替身 / 配对**：无（01a-10 用真 klode）。**重构敏感点**：断言的是导出文本和 sha。
**并发**：不适用（运行在 pipeline 的锁之内，锁的测试见 09a-03）。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 01a-01 | test_line_format | 成功 | `unit` | `fx-labs-1x`（effective） | `records_to_txt` | 行与 `<item> <value_audited> <date> [flag] ⟨rec_id⟩` 精确相等 | L1·S1 | |
| 01a-02 | test_only_verified_exported | 非法 | `unit` | verified、disputed、pending 混在一起 | 导出 | 只出现 verified 的 rec_id | L1·S3 | |
| 01a-03 | test_values_from_binding_rev | 边缘 | `unit` | effective r1 的值是 A，候选 r2 的值是 B | 导出 | 行里是 A | L1·S3 | |
| 01a-04 | test_cells_independence | 边缘 | `contract_replay` | `fx-u8` | 删掉或打乱 `cells.jsonl` 后导出 | 输出 sha 不变 | L2·S6 | |
| 01a-05 | test_lines_unique_by_construction | 边缘 | `unit` | hyp：观测（含同日、同项目、同值） | 导出 | 任意两行都不相同 | L1·S4 | |
| 01a-06 | test_fx_labs_3x_three_distinct | 边界 | `unit` | `fx-labs-3x` | 导出 | 3 行互不相同 | L1·S2 | |
| 01a-07 | test_export_map_provenance | 边缘 | `contract_replay` | `fx-chain` 回放全链 | 导出后用 00b3 校验器检查 | 0 违例；导出行与 map 行一一对应 | L1·S5 | |
| 01a-08 | test_empty_record_rejected | 非法 | `unit` | 没有字段的记录 | 导出 | 进入 `excluded`，原因 `empty_record`；不输出任何行 | L1·S3 | |
| 01a-09 | test_two_shelves | 成功 | `unit` | `fx-labs-3x` | 导出 | `library/records/<rec_id>.txt` 和 `library/items/<slug>.txt` 都存在，内容各归各架 | L1·S2 | |
| 01a-10 | test_u5_after_manual_fix | 边缘 | `cli_integration` | `fx-manualfix` | 真 `klode verify <card> "示例片 1.05 2026-09-18"`；再用 10.5 | 前者 VERIFIED，后者 NOT FOUND | L3·S3 | |
| 01a-11 | test_export_deterministic_rerun | 边缘 | `unit` | 任意 fx | 导出两次 | 字节相同 | L2·S5 | |

### WI-01b：`records_to_l1` + `items_index`

**Law**：锚点数 == 观测行数，且锚点与行一一对应（S4）。
**替身 / 配对**：无（01b-07 用真 klode）。**重构敏感点**：断言的是卡片内容和 check 结果。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 01b-01 | test_front_matter_kind_deterministic | 成功 | `unit` | `fx-labs-1x` | 生成卡 | front-matter 里有 `kind: deterministic` | L1·S1 | |
| 01b-02 | test_coverage_equals_rows | 边缘 | `unit` | hyp：观测序列 | 生成卡 | 锚点数 == 行数；每个锚点恰好命中一行；没有重复 | L1·S4 | |
| 01b-03 | test_thin_latest_and_trend | 成功 | `unit` | `fx-labs-3x` | 生成卡 | Thin 段显示最新日期的值，趋势方向正确 | L1·S1 | |
| 01b-04 | test_single_observation | 边界 | `unit` | `fx-labs-1x` | 生成后 `check_cards` | 1 个锚点，PASS | L1·S2 | |
| 01b-05 | test_missing_anchor_fails_check | 非法 | `unit` | `fx-labs-3x` 去掉 1 个锚点 | check_cards | FAIL，报覆盖率 2/3 | L1·S3 | |
| 01b-06 | test_records_shelf_cards_generated | 成功 | `unit` | `fx-labs-3x` | 生成 | 每个 records 源都有同名的 deterministic 卡 | L1·S1 | |
| 01b-07 | test_fx_labs_3x_real_klode | 成功 | `cli_integration` | `fx-labs-3x` | 真 klode：verify 三次 + `zoom full` | 三条都 VERIFIED；zoom full 里三条齐全 | L3·S1 | |

### WI-01c：`deident` 两档

**Law**：pseudonym 稳定，同样的输入得到同样的输出（S5）；dateshift 保持日期的先后和间隔（S5）；全库只用一个偏移（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是输出文本和密钥文件的位置。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 01c-01 | test_pseudonym_stable_and_injective | 边缘 | `unit` | hyp：一组姓名 | 脱敏两次 | 同一个输入得到同一个假名；在样本范围内不同的姓名得到不同的假名 | L1·S5 | |
| 01c-02 | test_pseudonym_dates_untouched | 成功 | `unit` | `fx-labs-3x` | pseudonym 模式 | 日期字节不变 | L1·S1 | |
| 01c-03 | test_dateshift_preserves_order_and_intervals | 边缘 | `unit` | hyp：日期列表 | dateshift | 先后顺序不变，相邻间隔不变 | L1·S5 | |
| 01c-04 | test_dateshift_single_offset_across_views | 边缘 | `unit` | `fx-labs-3x` | 导出 txt、L1、records 三种视图 | 三者用的偏移相同 | L1·S5 | |
| 01c-05 | test_key_only_in_private | 非法 | `unit` | tmp 项目 | dateshift | 密钥只存在于 `_private/deident_key.json`；其他产物里找不到密钥字节 | L2·S3 | |
| 01c-06 | test_pii_zero_hits | 非法 | `unit` | 全部 fx | 脱敏后跑 privacy 正则 | 0 命中 | L1·S3 | |
| 01c-07 | test_salt_change_changes_pseudonym | 边界 | `unit` | 两个不同的盐 | pseudonym | 假名不同 | L1·S2 | |
| 01c-08 | test_dateshift_calendar_boundaries | 边界 | `unit` | 2026-12-31、2028-02-29 | dateshift | 结果都是合法日期，间隔保持不变 | L1·S2 | |

### WI-02a：vendoring 第一批

**Law**：切章后拼回 == 原文（S5）；B5 重复 apply 幂等（S5）；修复后的变体集合 ⊇ 修复前的（S6）。
**替身 / 配对**：无（02a-08 用真 klode）。**重构敏感点**：断言的是文件内容和 check 结果。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 02a-01 | test_split_concat_equals_original | 边缘 | `unit` | hyp：带章头的文本 | `split_by_manifest` | 拼回后与原文逐字节相同 | L1·S5 | |
| 02a-02 | test_cn_heads | 成功 | `unit` | 「第一章」「一、」「检验报告」 | HEAD_RE | 三者都能识别 | L1·S2 | |
| 02a-03 | test_head_re_from_config | 边界 | `unit` | tmp `head_re.toml` 里的自定义正则 | 切章 | 按自定义正则切 | L1·S2 | |
| 02a-04 | test_b5_reapply_no_dup | 边缘 | `unit` | `fx-book-aurora` 的 tmp 拷贝 | 连续两次 `--apply` | BIBLIOGRAPHY 只出现一次（先确认红） | L2·S5 | |
| 02a-05 | test_b1_longest_match | 非法 | `unit` | `"reset -- this"` | `fix_anchors` 生成变体 | 变体集合包含 `"reset — this"`（先确认红） | L1·S3 | |
| 02a-06 | test_fix_anchors_variants_superset | 边缘 | `unit` | hyp：锚点文本 | 新旧两版分别生成变体 | 新集合 ⊇ 旧集合 | L1·S6 | |
| 02a-07 | test_policy_from_source_not_kind | 非法 | `unit` | 源在 `library/items/` 下，但卡上自填 `kind: authored` | `check_cards --policy` | 仍按 deterministic 规则判定 | L1·S3 | |
| 02a-08 | test_check_cards_json_output | 成功 | `cli_integration` | `fx-book-aurora` 的卡 | `check_cards --json` | 输出通过 schema；判定结果与非 json 模式一致 | L3·S1 | |
| 02a-09 | test_no_hardcoded_paths | 非法 | `unit` | `src/`、`prompts/` | 扫描 | 不含 `/Users/`、`003`、`189` 这类私有路径 | L1·S3 | |

### WI-02b：`write_card` 门禁 + `check_published`

**Law**：写卡是幂等的（S5）。门禁失败时零写入，用 S3 覆盖。
**替身 / 配对**：无。**重构敏感点**：断言的是 `library/cards` 的字节、receipt 和退出码。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 02b-01 | test_b2_fail_no_write | 非法 | `unit` | 一张过不了门禁 1 的卡 | `write_card` | exit 1；`library/cards` 的字节不变（先确认红） | L2·S3 | |
| 02b-02 | test_pass_writes_card | 成功 | `unit` | 合格的卡 | write_card | 标记线以下的内容等于卡正文 | L2·S1 | |
| 02b-03 | test_forced_receipt_fails | 非法 | `unit` | 带 `--force` | write_card | receipt 记 `forced=true`，步骤 status=failed | L2·S3 | |
| 02b-04 | test_published_folded_detected | 非法 | `cli_integration` | 已发布的卡里有 `--` 折叠锚点 | `check_published` | FAIL，并点出该锚点 | L3·S3 | |
| 02b-05 | test_stale_receipt_detected | 边缘 | `unit` | receipt 生成后卡又被改过 | check_published | 报 stale | L2·S3 | |
| 02b-06 | test_write_card_idempotent | 边缘 | `unit` | 合格的卡 | 写两次 | 字节相同 | L2·S5 | |

### WI-02c：书桌

**Law**：`cue_suggest` 只读调优集——对留出集做任何改动，它的输出都不变（S6）。
**替身 / 配对**：无（02c-01 用真 klode）。**重构敏感点**：断言的是分数、退出码和输出文件。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 02c-01 | test_b3_json_parse | 非法 | `cli_integration` | 维度名为 `safety` | 用真 `klode --json diagnose` 判定 | 判定正确 | L3·S3 | |
| 02c-02 | test_b4_cn_action_head | 非法 | `unit` | 纯中文的加粗动作头 | `check_synthesis` | 报 S4 | L1·S3 | |
| 02c-03 | test_tune_pass_holdout_fail | 非法 | `unit` | 调优集 5/5，留出集 3/5 | `desk_build --verify` | FAIL | L1·S3 | |
| 02c-04 | test_holdout_threshold_boundary | 边界 | `unit` | 4/5；3/5；只有 4 题且全对 | verify | 依次为 PASS、FAIL、FAIL（题数不足 5） | L1·S2 | |
| 02c-05 | test_holdout_frozen_sha | 非法 | `unit` | 改了留出集但没有 refreeze；改了并带 `--refreeze --reason` | verify | 前者报错；后者通过，receipt 记下 reason | L2·S3 | |
| 02c-06 | test_cue_suggest_ignores_holdout | 边缘 | `unit` | hyp：对留出集的任意改动 | `cue_suggest` | 输出不变 | L1·S6 | |
| 02c-07 | test_separate_scores_printed | 成功 | `unit` | `fx-book-aurora` | verify | stdout 里调优集和留出集各占一行成绩 | L1·S1 | |

### WI-02d：`gen_agent` + `smoke_mcp`

**Law**：No law：模板渲染，用 S1–S3 覆盖。
**替身 / 配对**：无（02d-06 用真 klode-mcp）。**重构敏感点**：断言的是生成文件的文本。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 02d-01 | test_first_line_frontmatter | 非法 | `unit` | 医疗模板 | gen_agent | 第 1 行是 `---`；版本注释在 frontmatter 闭合之后 | L1·S3 | |
| 02d-02 | test_zh_template_no_english_claim | 非法 | `unit` | 医疗模板 | gen_agent | 不出现「原文是英文」 | L1·S3 | |
| 02d-03 | test_params_rendered | 成功 | `unit` | lang_source、lang_answer、source_kind、cite_format | gen_agent | 每个参数都出现在该出现的位置 | L1·S2 | |
| 02d-04 | test_example_from_desk_tests | 成功 | `unit` | `desk.json.tests` | gen_agent | example 等于 tests 的第一项 | L1·S1 | |
| 02d-05 | test_empty_desk_tests | 边界 | `unit` | tests 为空 | gen_agent | **规格待定（§7-F4）**：期望 exit 20，原因 `no_examples` | L1·S2 | |
| 02d-06 | test_smoke_6_pass | 成功 | `cli_integration` | `fx-labs-3x` 的 build | 真 `smoke_mcp` | 6 项 PASS | L3·S1 | |
| 02d-07 | test_smoke_default_query_first_alias | 边界 | `unit` | 卡的 aliases | smoke_mcp 生成 query | query == aliases[0] | L1·S1 | |
| 02d-08 | test_prompts_no_private_paths | 非法 | `unit` | `prompts/` | 扫描 | 没有私有路径 | L1·S3 | |

### WI-03a：`init_kb` + `gen_manifest`

**Law**：`init_kb` 幂等（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是目录树和 toml 字段。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 03a-01 | test_init_kb_toml_fields | 成功 | `cli_integration` | tmp | init_kb 后用真 klode 读取 toml | `[library] id/name/description` 齐全；klode 能加载 | L3·S1 | |
| 03a-02 | test_init_idempotent | 边缘 | `unit` | tmp | 跑两次 | 树 sha 相同；`.gitignore` 没有重复行 | L2·S5 | |
| 03a-03 | test_init_preserves_existing_gitignore | 边缘 | `unit` | 已有自定义的 `.gitignore` 行 | init_kb | 原有行原样保留，只在后面追加 | L2·S3 | |
| 03a-04 | test_lt2_heads_single_source | 边界 | `unit` | 章头数为 0、1、2 | gen_manifest | 0 和 1 时整本作为单一来源；2 时切章 | L1·S2 | |
| 03a-05 | test_split_selfcheck_fail_aborts | 非法 | `unit` | 拼不回原文的 manifest | gen_manifest | 报错，一片都不写 | L1·S3 | |
| 03a-06 | test_explicit_manifest_overrides | 成功 | `unit` | U7 的 `--manifest` | gen_manifest | 产物按显式 manifest 划分 | L1·S1 | |

### WI-03b：`gen_desk`

**Law**：任意章节数和项目数下，留出题都 ≥ 5 道且互不相同（S4）；医疗留出题只引用实际存在的维度（S4）。
**替身 / 配对**：无。**重构敏感点**：断言的是生成的 desk 和 holdout 内容。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 03b-01 | test_dimension_merge_gt8 | 边界 | `unit` | 9 章、20 章 | gen_desk | 维度数 ≤ 8，只合并相邻的章 | L1·S2 | |
| 03b-02 | test_lt2_one_dimension | 边界 | `unit` | 1 章 | gen_desk | 整本一个维度 | L1·S2 | |
| 03b-03 | test_holdout_ge5_distinct | 边缘 | `unit` | hyp：章节数和项目数 | gen_desk | 留出题 ≥ 5 道，互不相同 | L1·S4 | |
| 03b-04 | test_holdout_insufficient_exit20 | 非法 | `unit` | 维度和项目都极少，补题后仍不足 5 道 | gen_desk | exit 20，原因 `holdout_insufficient` | L1·S3 | |
| 03b-05 | test_medical_holdout_only_existing_dimensions | 边缘 | `unit` | hyp：items 的任意子集 | gen_desk | 每道题引用的维度都在 desk.json 里 | L1·S4 | |
| 03b-06 | test_explicit_desk_overrides | 成功 | `unit` | `--desk` | gen_desk | 产物等于显式指定的 desk | L1·S1 | |

### WI-10b：doctor / preflight

**Law**：No law：逐项检查环境。
**替身 / 配对**：10b-03 使用录制的信任探测结果，配对 00d2-05。**重构敏感点**：断言的是退出码、修复命令文本和 receipt。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 10b-01 | test_missing_binary_exit40 | 非法 | `cli_integration` | 从 PATH 里去掉 klode | `kb-check --doctor` | exit 40，输出修复命令 | L3·S3 | |
| 10b-02 | test_tracked_settings_local_exit40 | 非法 | `cli_integration` | tmp git 仓库里 `settings.local.json` 已被跟踪 | doctor | exit 40，并给出提示 | L2·S3 | |
| 10b-03 | test_untrusted_project_exit40 | 非法 | `unit` | 录制的「未信任」探测结果 | doctor | exit 40；提示「在数据项目目录交互运行一次 claude」 | L1·S3 | |
| 10b-04 | test_all_present_exit0 | 成功 | `cli_integration` | 依赖齐全的环境 | doctor | exit 0；receipt 里缺项为 0 | L3·S1 | |
| 10b-05 | test_mcp_import_degrade_level | 边界 | `cli_integration` | 正常环境；以及 PYTHONPATH 里放一个会抛 ImportError 的影子包 | doctor | 降级级别分别为 0 和 1，并写入 receipt | L3·S2 | |
| 10b-06 | test_no_download | 边缘 | `cli_integration` | tmp HOME | doctor | HF 缓存、uv 缓存、brew 目录前后树相同 | L2·S3 | |
| 10b-07 | test_gitignore_missing_entries_exit40 | 边界 | `unit` | `.gitignore` 里缺 `_mask_review/` | doctor | exit 40，并点出缺的条目 | L1·S2 | |

### WI-09a：pipeline 内核

**Law**：done 复用 ⟺ 三个 hash 全部匹配（S5）；resume 幂等（S5）；崩溃（pid 已死）后重跑该步。
**替身 / 配对**：OCR/writer 回放，配对 06-11、00d2-01。**重构敏感点**：断言的是 receipt、退出码和替身调用日志的条目数。
**并发**：09a-03。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09a-01 | test_stage_order | 成功 | `contract_replay` | `fx-u3-mixed` | `pipeline` 第一段 | receipt 依次为 preflight、ocr、audit；exit 10 | L2·S1 | |
| 09a-02 | test_done_reuse_requires_three_hashes | 边缘 | `contract_replay` | hyp：改动 inputs、config、outputs 这三个 sha 的任意子集 | 再跑一次 | 重跑 ⟺ 改动的子集非空（看替身调用条目数） | L2·S5 | |
| 09a-03 | test_lock_exit40 | 并发 | `contract_replay` | `fx-u1-3labs` | 两个真进程 | 第二个 exit 40；第一个的产物完整 | L2·S3 | |
| 09a-04 | test_lock_released_after_crash | 边缘 | `contract_replay` | 持锁的进程被 kill -9 | 再跑 | 能拿到锁，并正常完成 | L2·S3 | |
| 09a-05 | test_crash_recovery_dead_pid | 边缘 | `contract_replay` | receipt 为 running 且 pid 已死 | 再跑 | 该步被重跑，之后 receipt 为 done | L2·S5 | |
| 09a-06 | test_running_live_pid_not_stolen | 边界 | `contract_replay` | receipt 为 running 且 pid 还活着（是另一个进程） | 再跑 | exit 40，不抢占 | L2·S3 | |
| 09a-07 | test_exit_code_table | 边界 | `unit` | 每种原因各构造一个 | 映射 | 退出码全集恰好是 {0,2,10,20,21,22,30,40,50,60,61}；每种原因的映射都正确 | L1·S2 | |
| 09a-08 | test_env_errors_skip_loops | 非法 | `contract_replay` | 假 claude 返回认证失败 | build 阶段 | exit 40；没有任何 loop receipt | L2·S3 | |
| 09a-09 | test_usage_error_exit2 | 非法 | `unit` | `--stage nonsense` | pipeline | exit 2 | L1·S3 | |
| 09a-10 | test_resume_idempotent | 边缘 | `contract_replay` | 所有步骤都已 done | 连续 resume 两次 | 没有新 receipt；退出码相同 | L2·S5 | |

### WI-09b：13 步 build（`builds/<build_id>/`）

**Law（K8）**：对任意失败的第 k 步——k 之后的步不执行，`current` 不动，`builds/<id>/` 之外零写入（S5）；`build_id` 变化 ⟺ inputs 变化（S4）。
**替身 / 配对**：writer 用假 claude 回放；klode 用录制输出。配对 09b-09、00d2-01。**重构敏感点**：断言的是树快照 diff、receipt 和替身日志的内容。
**并发**：09b-10。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09b-01 | test_steps_fixed_order_all_pass | 成功 | `contract_replay` | `fx-labs-3x` | `pipeline --stage build` | `steps[].name` 等于 13 项枚举的顺序，全部 pass；在注入的时钟下 started 单调不降 | L2·S1 | |
| 09b-02 | test_no_writes_outside_builds | 边缘 | `contract_replay` | 数据项目 + tmp HOME 的完整快照（路径、sha、mtime） | build | 前后 diff ⊆ `builds/<id>/` | L2·S5 | |
| 09b-03 | test_failure_at_step_k_stops | 边缘 | `contract_replay` | hyp：k ∈ 1..13，通过回放场景注入失败 | build | 第 k 步之后的步没有 receipt；`current` 不变；`builds/` 之外零写入 | L2·S5 | |
| 09b-04 | test_build_dir_readonly_and_manifest | 成功 | `contract_replay` | 同 09b-01 | build 后再改一个文件 | 目录为 a-w；`MANIFEST.sha256` 校验通过；改动之后校验失败 | L2·S3 | |
| 09b-05 | test_audit_bindings_effective_only | 边缘 | `contract_replay` | `fx-redo` | build | `audit_bindings[X] == {1, a1}`；X ∈ `stale_candidates` | L2·S3 | |
| 09b-06 | test_excluded_not_in_inputs_u8 | 非法 | `contract_replay` | `fx-u8` 里选 none 的记录 | build | 该记录在 `excluded` 中且写明原因；不出现在任何输入清单或导出行里 | L2·S3 | |
| 09b-07 | test_pipeline_never_passes_force | 非法 | `contract_replay` | writer 产出的卡过不了门禁 | build | 假 claude 和 klode 的调用日志里都没有 `--force`；build 失败，exit 20 | L2·S3 | |
| 09b-08 | test_build_id_changes_iff_inputs_change | 边缘 | `unit` | hyp：inputs 的任意一个分量变化 | `build_id` | id 变化 ⟺ inputs 变化 | L1·S4 | |
| 09b-09 | test_build_u7_real_klode | 成功 | `cli_integration` | `fx-book-aurora` | 真 klode，writer 用回放 | 13 步全部 PASS；strict 0E | L3·S1 | |
| 09b-10 | test_same_build_concurrent_exit40 | 并发 | `contract_replay` | 同一份 inputs | 两个进程同时 build | 一个 exit 40；只生成一个 `builds/<id>/` | L2·S3 | |
| 09b-11 | test_zero_verified_records_build | 边界 | `contract_replay` | 全部记录都是 disputed | build | **规格待定（§7-F4）**：期望 exit 20，原因 `no_verified_records`；`excluded` 列出全部记录 | L2·S2 | |

### WI-09c：三条回路

**Law**：任意失败序列下，轮数 ≤ `max_total_rounds` 且一定会终止（S4）；预算先预留再派发，保证累计花费 ≤ 预算（S4）。
**替身 / 配对**：假 claude 回放，配对 00d2-01。**重构敏感点**：断言的是 receipt 里的回路记录、退出码和花费。
**并发**：09c-08。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09c-01 | test_l1_chain | 成功 | `contract_replay` | 留出集未达标的场景 | build | 依次出现 cue → apply → verify，两组成绩分开记录 | L2·S1 | |
| 09c-02 | test_l2_chain | 成功 | `contract_replay` | 门禁 1 先 FAIL 后 PASS | build | writer 被重新派发；引用该卡的 S5 失效并重做 | L2·S1 | |
| 09c-03 | test_l3_chain | 成功 | `contract_replay` | citation-rot | build | fix_anchors → write_card → strict 通过 | L2·S1 | |
| 09c-04 | test_l3_escalates_l2 | 边缘 | `contract_replay` | L3 修完后仍有残留 | build | 升级为 L2 | L2·S3 | |
| 09c-05 | test_l2_l3_simultaneous_l3_first | 边界 | `contract_replay` | L2 和 L3 同时触发 | build | L3 的记录排在 L2 前面 | L2·S2 | |
| 09c-06 | test_no_progress_terminates | 边缘 | `contract_replay` | 问题清单 hash 连续两轮相同 | build | exit 50；receipt 记下已花费金额和剩余问题 | L2·S3 | |
| 09c-07 | test_rounds_monotone_bounded | 边缘 | `unit` | hyp：失败序列 | loops 调度 | 轮数 ≤ max；一定终止 | L1·S4 | |
| 09c-08 | test_budget_reservation_before_dispatch | 并发 | `contract_replay` | 并发 4，预算只够 2 个任务 | build | 只派发了 2 个；累计花费 ≤ 预算 | L2·S4 | |
| 09c-09 | test_max_rounds_boundary | 边界 | `unit` | 第 6 轮和第 7 轮 | 调度 | 第 6 轮允许；第 7 轮 exit 50 | L1·S2 | |

### WI-10a：`/kb-check` 内核

**Law**：No law：判据表，各判据用阈值边界（S2）覆盖。
**替身 / 配对**：无（10a-01/02/09 用真 klode）。**重构敏感点**：断言的是 check_receipt 和退出码。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 10a-01 | test_stub_card_fails | 非法 | `cli_integration` | 含 stub 卡的 build | kb-check | FAIL，映射到 L2 | L3·S3 | |
| 10a-02 | test_folded_anchor_fails | 非法 | `cli_integration` | 含 FOLDED 锚点 | kb-check | FAIL，映射到 L3 | L3·S3 | |
| 10a-03 | test_forced_receipt_fails_check | 非法 | `contract_replay` | `forced=true` | kb-check | exit 20 | L2·S3 | |
| 10a-04 | test_loop_mapping | 边界 | `unit` | 每种失败各一个 | 映射 | 分别正确映射到 L1、L2、L3、40、50 | L1·S2 | |
| 10a-05 | test_aliases_boundary | 边界 | `unit` | aliases 为 9 和 10 | 判据 | 9 FAIL，10 PASS | L1·S2 | |
| 10a-06 | test_authored_anchors_boundary | 边界 | `unit` | authored 卡的锚点为 4 和 5 | 判据 | 4 FAIL，5 PASS | L1·S2 | |
| 10a-07 | test_check_reads_build_dir_not_current | 边缘 | `contract_replay` | `current` 指向合格的 A，B 有缺陷 | 对 B 运行 check | 报出 B 的缺陷 | L2·S3 | |
| 10a-08 | test_plugin_field_in_receipt | 成功 | `contract_replay` | 任意 build | kb-check | `plugin.{name,version,entry,root_sha}` 齐全 | L2·S1 | |
| 10a-09 | test_all_pass_receipt | 成功 | `cli_integration` | `fx-labs-3x` | kb-check | exit 0；所有判据 PASS | L3·S1 | |
| 10a-10 | test_u5_ready_false_on_pending | 边缘 | `unit` | 宿主状态为 pending | 汇总 | `u5_ready:false`，按警告计 | L1·S3 | |

### WI-09d：发布协议

**Law（K5）**：
- 任何崩溃点之后，`current` 都完整地指向某一个 build（S5）；
- 并发 readlink 永远看不到悬空的指针（S4）；
- prune 永不移动受保护集合中的 build（S4）；
- builds 与 _trash 中的 build 总数守恒（S5）。

**替身 / 配对**：无（纯文件系统）。登记之后的真实读取由 11a-01、11b-07 覆盖。**重构敏感点**：断言的是指针、`PUBLISH.log` 的行和目录树。
**并发**：09d-03、09d-11、09d-13。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09d-01 | test_publish_switches_pointer | 成功 | `contract_replay` | 新增 `fx-publish-ab`（在 tmp 里由回放构建的两个 build） | 发布 B | `readlink(current)` 为 B；`PUBLISH.log` 新增一行，带 seq 和 `target_build_id=B` | L2·S1 | |
| 09d-02 | test_pointer_always_complete | 边缘 | `contract_replay` | `fx-publish-ab` | 枚举所有崩溃点，再用 hyp 加随机 SIGKILL | `current` 要么是 A 要么是 B，存在、可解析、不悬空 | L2·S5 | |
| 09d-03 | test_switch_no_window | 并发 | `contract_replay` | 同上 | 一个线程连续 readlink，同时在 A/B 之间来回切换 200 次 | 读线程一次也没遇到 ENOENT 或悬空 | L2·S4 | |
| 09d-04 | test_stale_replay_exit22 | 非法 | `contract_replay` | `current` 为 C 时 replay B | `kb-publish --replay B` | exit 22；各登记产物的字节不变 | L2·S3 | |
| 09d-05 | test_replay_skips_newer_marker | 边缘 | `contract_replay` | 登记产物上的标记指向更新的 build | replay | 记 `skipped_newer`；产物不变 | L2·S3 | |
| 09d-06 | test_replay_only_failed_actions | 边缘 | `contract_replay` | 日志里 mcp_json 已 done，registry 为 failed | replay | 只重放 registry；`.mcp.json` 字节不变 | L2·S3 | |
| 09d-07 | test_post_publish_failure_exit21_pointer_kept | 非法 | `contract_replay` | registry 路径不可写 | 发布 | exit 21；`current` 已经指向新 build | L2·S3 | |
| 09d-08 | test_prune_never_moves_protected | 边缘 | `contract_replay` | hyp：发布日志 + 租约 + keep N | `--prune` | 受保护集合（current、回滚目标、待恢复的、最近 N 个、有存活读者的）都不在 `_trash` 里；其余都在 | L2·S4 | |
| 09d-09 | test_prune_conserves_builds | 边缘 | `contract_replay` | hyp | prune | 前后 `|builds|+|_trash|` 相等（只移动，不删除） | L2·S5 | |
| 09d-10 | test_prune_cleans_dead_leases | 边缘 | `contract_replay` | 某个 build 只有一个 pid 已死的租约 | prune | 租约被清掉，该 build 可以被移走 | L2·S3 | |
| 09d-11 | test_lease_vs_prune_race | 并发 | `contract_replay` | 真双进程：读者持续持有租约，prune 循环执行 | 并发运行 | 有存活读者的 build 从来没被移走 | L2·S5 | |
| 09d-12 | test_rollback | 成功 | `contract_replay` | 已发布 B，prev 为 A | `--rollback-to A` | 指针指向 A；所有登记都重放到 A；日志 action=rollback | L2·S1 | |
| 09d-13 | test_replay_rollback_share_lock | 并发 | `contract_replay` | replay 和 rollback 同时运行 | 并发 | 其中一个 exit 40；最终状态与串行执行的结果一致 | L2·S3 | |
| 09d-14 | test_keep_boundary | 边界 | `contract_replay` | keep N=0 和 N=1 | prune | 受保护集合依然保留 | L2·S2 | |
| 09d-15 | test_pending_judged_by_latest_status | 边界 | `unit` | 日志里同一个动作先 failed、后 done | 计算待恢复集合 | 该动作不算待恢复 | L1·S2 | |
| 09d-16 | test_publish_requires_check_pass | 非法 | `contract_replay` | `check_receipt` 为 FAIL | 发布 | 拒绝发布；指针不变 | L2·S3 | |

### WI-11a：MCP 包装

**Law（K7）**：在同一个进程里，每次调用时 `kb_version.build_id` == 那一刻的 `basename(readlink(current))`，在任意切换序列下都成立（S4/S5）。
**替身 / 配对**：无（真 klode 0.5.0）。强制 import 失败用影子包实现（11a-06/07）。**重构敏感点**：断言的是 JSON-RPC 响应内容。
**并发**：11a-09。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 11a-01 | test_same_process_A_to_B | 边缘 | `cli_integration` | `fx-publish-ab` | 同一个进程里：发布 A → 调 kb_version 和 search/verify → 发布 B → 再查 | build_id 依次为 A、B；查询结果分别对应 A、B 的内容；进程 pid 不变 | L3·S5 | |
| 11a-02 | test_kb_version_equals_readlink | 边缘 | `cli_integration` | hyp：切换序列 | 每次切换后调用一次 | 每次都等于 readlink 的结果 | L3·S4 | |
| 11a-03 | test_missing_current_iserror | 非法 | `cli_integration` | 没有 `current` | tools/call；然后发布；再调用 | 第一次返回 `isError:true`，进程仍存活；发布后调用正常 | L3·S3 | |
| 11a-04 | test_tools_list_includes_kb_version | 成功 | `cli_integration` | 任意 build | tools/list | 结果等于 klode 的 TOOLS 加上 kb_version | L3·S1 | |
| 11a-05 | test_handle_none_one_response_per_request | 边界 | `cli_integration` | 依次发送 initialize、tools/list、ping | 读 stdout | 每个请求恰好一行 JSON-RPC 响应，id 对得上；没有多出来的 `null` 输出 | L3·S2 | |
| 11a-06 | test_degrade_subprocess_proxy | 边缘 | `cli_integration` | 用影子包让 `klode.lib.mcp_server` import 失败 | 跑 11a-01 的 A→B 流程 | 仍然成立 | L3·S3 | |
| 11a-07 | test_degrade_cli_mode | 边缘 | `cli_integration` | 两级 import 都失败 | A→B | 仍然成立；输出文本与正常模式一致 | L3·S3 | |
| 11a-08 | test_lease_cleanup_on_switch_and_exit | 边缘 | `cli_integration` | `fx-publish-ab` | 切换后关闭 stdin | 切换后 A 的租约消失；EOF 后所有租约都消失 | L2·S3 | |
| 11a-09 | test_switch_mid_call_uses_old | 并发 | `cli_integration` | 一次慢查询（在大库上 search） | 查询进行时切换 | 这次调用的结果来自 A；下一次来自 B | L3·S3 | |
| 11a-10 | test_malformed_jsonrpc | 非法 | `cli_integration` | 一行垃圾数据 | 发送 | 返回 error 响应；进程仍存活，下一个请求正常 | L3·S3 | |

### WI-11b：登记器 + `u5_ready` 双探针【宿主探针 G2；真实项目 G3】

**Law**：`.mcp.json` 合并只改我们自己的键，其他键字节等价（S5）；registry 按 id 幂等 upsert（S5）。
**替身 / 配对**：无（11b-09 用真宿主）。**重构敏感点**：断言的是合并后的 JSON/TOML 内容。
**G3 守卫**：所有用例都在 `HOME=tmp`、`KLODE_REGISTRY=tmp` 下运行；11b-10 断言不会碰真实路径。
**并发**：11b-11。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 11b-01 | test_mcp_json_merge_preserves_others | 边缘 | `unit` | hyp：`.mcp.json` 里已有的键 | register 两次 | 其他键的值不变；我们的键指向包装的绝对路径并带 `env.KLODE_KIT_BUILD`；第二次结果与第一次相同 | L2·S5 | |
| 11b-02 | test_mcp_json_absent_created | 边界 | `unit` | 没有 `.mcp.json` | register | 新建文件，里面只有我们的键 | L2·S2 | |
| 11b-03 | test_mcp_json_malformed_refuse | 非法 | `unit` | `.mcp.json` 是畸形 JSON | register | 非 0 退出（期望 40）；原文件字节不变 | L2·S3 | |
| 11b-04 | test_settings_local_append | 成功 | `unit` | 已有 `enabledMcpjsonServers` | register 两次 | 列表里有该名字且不重复；其他内容不变 | L2·S1 | |
| 11b-05 | test_registry_upsert_idempotent | 边缘 | `unit` | `KLODE_REGISTRY=tmp` | upsert 两次 | 只有一个条目；路径指向 current；带 build_id | L2·S5 | |
| 11b-06 | test_expert_written_atomic | 成功 | `unit` | tmp 数据项目 | 发布 agent | `.claude/agents/<kb>-expert.md` 存在，第一行有 build 标记 | L2·S1 | |
| 11b-07 | test_direct_probe_build_id | 成功 | `cli_integration` | tmp 合成数据项目 | 用 `.mcp.json` 里的同一份 argv 和 cwd 起包装，调用 kb_version | build_id == 目标 | L3·S1 | |
| 11b-08 | test_probe_mismatch_exit21 | 非法 | `cli_integration` | 把 current 指回旧 build | 探针 | `u5_ready:false`；exit 21 | L2·S3 | |
| 11b-09 | test_host_probe_tool_result | 成功 | `model_integration` | tmp 合成数据项目（`_lab`） | `claude -p --output-format stream-json --verbose` | 按 tool_use id 取到的 tool_result 里 build_id == 目标 | L3·S1 | G2 G5 |
| 11b-10 | test_never_touches_real_paths | 边缘 | `unit` | `HOME=tmp` | 完整跑一遍 register | 所有写入路径都在 tmp 下；默认 registry 路径解析为 `tmp/.klode/registry.toml` | L2·S3 | |
| 11b-11 | test_concurrent_registry_upsert | 并发 | `unit` | tmp | 两个进程分别 upsert 不同的 id | 两个条目都在；文件是合法 TOML | L2·S5 | |

---

## 5. M3 · med-intake、workflow、打包、文档

### WI-12：agents【G2】

**Law**：kb-verifier 运行前后，记录的 status 字节不变（K3 的一部分，用 S3 覆盖；跨阶段的性质见 08c-16）。
**替身 / 配对**：verifier 用假 claude 回放，配对 12-04。**重构敏感点**：断言的是 frontmatter 解析结果和记录字节。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 12-01 | test_frontmatter_static | 成功 | `unit` | `agents/*.md` | 解析 | YAML 合法；没有 hooks、mcpServers、permissionMode | L1·S2 | |
| 12-02 | test_frontmatter_forbidden_keys_rejected | 非法 | `unit` | tmp 拷贝里注入 `hooks:` | 静态检查 | FAIL | L1·S3 | |
| 12-03 | test_verifier_never_changes_status | 边缘 | `contract_replay` | `fx-u8` + verifier 回放 | 运行 verifier | `_by_id/**` 字节不变；`advice.json` 通过 schema | L2·S3 | |
| 12-04 | test_real_writer_card_check_pass | 成功 | `model_integration` | `fx-book-aurora` | 真实写卡 | check_cards PASS | L3·S1 | G2 |
| 12-05 | test_host_discovers_expert | 成功 | `host_acceptance` | `_lab` 数据项目 | `claude -p` 列出 agents | 列表里有 `<kb>-expert` | L3·S1 | G2 G5 |
| 12-06 | test_verifier_tools_readonly | 非法 | `unit` | kb-verifier 的 frontmatter | 解析 | tools 里没有 Edit、Write、Bash | L1·S3 | |

### WI-13b：`/med-intake` 编排

**Law**：日期别名是稳定的，重跑后仍指向同一个 batch（S5）。
**替身 / 配对**：OCR 回放，配对 06-11/12。**重构敏感点**：断言的是 stdout、退出码和符号链接。
**并发**：13b-07。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 13b-01 | test_u1_stdout_and_receipt | 成功 | `contract_replay` | `fx-u1-3labs` | `bin/klode-kit med-intake --inbox <tmp>/_inbox/2026-10-22/` | stdout 里两行原文都在；exit 10；receipt 齐全 | L2·S1 | |
| 13b-02 | test_alias_symlink_stable | 边缘 | `contract_replay` | 同上 | 重跑 | `work/20261022` 和 `confirm/20261022.html` 的链接目标不变 | L2·S5 | |
| 13b-03 | test_type_by_header_keyword | 边界 | `unit` | 抬头为「检验报告」「处方」，以及没有关键词 | 判定单据类型 | 依次为 lab、rx；没有关键词时要求 `--type`，exit 2 | L1·S2 | |
| 13b-04 | test_explicit_type_overrides | 成功 | `unit` | `--type rx` | 判定单据类型 | rx | L1·S1 | |
| 13b-05 | test_alias_collision_same_date | 边缘 | `contract_replay` | 同一个日期、不同图片的第二批 | med-intake | **规格待定（§7-F4）**：不能悄悄覆盖已有别名；期望新建 `20261022-2`，或者 exit 30 | L2·S3 | |
| 13b-06 | test_empty_inbox_exit2 | 非法 | `contract_replay` | 空 inbox | med-intake | exit 2 | L1·S3 | |
| 13b-07 | test_concurrent_med_intake_exit40 | 并发 | `contract_replay` | 两个 inbox | 两个进程同时运行 | 一个 exit 40，另一个完整跑完 | L2·S3 | |

### WI-13a：五个 SKILL.md【G2】

**Law**：No law：静态文本检查。
**替身 / 配对**：无。**重构敏感点**：断言的是 SKILL.md 的文本。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 13a-01 | test_skills_static | 成功 | `unit` | `skills/*/SKILL.md` | 解析 | 正文都调用 `${CLAUDE_PLUGIN_ROOT}/bin/klode-kit`；白名单是 `Bash(*/bin/klode-kit *)` | L1·S2 | |
| 13a-02 | test_skills_no_business_logic | 非法 | `unit` | 同上 | 扫描 | 没有 `python -c`，也没有内联脚本块 | L1·S3 | |
| 13a-03 | test_known_pitfalls_and_iron_rules | 边界 | `unit` | 同上 | 计数 | A11 的 12 条已知坑和 A8 的 2 条铁律都在 | L1·S2 | |
| 13a-04 | test_kb_check_entry | 成功 | `host_acceptance` | `fx-labs-3x` | `claude -p --plugin-dir .` | `plugin.root_sha == release_sha` | L3·S1 | G2 G5 |

### WI-14：Workflow 薄壳【G2】

**Law**：No law：静态约束 + 两段的阶段序列。
**替身 / 配对**：14-03/05 在 node 里给 `agent()` 打桩并记录调用（这是宿主运行时的边界），配对 14-04。**重构敏感点**：断言的是阶段序列，不碰 JS 的内部结构。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 14-01 | test_workflow_static | 非法 | `unit` | `workflows/kb-pipeline.js` | 扫描 | 没有 `Date.now`、`Math.random`、`require('fs')`、`child_process` | L1·S3 | |
| 14-02 | test_one_agent_per_stage_with_schema | 成功 | `unit` | 同上 | 解析 | 每个阶段恰好一次 `agent(…, {schema})` | L1·S2 | |
| 14-03 | test_stage1_ends_at_audit | 边界 | `cli_integration` needs_node | `agent()` 桩在 audit 阶段返回 exit 10 | 用 node 运行脚本 | 阶段序列为 preflight、ocr、audit，然后结束 | L5·S2 | |
| 14-04 | test_workflow_two_stage | 成功 | `host_acceptance` | `fx-u3-mixed` | 在宿主里实跑 | 两段都通过；headless 不可行时降级为用户观察项 | L3·S1 | G2 G5 |
| 14-05 | test_resume_sequence | 边界 | `cli_integration` needs_node | 带 `args.resume` | 用 node 运行 | 阶段序列为 writeback、build、check、publish | L5·S2 | |

### WI-15a：打包与安装【G4】

**Law**：重复安装是幂等的（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是启动器的输出和 `.venv` 的状态。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 15a-01 | test_install_outside_repo | 成功 | `cli_integration` | tmp HOME + tmp cwd | 运行 `scripts/install.sh`，再 `--version` | 输出包版本 | L3·S1 | G4 |
| 15a-02 | test_reinstall_idempotent | 边缘 | `cli_integration` | 已经装好 | 再装一次 | `uv.lock` 不变；`--version` 仍然正常 | L2·S5 | G4 |
| 15a-03 | test_klode_import_smoke | 成功 | `cli_integration` | 已经装好 | `python -c "import klode.lib"` | exit 0；`klode.__version__ == "0.5.0"` | L3·S1 | |
| 15a-04 | test_launcher_python_override | 边界 | `cli_integration` | 设置 `KLODE_KIT_PYTHON` | 启动器 | 用的是指定的解释器（看输出的 `sys.executable`） | L3·S2 | |
| 15a-05 | test_missing_venv_message | 非法 | `cli_integration` | 没有 `.venv` | 启动器 | exit 40，提示先运行 install.sh | L3·S3 | |

### WI-15b：`release_manifest` + `release_check`

**Law**：`release_sha` 变化 ⟺ 发布清单里某个文件的内容或相对路径变化（S4/S6）。
**替身 / 配对**：时钟注入（15b-05）。**重构敏感点**：断言的是 sha 和退出码。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 15b-01 | test_release_sha_changes_iff_file_changes | 边缘 | `unit` | hyp：文件树 + 一处改动（在清单内或清单外） | `release_manifest` | sha 变化 ⟺ 改动落在清单内 | L1·S4 | |
| 15b-02 | test_template_only_change_invalidates | 非法 | `unit` | 只改了 `templates/` 下的一个文件 | 用 release_check 校验旧 receipt | 旧 receipt 失效 | L2·S3 | |
| 15b-03 | test_missing_lane_receipt_fails | 非法 | `unit` | 缺 host lane 的 receipt | release_check | FAIL，并点出缺的是哪条 lane | L1·S3 | |
| 15b-04 | test_dirty_tree_fails | 非法 | `unit` | tmp git 仓库里有未提交的改动 | release_check | FAIL | L2·S3 | |
| 15b-05 | test_receipt_age_boundary | 边界 | `unit` | 注入时钟：正好 7 天；7 天加 1 秒 | release_check | 前者 PASS，后者 FAIL | L1·S2 | |
| 15b-06 | test_manifest_covers_all_runtime_globs | 边界 | `unit` | R3-08 列出的每个 glob | release_manifest | 每个 glob 在 `RELEASE.sha256` 里至少有一个文件；有 glob 匹配为空时 FAIL | L1·S2 | |
| 15b-07 | test_all_green_passes | 成功 | `unit` | 合成的五份 PASS receipt | release_check | exit 0 | L1·S1 | |
| 15b-08 | test_rename_same_content_changes_sha | 边缘 | `unit` | 内容不变，只改相对路径 | release_manifest | sha 变化 | L1·S6 | |

### WI-30：文档

**Law**：No law：检查链接和路径是否存在。
**替身 / 配对**：无。**重构敏感点**：断言的是文档文本与文件系统的对应关系。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 30-01 | test_doc_paths_exist | 成功 | `unit` | `docs/*.md`、`README_v2.md` | 抽取其中的命令和路径 | 每一个都真实存在 | L2·S2 | |
| 30-02 | test_docs_no_private_ids | 非法 | `unit` | 同上 | 扫描 | 不含私有项目的编号或路径 | L1·S3 | |
| 30-03 | test_readme_original_untouched | 边缘 | `unit` | git HEAD | 比对 | `README.md` 的 sha 与 HEAD 中的一致；`README_v2.md` 存在（拍板 9 和全局规则） | L2·S3 | |
| 30-04 | test_directory_doc_covers_top_dirs | 边界 | `unit` | `docs/目录结构.md` | 与顶层目录比对 | 每个顶层目录都有说明 | L1·S2 | |

### WI-31：U1–U8 验收套件【含 G2】

**Law**：来源链在流水线的真实产物上同样成立（P-01/P-02，S5）。
**替身 / 配对**：replay 版本的配对就是同一张表里的 cli、model、host 版本。**重构敏感点**：只通过用户入口（`bin/klode-kit <子命令>`、宿主斜杠命令）断言，与内部结构无关。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| U1-01 | test_u1_med_intake | 成功 | `contract_replay` | `fx-u1-3labs` | `med-intake --inbox <tmp>/_inbox/2026-10-22/` | stdout 里有 `ocr: 3 页 × 4 路 OK` 和 `audit: 5 条分歧 → confirm/20261022.html`；exit 10；状态 `awaiting_confirm`；`work/20261022/ocr/receipt.json` 四路都有耗时，失败页 0 | L2·S1 | |
| U1-02 | test_u1_rerun_idempotent | 边缘 | `contract_replay` | 同上 | 重跑 | 替身没有新的调用；stdout 相同；batch 相同 | L2·S5 | |
| U1-03 | test_u1_dispute_count_tracks_fixture | 边界 | `contract_replay` | `fx-u1-3labs` 和 `fx-u3-mixed` | 分别跑 med-intake | 分歧数分别等于各自真值里的分歧数（防止把「5」写死） | L2·S2 | |
| U2-01 | test_u2_kb_ocr_rotated | 成功 | `contract_replay` | `fx-rotated90` | `kb-ocr --batch _inbox/clinic-0918/ --routes vision,mlx,claude,codex` | 每页 `rotate: 90`；`_masked/` 下打码图的抬头区域像素全为 0；对照图在 `_mask_review/`（R-2）；`ocr/vision/<fp>/p001.jsonl` 每行的 `bbox_px` 都是 4 个整数 | L2·S1 | |
| U2-02 | test_u2_kb_ocr_rotated_macos | 成功 | `cli_integration` macos | 同上 | 真 Vision | 同上（vision 这一路是真的） | L3·S1 | G4 |
| U2-03 | test_u2_unmasked_never_sent | 非法 | `contract_replay` | 同上，但跳过打码直接发送原图 | kb-ocr | exit 60；云端替身调用 0 条 | L2·S3 | |
| U3-01 | test_u3_pipeline_first_stage | 成功 | `contract_replay` | `fx-u3-mixed` | pipeline 第一段 | `records/2026-10-22-lab.md` 存在，`status: pending_confirm`、`uncertain: []`；无分歧的页为 verified | L2·S1 | |
| U3-02 | test_u3_auto_verified_only_when_all_agree | 边缘 | `contract_replay` | `fx-u3-mixed`，把无分歧页某一路的一个值翻转 | 第一段 | 该页从 verified 变为 pending_confirm（metamorphic，K1） | L2·S6 | |
| U3-03 | test_u3_workflows_host | 成功 | `host_acceptance` | `fx-u3-mixed` | `/klode-kit:kb-pipeline` | `/workflows` 里出现 kb-pipeline；headless 不可行时由用户观察并签字 | L3·S1 | G2 G5 |
| U4-01 | test_u4_guide_pdf | 成功 | `cli_integration` | `fx-text-pdf`（当作 guide-2026.pdf） | kb-ocr，再 `kb-build --kb kb/guide-klode` | `text_layer: true` 且跳过四路；`build_receipt` 13 步全部 PASS；`klode check --strict` 输出 `OK: 0 errors, 0 warning(s)` | L3·S1 | |
| U4-02 | test_u4_mixed_pdf_not_skipped | 边界 | `cli_integration` | `fx-mixed-pdf` | kb-ocr | 只有 text 页被跳过 | L3·S2 | |
| U5-01 | test_u5_verify_cli | 成功 | `cli_integration` | 医疗 fixture 库 | `klode verify <card> "示例片 10mg 2026-09-18"`；以及包装的 verify | 都是 VERIFIED，两者结果一致 | L3·S1 | |
| U5-02 | test_u5_expert_qa | 成功 | `model_integration` | 同上，在 `_lab` 数据项目里 | 问 `med-klode-expert`「示例片最近三次的剂量」 | 每条回答都带 `(grep: …)`；逐条 verify 都是 VERIFIED | L3·S1 | G2 G5 |
| U5-03 | test_u5_expert_not_found | 非法 | `model_integration` | 同上 | 问一个库里没有的药 | 回答「查不到」；回答里没有任何剂量数字 | L3·S3 | G2 G5 |
| U6-01 | test_u6_redo_keeps_verified_acceptance | 边缘 | `contract_replay` | `fx-redo` | 通过用户入口跑 `kb-ocr --batch … --redo`，再跑 `kb-audit` | 出现新的 `ocr/<engine>/<新fp>/`，旧目录的树 sha 不变；`fixes.jsonl` 原有前缀字节不变；`verified/*.md` 的链接目标和 sha 不变，点选提交后才更新 | L2·S5 | |
| U7-01 | test_u7_book_build | 成功 | `cli_integration` | `fx-book-aurora` | `kb-build --kb kb/demo-klode --source aurora-kettle.txt --desk src/desk.json` | `build_receipt` 里：3 片拼回 == 原文、check_cards 3/3、strict 0E、desk verify 5/5；`cards_content/` 下 3 张卡；`.mcp.json` 新增 `demo-klode`；smoke 6 项 PASS | L3·S1 | |
| U7-02 | test_u7_mcp_json_other_entries_preserved | 边缘 | `cli_integration` | 同上，`.mcp.json` 里事先已有别的服务器 | 同上 | 别的条目字节等价 | L2·S5 | |
| U8-01 | test_u8_confirm_writeback | 成功 | `contract_replay` | `fx-u8` | 渲染确认页 → 模拟点选（含「都不对」）→ `kb-audit --writeback choices.json` | 每条都有小图、四路候选和 ★；`verified/2026-10-22-lab.md` 为 `status: verified` | L2·S1 | |
| U8-02 | test_u8_export_via_node | 成功 | `cli_integration` needs_node | 同上 | 用 node 执行导出 JS 后写回 | 结果与 U8-01 相同 | L3·S1 | |
| U8-03 | test_u8_none_disputed_excluded | 非法 | `contract_replay` | 同上 | 写回后 build | 该记录 `status: disputed`；uncertain 条目的 id 为 `p002-l07`（R-14）；不在 `build_receipt` 的输入清单里 | L2·S3 | |
| U8-04 | test_u8_resubmit_same_choices | 边缘 | `contract_replay` | 同上 | 再提交一次 | 没有新 rev，视图不变 | L2·S5 | |
| P-01 | test_provenance_roundtrip_on_pipeline_output | 边缘 | `contract_replay` | `fx-u8` 写回 + build 之后的回放全链 | 00b3 校验器 | 0 违例（K4） | L1·S5 | |
| P-02 | test_provenance_roundtrip_on_cli_output | 边缘 | `cli_integration` | U7 和 U5 用真 klode 生成的产物 | 00b3 校验器 | 0 违例 | L1·S5 | |
| UC-01 | test_u1_two_inboxes_concurrent | 并发 | `contract_replay` | 两个 inbox | 两个 med-intake 并发 | 一个 exit 40 并给出提示；另一个干净地跑完，产物与单独运行时相同 | L2·S3 | |

### WI-19（可选）：回修用户 skill【G1】

不进 CI，只在用户批准后手动执行。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 19-01 | manual_b1_b5_repro_flip | 成功 | `manual` | 03 基线 §5 的复现命令 | 用户批准后，在 `~/.claude/skills/klode-build/` 上执行 | 每条复现命令的结果都翻转为预期 | L2·S1 | G1 |
| 19-02 | manual_backup_before_write | 非法 | `manual` | — | 写入之前 | 已列出备份的位置和时间且备份确实存在，用户批准已记录；缺任何一项就不写 | L2·S3 | G1 |

---

## 6. U1–U8 用户感知层验收表（对应计划 §3）

| U | 计划 §3 中的那一行 | 用户能直接看到的东西 | 覆盖用例（主 + 补强） | lane | 闸门 |
|---|---|---|---|---|---|
| U1 | `test_u1_med_intake.py`（contract_replay） | stdout 两行原文、exit 10、awaiting_confirm、receipt 四路耗时 | U1-01；U1-02（重跑幂等）、U1-03（分歧数不写死）、UC-01（并发）、13b-01/02 | contract_replay | — |
| U2 | `test_u2_kb_ocr_rotated.py`（contract_replay；cli 版带 macos） | rotate 90、`_masked/` 抬头全黑、对照图在 `_mask_review/`、bbox_px 为 4 个整数 | U2-01、U2-02；U2-03（未打码的图永远不外发）、04b-01、05a-01/07 | contract_replay、cli_integration macos | G4（U2-02） |
| U3 | `test_u3_pipeline_first_stage.py` + `test_u3_workflows_host.py` | records 文件为 pending_confirm、`uncertain: []`；无分歧页为 verified；`/workflows` 里可见 | U3-01、U3-03；U3-02（K1 metamorphic）、08d-03、00d2-03/04 | contract_replay、host_acceptance | G2 G5（U3-03） |
| U4 | `test_u4_guide_pdf.py`（cli_integration） | text_layer、routes_skipped、13 步全部 PASS、`OK: 0 errors, 0 warning(s)` | U4-01；U4-02、04c-04、09b-01 | cli_integration | — |
| U5 | `test_u5_verify_cli.py` + `test_u5_expert_qa.py` | verify 为 VERIFIED、包装的结果一致、回答逐条带锚点、问不到的内容回答「查不到」 | U5-01、U5-02、U5-03；01a-10（人工改值后旧值 NOT FOUND）、11a-01、11b-07/09 | cli_integration、model_integration | G2 G5（U5-02/03） |
| U6 | `tests/replay/test_u6_redo_keeps_verified.py` | 新的 fp 目录、旧目录不动、fixes 只追加、verified 保持到点选完成 | U6-01；08d-06、04b-06、08c-09、09b-05 | contract_replay | — |
| U7 | `test_u7_book_build.py`（cli，writer 用回放） | 拼回 == 原文、3/3、0E、5/5、3 张卡、`.mcp.json` 条目、smoke 6 项 | U7-01；U7-02、02a-01、09b-09、02d-06 | cli_integration | — |
| U8 | `test_u8_confirm_writeback.py`（contract_replay + cli needs_node） | 小图 + 四路候选 + ★；写回后为 verified；选「都不对」的为 disputed、含 `p002-l07`、不进 build 输入 | U8-01、U8-02、U8-03；U8-04、08b-01/09、08c-11、09b-06 | contract_replay、cli_integration needs_node | — |

U1–U8 每一条都至少有一个从用户入口发起的用例。

---

## 7. 交给 spec-adversary 之前自己找出的缺口和规格问题

| # | 问题 | 处理 |
|---|---|---|
| F1 | r4.3 字面写的是 `not cwd.startswith(数据项目)`。按字符串前缀比较，会把 `/tmp/x/p2` 误判为在 `/tmp/x/p` 里面；而且不按 realpath 判断的话，符号链接可以绕过 | 00c1-06/07 要求先取 realpath，再按路径分量比较 |
| F2 | 版本下限如果按字符串比较，2.1.1000 会被当成小于 2.1.259 | 00c1-11 |
| F3 | 同一张图出现两次时 `batch_id` 怎么算没有定义（r2.4 写的是 `sorted(img_sha256)`） | 00b2-02 标为规格待定，要求实现前先记下决定 |
| F4 | 以下情况在 02/04 中都没有规定：空 inbox、inbox 里有非图片文件、没有任何 verified 记录时 build、同一日期第二批 med-intake、desk tests 为空 | 04b-10/11、09b-11、13b-05、02d-05 标为「规格待定」并给出期望值。实现者要先确认，或者修改期望并写明理由（policy 第 12 条） |
| F5 | 计划只在 07c（compare）层有 auto-agree 的性质测试。如果 writeback 不看 uncertain，或者 gate 漏了 route_disabled，07c 依然全绿，问题会被放过 | 新增跨层的 08a-07、08c-10，以及 metamorphic 的 U3-02 |
| F6 | 计划没有测试「伪造的 choices 能否把页级 uncertain 的记录点成 verified」 | 08c-11 |
| F7 | 计划没有测试「除 writeback 以外没有任何阶段写 status」 | 08c-16（metamorphic）、12-03 |
| F8 | 发送器有 TOCTOU 窗口：检查 sha 之后、复制之前，文件可能被换掉 | 05b-08；要求复制后对副本重算 sha，并且只发送副本 |
| F9 | 崩溃注入需要在生产代码里加 `crashpoint()` 缝，与 policy 第 13 条有张力 | §0.3：枚举写点，再用随机 SIGKILL 兜底；在 WI-00s 记录一次决定 |
| F10 | 计划把 `test_generation_deterministic` 放在 unit lane，但它只能在 macOS 上运行，到了 Linux CI 会变成 skip 冒充 PASS | 移到 `cli_integration` macos（00a-01/02） |
| F11 | 累计费用恰好等于 `max_cost_usd_per_batch` 时是否算「超过」，没有写明 | 06-07 按严格大于断言 |
| F12 | R-2（U2 的对照图放 `_mask_review/`，而 04 字面写的是 `_masked/`）还在等用户确认 | U2-01 按设计断言；如果用户不同意，只需要改这一行的期望 |
| F13 | 计划概览写的是 53 个 WI，而你给的数字是 52 | 多出来的是 WI-17：它没有 lane、没有测试，归入 Deferred；WI-19 只有 manual 用例 |

需新增的 fixture：
- `fx-corrupt-pdf`（04c-06）。
- `tests/fixtures/replay/probe-synth/*.jsonl`（00c2-02/03/04/10，手写的合成事件流）。
- `fx-publish-ab`：由测试辅助函数在 tmp 中用 `fx-labs-1x`、`fx-labs-3x` 的回放构建，不入库。

另外，`fx-chain` 需要显式包含 `legal-{manual,dateshift,alias,oldbuild,pdftext_dpi}` 五个合法变体目录。以上入库的文件都要登记进 MANIFEST。

---

## 8. 统计

**总数：490**

| lane | 数量 |
|---|---|
| `unit` | 254 |
| `contract_replay` | 155 |
| `cli_integration` | 57 |
| `model_integration` | 13 |
| `host_acceptance` | 9 |
| `manual` | 2 |

| 五类 | 数量 |
|---|---|
| 成功 | 119 |
| 边界 | 89 |
| 非法 | 119 |
| 边缘 | 140 |
| 并发 | 23 |

| 里程碑 | 用例 | unit | contract_replay | cli | model | host | manual |
|---|---|---|---|---|---|---|---|
| M0 | 129 | 85 | 27 | 5 | 7 | 5 | 0 |
| M1 | 144 | 68 | 65 | 9 | 2 | 0 | 0 |
| M2 | 152 | 79 | 44 | 28 | 1 | 0 | 0 |
| M3 | 65 | 22 | 19 | 15 | 3 | 4 | 2 |

需要手动闸门的用例：

| 闸门 | 用例 |
|---|---|
| G1 | 19-01、19-02 |
| G2 | 00c2-06/07/08/09、00c3-09、00d2-01/02/03/04/07、06-11/12、11b-09、12-04/05、13a-04、14-04、U3-03、U5-02/03，共 20 条 |
| G3 | 没有自动用例会碰到；11b-10 断言不碰真实路径。对真实项目执行 publish 属于阶段 6，由用户执行 |
| G4 | 04a-10/11/12、05a-07、15a-01/02、U2-02（这些用例的前提是安装依赖或下载权重） |
| G5 | 00d2-03/04/05/06/07、11b-09、12-05、13a-04、14-04、U3-03、U5-02/03 |

## Deferred

- **WI-17**（ask-arbiter，可选）：没有 lane，也没有自动测试；内容是否与 04 的拍板一致由人工核对。
- 计划 Deferred 清单里的项都不设计测试，包括：klode gate rubric、MLX 权重下载、截图拼接、kb_status/kb_map、备份实现、SDK 附图、五个组件目录以外的组件、改 klode 上游、每次建库都加人工停点。
- mutation（mutmut ≥ 80%）是对上述 criticalPaths 用例的度量，不另设用例。
