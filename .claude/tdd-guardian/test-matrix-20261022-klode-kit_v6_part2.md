# Test Matrix v6（第 2 部分）— klode-kit（plan-20261022-klode-kit，M0–M3）

> 本文件接续 `test-matrix-20261022-klode-kit_v6.md`（§0–§3），包含 §4–§9 与 Deferred。两个文件合起来是完整的 v6。读法、约定和 §0.3 测试缝规格见第 1 部分。

---

## 4. M2 · build / check / 发布 / MCP

### WI-01a：`records_to_txt` + `export_map`

**Law**：
- 行唯一（S4）。
- 与 cells 无关（S6）。
- 来源链成立（S5）。
- 重跑结果相同（S5）。
- **整行**由 `audit_bindings` 绑定的 rev 投影出来（v2，缺口 11）。

**替身 / 配对**：无。**重构敏感点**：断言的是导出文本和 sha。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 01a-01 | test_line_format | 成功 | `unit` | `fx-labs-1x`（effective） | `records_to_txt` | 行与 `<item> <value_audited> <date> [flag] ⟨rec_id⟩` 精确相等 | L1·S1 | |
| 01a-02 | test_only_verified_exported | 非法 | `unit` | verified、disputed、pending 混在一起 | 导出 | 只出现 verified 的 rec_id | L1·S3 | |
| 01a-03 | test_values_from_binding_rev | 边缘 | `unit` | **v2**：effective r1 与候选 r2 的 item、value、date、flag **四列都不同** | 导出 | 整行等于由 r1 投影出的行（四列都取自 r1） | L1·S3 | |
| 01a-04 | test_cells_independence | 边缘 | `contract_replay` | `fx-u8` | 删掉或打乱 `cells.jsonl` 后导出 | 输出 sha 不变 | L2·S6 | |
| 01a-05 | test_lines_unique_by_construction | 边缘 | `unit` | hyp：观测（含同日、同项目、同值） | 导出 | 任意两行都不相同 | L1·S4 | |
| 01a-06 | test_fx_labs_3x_three_distinct | 边界 | `unit` | `fx-labs-3x` | 导出 | 3 行互不相同 | L1·S2 | |
| 01a-07 | test_export_map_provenance | 边缘 | `contract_replay` | `fx-chain` 回放全链 | 导出后用 00b3 校验器检查 | 0 违例；导出行与 map 行一一对应 | L1·S5 | |
| 01a-08 | test_empty_record_rejected | 非法 | `unit` | 没有字段的记录 | 导出 | 进入 `excluded`，原因 `empty_record`；不输出任何行 | L1·S3 | |
| 01a-09 | test_two_shelves | 成功 | `unit` | `fx-labs-3x` | 导出 | `library/records/<rec_id>.txt` 和 `library/items/<slug>.txt` 都存在，内容各归各架 | L1·S2 | |
| 01a-10 | test_u5_after_manual_fix | 边缘 | `cli_integration` | `fx-manualfix` | 真 `klode verify <card> "示例片 1.05 2026-09-18"`；再用 10.5 | 前者 VERIFIED，后者 NOT FOUND | L3·S3 | |
| 01a-11 | test_export_deterministic_rerun | 边缘 | `unit` | 任意 fx | 导出两次 | 字节相同 | L2·S5 | |

### WI-01b：`records_to_l1` + `items_index`

**Law**：锚点与行一一对应（S4）。
**替身 / 配对**：无。**重构敏感点**：断言的是卡片内容。
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

**Law**：
- 假名稳定（S5）。
- 日期平移后，顺序和间隔不变（S5）。
- 只有一个偏移量（S5）。

**替身 / 配对**：无。**重构敏感点**：断言的是输出文本和密钥位置。
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

**Law**：
- 拼回 == 原文（S5）。
- B5 幂等（S5）。
- 变体集合是超集（S6）。

**替身 / 配对**：无。**重构敏感点**：断言的是文件内容。
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

**Law**：幂等（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是字节和 receipt。
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

**Law**：`cue_suggest` 与留出集无关（S6）。
**替身 / 配对**：无。**重构敏感点**：断言的是分数和退出码。
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

**Law**：No law：模板渲染。
**替身 / 配对**：无。**重构敏感点**：断言的是生成文本。
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

**Law**：init 幂等（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是目录树和 toml。
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

**Law**：
- 留出题 ≥ 5 道，且互不相同（S4）。
- 只引用实际存在的维度（S4）。

**替身 / 配对**：无。**重构敏感点**：断言的是生成内容。
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
**替身 / 配对**：10b-03 用录制的探测结果，配对 00d2-05。**重构敏感点**：断言的是退出码和修复命令。
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
| 10b-08 | test_doctor_reports_ignored_seam_vars | 非法 | `unit` | 参数化：环境里残留 `KLODE_KIT_PAUSE_AT`、`KLODE_KIT_CRASH_AT` 或 `KLODE_KIT_PAUSE_DIR` 之一，并且 ① 没有 `KLODE_KIT_TEST_SEAMS=1`，或 ② `KLODE_KIT_REAL=1` | doctor | exit 40，输出点名残留的变量并给出 `unset` 命令；不设这些变量时，这一项通过（F9） | L1·S3 | |

### WI-09a：pipeline 内核

**Law**：
- 三个 hash 都匹配才复用（S5）。
- resume 幂等（S5）。

**替身 / 配对**：回放，配对 06-11、00d2-01。**重构敏感点**：断言的是 receipt 和退出码。
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

**Law（K8）**：
- 任意第 k 步失败后：后续步骤不执行、指针不动、外部零写入（S5）。
- `build_id` 变 ⟺ inputs 变（S4）。
- 完成后整个 build 树**递归**只读（v2，缺口 12）。

**替身 / 配对**：writer 用回放、klode 用录制，配对 09b-09、00d2-01。**重构敏感点**：断言的是树快照和 receipt。
**并发**：09b-10。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09b-01 | test_steps_fixed_order_all_pass | 成功 | `contract_replay` | `fx-labs-3x` | `pipeline --stage build` | `steps[].name` 等于 13 项枚举的顺序，全部 pass；在注入的时钟下 started 单调不降 | L2·S1 | |
| 09b-02 | test_no_writes_outside_builds | 边缘 | `contract_replay` | 数据项目 + tmp HOME 的完整快照（路径、sha、mtime） | build | 前后 diff ⊆ `builds/<id>/` | L2·S5 | |
| 09b-03 | test_failure_at_step_k_stops | 边缘 | `contract_replay` | hyp：k ∈ 1..13，通过回放场景注入失败 | build | 第 k 步之后的步没有 receipt；`current` 不变；`builds/` 之外零写入 | L2·S5 | |
| 09b-04 | test_build_dir_readonly_and_manifest | 成功 | `contract_replay` | 同 09b-01 | build 后，测试先 `chmod u+w`，再改一个文件 | `MANIFEST.sha256` 先校验通过，改动之后校验失败（只读位由 09b-12 覆盖；v2 按缺口 12 拆开） | L2·S3 | |
| 09b-05 | test_audit_bindings_effective_only | 边缘 | `contract_replay` | `fx-redo` | build | `audit_bindings[X] == {1, a1}`；X ∈ `stale_candidates` | L2·S3 | |
| 09b-06 | test_excluded_not_in_inputs_u8 | 非法 | `contract_replay` | `fx-u8` 里选 none 的记录 | build | 该记录在 `excluded` 中且写明原因；不出现在任何输入清单或导出行里 | L2·S3 | |
| 09b-07 | test_pipeline_never_passes_force | 非法 | `contract_replay` | writer 产出的卡过不了门禁 | build | 假 claude 和 klode 的调用日志里都没有 `--force`；build 失败，exit 20 | L2·S3 | |
| 09b-08 | test_build_id_changes_iff_inputs_change | 边缘 | `unit` | hyp：inputs 的任意一个分量变化 | `build_id` | id 变化 ⟺ inputs 变化 | L1·S4 | |
| 09b-09 | test_build_u7_real_klode | 成功 | `cli_integration` | `fx-book-aurora` | 真 klode，writer 用回放 | 13 步全部 PASS；strict 0E | L3·S1 | |
| 09b-10 | test_same_build_concurrent_exit40 | 并发 | `contract_replay` | 同一份 inputs | 两个进程同时 build | 一个 exit 40；只生成一个 `builds/<id>/` | L2·S3 | |
| 09b-11 | test_zero_verified_records_build | 边界 | `contract_replay` | 全部记录都是 disputed | build | **规格待定（§7-F4）**：期望 exit 20，原因 `no_verified_records`；`excluded` 列出全部记录 | L2·S2 | |
| 09b-12 | test_build_tree_fully_readonly | 边缘 | `contract_replay` | `fx-labs-3x` | build 完成后遍历 `builds/<id>/`（除 `.readers/`） | 每个文件和目录都没有写权限位；以写模式打开任意文件都抛 `PermissionError`；在任意子目录新建文件都失败 | L2·S3 | |

### WI-09c：三条回路

**Law**：
- 轮数有上限，且一定会终止（S4）。
- 预算先预留，再派发（S4）。

**替身 / 配对**：回放，配对 00d2-01。**重构敏感点**：断言的是回路记录和花费。
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

**Law**：No law：这是一张判据表。
**替身 / 配对**：无。**重构敏感点**：断言的是 receipt 和退出码。
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
- 崩溃后，`current` 完整地指向一个**已校验**的 build：MANIFEST 存在且校验通过，`check_receipt.status == PASS`，`build_id` 一致，整棵树递归只读（S5；v2 缺口 5，v4 P2-10 加强）。
- 并发 readlink 永远不会读到悬空链接（S4）。
- prune 不移动受保护的 build（S4）。
- 只移动不删除，build 总数守恒（S5）。
- 判断「较新」按 `PUBLISH.log.seq`（v2，缺口 14）。

**替身 / 配对**：无。**重构敏感点**：断言的是指针、日志和目录树。
**并发**：09d-03、09d-11、09d-13。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 09d-01 | test_publish_switches_pointer | 成功 | `contract_replay` | 新增 `fx-publish-ab`（在 tmp 里由回放构建的两个 build） | 发布 B | `readlink(current)` 为 B；`PUBLISH.log` 新增一行，带 seq 和 `target_build_id=B` | L2·S1 | |
| 09d-02 | test_pointer_always_complete | 边缘 | `contract_replay` | `fx-publish-ab`；**崩溃点覆盖 `pipeline --resume` 的 build → check → publish 全段**（包括 chmod 之前、check 之前） | 枚举所有崩溃点，再用 hyp 加随机 SIGKILL，然后 resume | `current` 要么是 A 要么是 B，存在、可解析、不悬空。**v2 加强**：它指向的 build 的 MANIFEST.sha256 校验通过，`check_receipt.status == PASS`，`check_receipt.build_id == basename(target)`；从不指向写了一半或未经 check 的 build | L2·S5 | |
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
| 09d-17 | test_publish_rejects_unverified_target | 非法 | `contract_replay` | 参数化 6 种：① 缺 `check_receipt.json`；② `check_receipt.build_id` ≠ 目标；③ `MANIFEST.sha256` 校验失败；④ 顶层目录可写；⑤ 顶层只读但内部某个文件可写；⑥ 缺 `MANIFEST.sha256` | 发布 | 六种都拒绝；`readlink(current)` 不变；`PUBLISH.log` 不新增 switch 行（v4：增加 ⑤⑥，对应第 2 轮 P2-10） | L2·S3 | |
| 09d-18 | test_skipped_newer_uses_publish_seq | 边缘 | `contract_replay` | 依次发布 A、B（B 的 build_id 字典序更大），再 rollback 到 A；此时登记产物上的标记为 B（seq 更小） | replay A | 该动作被重放，不记 `skipped_newer`（按 seq 判断，不按 build_id） | L2·S3 | |

### WI-11a：MCP 包装

**Law（K7）**：
- 同一个进程里，**不论哪一类请求、哪个工具先到**，切换之后的每次查询都读取当时 `current` 指向的 build。v2 对应缺口 1；v4 把工具范围扩到 11a-04 返回的完整 TOOLS 集合，对应 P2-9。
- `kb_version.build_id` 等于上一次查询实际使用的 build（按内容判断），也等于 readlink 的结果（S4/S5）。
- 读者租约在 `Config.load` 之前写好，prune 期间不影响正在服务的 build（v4，P2-7）。用 audit hook 证明顺序：租约路径上的第一个文件系统变更事件必须存在，早于 `mcp.after_resolve`，也早于第一次打开 build 内的文件（v5 建议 1；v6 把事件泛化，不再绑定「写模式 open」这一种建租约的方式，对应第 4 轮缺口 G）。

**替身 / 配对**：无，用真 klode 0.5.0。**重构敏感点**：断言的是 JSON-RPC 的响应内容和 audit 事件的顺序。
**fixture 要求**：`fx-publish-ab` 的 A 和 B 各带一条独有的标记行；11a-13 另外需要第三个 build C。
**并发**：11a-09、11a-13。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 11a-01 | test_same_process_A_to_B | 边缘 | `cli_integration` | `fx-publish-ab` | 同一个进程里：发布 A → 调 kb_version 和 search/verify → 发布 B → 再查 | build_id 依次为 A、B；查询结果分别对应 A、B 的内容；进程 pid 不变 | L3·S5 | |
| 11a-02 | test_kb_version_equals_readlink | 边缘 | `cli_integration` | hyp：切换序列 × 请求序列。请求类型取自 11a-04 返回的完整 TOOLS 集合（search、verify、zoom 等全部工具，外加 kb_version），切换后的第一条请求可以是其中任何一种 | 每次切换后按生成的顺序发请求 | 每次查询返回的标记行都属于当时 readlink 指向的目标；随后的 `kb_version.build_id` 既等于 readlink，也等于上一次查询实际使用的 build（v4：请求类型扩到完整 TOOLS，对应第 2 轮 P2-9） | L3·S4 | |
| 11a-03 | test_missing_current_iserror | 非法 | `cli_integration` | 没有 `current` | tools/call；然后发布；再调用 | 第一次返回 `isError:true`，进程仍存活；发布后调用正常 | L3·S3 | |
| 11a-04 | test_tools_list_includes_kb_version | 成功 | `cli_integration` | 任意 build | tools/list | 结果等于 klode 的 TOOLS 加上 kb_version | L3·S1 | |
| 11a-05 | test_handle_none_one_response_per_request | 边界 | `cli_integration` | 依次发送 initialize、tools/list、ping | 读 stdout | 每个请求恰好一行 JSON-RPC 响应，id 对得上；没有多出来的 `null` 输出 | L3·S2 | |
| 11a-06 | test_degrade_subprocess_proxy | 边缘 | `cli_integration` | 用影子包让 `klode.lib.mcp_server` import 失败 | 跑 11a-01 的 A→B 流程 | 仍然成立 | L3·S3 | |
| 11a-07 | test_degrade_cli_mode | 边缘 | `cli_integration` | 两级 import 都失败 | A→B | 仍然成立；输出文本与正常模式一致 | L3·S3 | |
| 11a-08 | test_lease_cleanup_on_switch_and_exit | 边缘 | `cli_integration` | `fx-publish-ab` | 切换后关闭 stdin | 切换后 A 的租约消失；EOF 后所有租约都消失（v4：删去 v2 加的两句在本用例里恒为真的断言，「先写租约再 load」改由 11a-13 覆盖，对应第 2 轮 P2-7） | L2·S3 | |
| 11a-09 | test_switch_mid_call_uses_old | 并发 | `cli_integration` | 一次慢查询（在大库上 search） | 查询进行时切换 | 这次调用的结果来自 A；下一次来自 B | L3·S3 | |
| 11a-10 | test_malformed_jsonrpc | 非法 | `cli_integration` | 一行垃圾数据 | 发送 | 返回 error 响应；进程仍存活，下一个请求正常 | L3·S3 | |
| 11a-11 | test_query_first_after_switch_serves_new_build | 边缘 | `cli_integration` | `fx-publish-ab`；参数化：三种模式（正常、降级①、降级②）× 切换后第一条请求所用的工具（11a-04 返回的 TOOLS 中除 kb_version 外的每一个） | 先在 A 上查询，然后发布 B；**切换后的第一条请求就是该工具**（之前不调 kb_version，也不调 tools/list） | 结果只含 B 独有的标记行，不含 A 独有的；之后再调 kb_version，`build_id == B`（v4：第一条请求覆盖全部工具，对应第 2 轮 P2-9） | L3·S5 | |
| 11a-12 | test_notification_no_response | 边界 | `cli_integration` | 已经 initialize | 发送 `notifications/initialized`（不带 id） | stdout 没有新增任何行；下一个请求仍然正常返回 | L3·S2 | |
| 11a-13 | test_lease_before_load_under_prune | 并发 | `cli_integration` | `fx-publish-ab` 再加第三个 build C，`current` 指向 B；`KLODE_KIT_TEST_SEAMS=1`、`KLODE_KIT_PAUSE_AT=mcp.after_resolve`，使用独立的 `KLODE_KIT_PAUSE_DIR`。<br>**audit（v5 加，v6 泛化）**：包装子进程安装 `sys.addaudithook`，记录：目标路径位于租约目录内、且与 B 对应的所有文件系统变更事件（写模式 `open`/`os.open`、`rename`/`replace` 的目标、`mkdir`、`symlink`）；`klode_kit.crashpoint` 事件；每一次打开 `builds/B/` 内文件的 `open` | 对 B 发一条查询；等 `mcp.after_resolve.reached` 出现后，发布 C 并运行 `--prune --keep 0`，然后放行 | `reached` 必须出现，否则判 FAIL；这次请求不报错，并返回 B 的标记行；B 不在 `_trash` 里，在持有租约期间从未被移走；请求结束后，B 的租约被清理。<br>**audit**：租约路径上的第一个文件系统变更事件**必须存在**（不存在判 FAIL），它早于 `mcp.after_resolve`，也早于第一次打开 `builds/B/` 内的任何文件。断言不绑定建租约的具体方式：直接写、临时名加 rename、mkdir、symlink 都可以。<br>（v5：对应第 2 轮 P2-7、第 3 轮建议 1。v6：事件泛化并要求非空，对应第 4 轮缺口 G） | L2·S3 | |

### WI-11b：登记器 + `u5_ready` 双探针【宿主探针 G2；真实项目 G3】

**Law**：
- 合并时只改自己的键（S5）。
- upsert 幂等（S5）。

**替身 / 配对**：无。**重构敏感点**：断言的是合并后的内容。
**G3 守卫**：所有用例都在 `HOME=tmp`、`KLODE_REGISTRY=tmp` 下运行。
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

**Law**：verifier 不改 status（S3；跨阶段的约束见 08c-16）。
**替身 / 配对**：回放，配对 12-04。**重构敏感点**：断言的是 frontmatter 和字节。
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

**Law**：日期别名稳定（S5）。
**替身 / 配对**：回放，配对 06-11/12。**重构敏感点**：断言的是 stdout、退出码和链接。
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

**Law**：No law。
**替身 / 配对**：无。**重构敏感点**：断言的是文本。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 13a-01 | test_skills_static | 成功 | `unit` | `skills/*/SKILL.md` | 解析 | 正文都调用 `${CLAUDE_PLUGIN_ROOT}/bin/klode-kit`；白名单是 `Bash(*/bin/klode-kit *)` | L1·S2 | |
| 13a-02 | test_skills_no_business_logic | 非法 | `unit` | 同上 | 扫描 | 没有 `python -c`，也没有内联脚本块 | L1·S3 | |
| 13a-03 | test_known_pitfalls_and_iron_rules | 边界 | `unit` | 同上 | 计数 | A11 的 12 条已知坑和 A8 的 2 条铁律都在 | L1·S2 | |
| 13a-04 | test_kb_check_entry | 成功 | `host_acceptance` | `fx-labs-3x` | `claude -p --plugin-dir .` | `plugin.root_sha == release_sha` | L3·S1 | G2 G5 |

### WI-14：Workflow 薄壳【G2】

**Law**：No law。
**替身 / 配对**：`agent()` 桩，配对 14-04。**重构敏感点**：断言的是阶段序列。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 14-01 | test_workflow_static | 非法 | `unit` | `workflows/kb-pipeline.js` | 扫描 | 没有 `Date.now`、`Math.random`、`require('fs')`、`child_process` | L1·S3 | |
| 14-02 | test_one_agent_per_stage_with_schema | 成功 | `unit` | 同上 | 解析 | 每个阶段恰好一次 `agent(…, {schema})` | L1·S2 | |
| 14-03 | test_stage1_ends_at_audit | 边界 | `cli_integration` needs_node | `agent()` 桩在 audit 阶段返回 exit 10 | 用 node 运行脚本 | 阶段序列为 preflight、ocr、audit，然后结束 | L5·S2 | |
| 14-04 | test_workflow_two_stage | 成功 | `host_acceptance` | `fx-u3-mixed` | 在宿主里实跑 | 两段都通过；headless 不可行时降级为用户观察项 | L3·S1 | G2 G5 |
| 14-05 | test_resume_sequence | 边界 | `cli_integration` needs_node | 带 `args.resume` | 用 node 运行 | 阶段序列为 writeback、build、check、publish | L5·S2 | |

### WI-15a：打包与安装【G4】

**Law**：重装幂等（S5）。
**替身 / 配对**：无。**重构敏感点**：断言的是启动器的输出。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 15a-01 | test_install_outside_repo | 成功 | `cli_integration` | tmp HOME + tmp cwd | 运行 `scripts/install.sh`，再 `--version` | 输出包版本 | L3·S1 | G4 |
| 15a-02 | test_reinstall_idempotent | 边缘 | `cli_integration` | 已经装好 | 再装一次 | `uv.lock` 不变；`--version` 仍然正常 | L2·S5 | G4 |
| 15a-03 | test_klode_import_smoke | 成功 | `cli_integration` | 已经装好 | `python -c "import klode.lib"` | exit 0；`klode.__version__ == "0.5.0"` | L3·S1 | |
| 15a-04 | test_launcher_python_override | 边界 | `cli_integration` | 设置 `KLODE_KIT_PYTHON` | 启动器 | 用的是指定的解释器（看输出的 `sys.executable`） | L3·S2 | |
| 15a-05 | test_missing_venv_message | 非法 | `cli_integration` | 没有 `.venv` | 启动器 | exit 40，提示先运行 install.sh | L3·S3 | |

### WI-15b：`release_manifest` + `release_check`

**Law**：sha 变化 ⟺ 清单内的文件发生变化（S4/S6）。
**替身 / 配对**：时钟注入。**重构敏感点**：断言的是 sha 和退出码。
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

**Law**：No law。
**替身 / 配对**：无。**重构敏感点**：断言的是文档与文件系统的对应关系。
**并发**：不适用。

| ID | 测试名 | 类 | lane | fixture | 操作 | 断言（行为级） | L·S | 闸门 |
|---|---|---|---|---|---|---|---|---|
| 30-01 | test_doc_paths_exist | 成功 | `unit` | `docs/*.md`、`README_v2.md` | 抽取其中的命令和路径 | 每一个都真实存在 | L2·S2 | |
| 30-02 | test_docs_no_private_ids | 非法 | `unit` | 同上 | 扫描 | 不含私有项目的编号或路径 | L1·S3 | |
| 30-03 | test_readme_original_untouched | 边缘 | `unit` | git HEAD | 比对 | `README.md` 的 sha 与 HEAD 中的一致；`README_v2.md` 存在（拍板 9 和全局规则） | L2·S3 | |
| 30-04 | test_directory_doc_covers_top_dirs | 边界 | `unit` | `docs/目录结构.md` | 与顶层目录比对 | 每个顶层目录都有说明 | L1·S2 | |

### WI-31：U1–U8 验收套件【含 G2】

**Law**：来源链在真实产物上同样成立（S5）。
**替身 / 配对**：replay 版本的配对，就是同一张表里的 cli、model、host 版本。**重构敏感点**：只走用户入口。

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

| U | 计划 §3 的行 | 用户能直接看到的东西 | 覆盖用例（主；补强） | lane | 闸门 |
|---|---|---|---|---|---|
| U1 | `test_u1_med_intake.py`（contract_replay） | stdout 两行原文、exit 10、awaiting_confirm、receipt 里四路的耗时 | U1-01；U1-02（重跑幂等）、U1-03（分歧数不写死）、UC-01（并发）、13b-01/02、08c-18/20 | contract_replay | — |
| U2 | `test_u2_kb_ocr_rotated.py`（contract_replay；cli 版带 macos） | rotate 90、`_masked/` 抬头全黑、对照图在 `_mask_review/`、bbox_px 为 4 个整数 | U2-01、U2-02；U2-03（未打码的图永不外发）、04b-01、05a-01/07、05b-08/15/16 | contract_replay、cli_integration macos | G4（U2-02） |
| U3 | `test_u3_pipeline_first_stage.py` + `test_u3_workflows_host.py` | records 文件为 pending_confirm、`uncertain: []`；无分歧页为 verified；`/workflows` 里可见 | U3-01、U3-03；U3-02（K1 metamorphic）、08c-10/17、07b-04、08d-03、00d2-03/04 | contract_replay、host_acceptance | G2 G5（U3-03） |
| U4 | `test_u4_guide_pdf.py`（cli_integration） | text_layer、routes_skipped、13 步全部 PASS、`OK: 0 errors, 0 warning(s)` | U4-01；U4-02、04c-04、09b-01 | cli_integration | — |
| U5 | `test_u5_verify_cli.py` + `test_u5_expert_qa.py` | verify 返回 VERIFIED、包装的结果一致、回答逐条带锚点、问到库里没有的内容时答「查不到」 | U5-01、U5-02、U5-03；01a-10（人工改值后，旧值 NOT FOUND）、11a-01、11a-11、11a-13、11b-07/09 | cli_integration、model_integration | G2 G5（U5-02/03） |
| U6 | `tests/replay/test_u6_redo_keeps_verified.py` | 新 fp 目录、旧目录不动、fixes 只追加、verified 保持到点选为止 | U6-01；08d-06、04b-06、08c-09、09b-05 | contract_replay | — |
| U7 | `test_u7_book_build.py`（cli，writer 用回放） | 拼回 == 原文、3/3、0E、5/5、3 张卡、`.mcp.json` 条目、smoke 6 项 | U7-01；U7-02、02a-01、09b-09、02d-06 | cli_integration | — |
| U8 | `test_u8_confirm_writeback.py`（contract_replay + cli needs_node） | 小图 + 四路候选 + ★；写回后为 verified；选「都不对」的为 disputed、`p002-l07`、不进 build 输入 | U8-01、U8-02、U8-03；U8-04、08b-01/09、08c-11/18/19/20、00b1-14、09b-06 | contract_replay、cli_integration needs_node | — |

U1–U8 每一条都至少有一个从用户入口发起的用例。

---

## 7. 缺口与规格问题

| # | 问题 | 处理 |
|---|---|---|
| F1 | r4.3 用 `startswith` 判断「在项目内」：前缀相同的兄弟目录会被误判，符号链接也能绕过 | 00c1-06/07 |
| F2 | 版本号按字典序比较 | 00c1-11（claude）、00c1-20（codex） |
| F3 | 重复图片时 `batch_id` 怎么算未定义 | 00b2-02，规格待定 |
| F4 | 以下情况在 02/04 中都没有规定：空 inbox、非图片文件、零 verified 的 build、同一日期的第二批、desk 的 tests 为空 | 04b-10/11、09b-11、13b-05、02d-05，均为规格待定 |
| F5 | auto-agree 原来只在 compare 层做了性质测试 | 08a-07、08c-10（v4：由生成器真值判定；v5：按同值段判定是否被触及，并加 `@example`；v6：加计数约束）、U3-02、08c-17、07b-04 |
| F6 | 伪造的 choices 能否绕过页级 uncertain | 08c-11 |
| F7 | writeback 是否是唯一写 status 的地方 | 08c-16、12-03 |
| F8 | 发送存在 TOCTOU | 05b-08（v4 加 reached 门槛）、05b-16（逐个暂停点；v5 用 audit 证明暂停点位置；v6 加「校验前源文件只读打开一次」） |
| F9（v4 已决定） | 测试缝与 policy 第 13 条的张力，以及生产环境误设变量的风险 | **主会话决定**：<br>• 生效条件：只有 `KLODE_KIT_TEST_SEAMS=1`（仅由 conftest 设置）且 `KLODE_KIT_REAL != 1` 时，测试缝才生效。<br>• 被忽略时：stderr 输出警告；doctor 报缺项并 exit 40（只有 doctor 以 40 退出，运行本身照常继续）。<br>• `PAUSE_AT` 已设而 `PAUSE_DIR` 未设时，必须拒绝，不退回全局目录。<br>• 暂停点清单写死在 §0.3。<br>对应用例：00s-14、00s-15、10b-08、00s-16 |
| F10 | 生成确定性的测试放在 unit lane，在 Linux 上只能 skip | 移到 cli_integration 的 macos 子集 |
| F11 | 预算恰好等于上限时，是否算「超过」 | 06-07 |
| F12 | R-2（对照图放在 `_mask_review/`）待用户确认 | U2-01 |
| F13 | WI 总数是 53 还是 52 | 差的是 WI-17，已放入 Deferred |
| F14（v2） | **choices 只覆盖部分字段时怎么处理**（第 1 轮缺口 4） | **规格待定（推荐）**：exit 30，列出缺失的 field_id，零写入。由 08c-18 断言；主会话确认后写入 02 r2.3 的写回校验 |
| F15（v2） | **env 白名单** `ANTHROPIC_*` 会放行 `ANTHROPIC_BASE_URL` | 设计层收窄为显式键 `PATH HOME TMPDIR LANG ANTHROPIC_API_KEY`。00c1-08 把 `ANTHROPIC_BASE_URL` 列为非法样例；05b-15（v4）断言实际执行时的 env 也满足这个白名单 |
| F16（v2） | 05b-08 改了名字和断言方式。按 policy 第 12 条，改断言要说明理由 | v1 的写法靠线程时序制造竞态，等于随机放过缺陷，所以 v2 改为用 `KLODE_KIT_PAUSE_AT` 固定窗口。v4 再加上 reached 门槛：暂停点没被触发时用例判 FAIL，不能走成功分支。到目前为止还没有任何实现，所以这次替换不影响任何 red receipt |
| F17（v4） | **重复的 field_id**（第 2 轮 P2-8） | 两层都拒绝：schema 层由 00b1-14 覆盖；writeback 层由 08c-20 覆盖（绕过 schema 直接交给 writeback 时，exit 30，零写入） |
| F18（v5） | **08c-10 的真值谓词在跨记录的同值连续段上不可满足**（第 3 轮缺口 D） | 「被触及」改为按整个同值连续段判定，与 07b-12 一致；与这类段相交的记录只要求单向蕴含（verified ⟹ 值等于真值），其余记录仍要求双向。这项修改属于修正规格自身的不可满足性，不是放宽断言。仍然可以挡住第 2 轮的「按页判断」攻击，因为那种攻击不涉及同值段。v6 再为被触及的段加一条计数约束，恢复全链上对「忽略 None、其余三路一致就 verified」的覆盖（第 4 轮建议 1） |
| F19（v6） | **audit 断言的两类通病**（第 4 轮缺口 E、F、G）：① 只盯一种事件形态（如「写模式 open 某个文件名」），正确实现换一种写法（临时名加 rename、mkdir、symlink）就会让断言空成立或误判红；② 事件不限路径范围，解释器自身的写入（pyc、`/dev/null`、私有 tmp）会让正确实现时红时绿 | 统一写法：<br>• 观察「目标落在某个路径范围内的全部文件系统变更事件」，不绑定具体写法；<br>• 凡是「早于 / 晚于」类断言，都同时要求被观察事件**至少出现一次**；<br>• 持久写只计持久化根目录之下的事件，排除 devnull、`__pycache__` 和私有 TMPDIR，子进程设置 `PYTHONDONTWRITEBYTECODE=1`。<br>落在 05b-16、00s-16、11a-13；`crashpoint()` 无条件发出 audit 事件，见 §0.3 |

需要新增的 fixture：
- `fx-corrupt-pdf`
- `tests/fixtures/replay/probe-synth/*.jsonl`
- `fx-publish-ab`：在 tmp 中构建，A 和 B 各带一条独有的标记行；11a-13 另需第三个 build C
- `fx-approx-only`
- `fx-chain` 下的 `legal-{manual,dateshift,alias,oldbuild,pdftext_dpi}` 和 `broken-7…12`

以上入库的文件都要登记进 MANIFEST。

---

## 8. 统计

**总数：517**（与 v5 相同；v6 没有新增用例，只在原 ID 上修改了 4 条，类、lane 和 L·S 都没有变）

| lane | 数量 |
|---|---|
| `unit` | 261 |
| `contract_replay` | 172 |
| `cli_integration` | 60 |
| `model_integration` | 13 |
| `host_acceptance` | 9 |
| `manual` | 2 |

| 五类 | 数量 |
|---|---|
| 成功 | 119 |
| 边界 | 91 |
| 非法 | 135 |
| 边缘 | 147 |
| 并发 | 25 |

| 里程碑 | 用例 | unit | contract_replay | cli | model | host | manual |
|---|---|---|---|---|---|---|---|
| M0 | 142 | 90 | 35 | 5 | 7 | 5 | 0 |
| M1 | 151 | 69 | 71 | 9 | 2 | 0 | 0 |
| M2 | 159 | 80 | 47 | 31 | 1 | 0 | 0 |
| M3 | 65 | 22 | 19 | 15 | 3 | 4 | 2 |

需要手动闸门的用例（v6 没有变化）：

| 闸门 | 用例 |
|---|---|
| G1 | 19-01、19-02 |
| G2 | 00c2-06/07/08/09、00c3-09、00d2-01/02/03/04/07、06-11/12、11b-09、12-04/05、13a-04、14-04、U3-03、U5-02/03，共 20 条 |
| G3 | 自动用例都不会碰；11b-10 断言不触及真实路径 |
| G4 | 04a-10/11/12、05a-07、15a-01/02、U2-02 |
| G5 | 00d2-03/04/05/06/07、11b-09、12-05、13a-04、14-04、U3-03、U5-02/03 |

---

## 9. v6 变更 ID 清单与第 4 轮对照

**与 v5 相比修改的行（4 条，ID 不变，内容不同）**：00s-16、05b-16、08c-10、11a-13

**新增的行**：无

不在修改清单里的 513 行，都应与 v5 逐字节相同。

**非用例行的改动**（不进入下面的逐行比对）：
- 头部说明；
- §0.3：`crashpoint()` 无条件发出 `sys.audit`；位置约束 ①②③ 与 mcp 侧的泛化；参数与清单一一对应；循环内的点只算一个名字；
- §1 中的 K1、K3、K5、K6；
- WI-00s、WI-05b、WI-08c、WI-11a 的 Law 段；
- §7 中的 F5、F8、F18，以及新增的 F19；
- §8 的说明文字；
- §9；
- Deferred。

**机械校验命令**（在仓库根目录运行，期望输出 `mismatches: 0`）。v6 分为两个文件，脚本会把两部分合在一起读：

```
python3 - <<'EOF'
import re
P='.claude/tdd-guardian/'
row=re.compile(r'^\| ([0-9A-Za-z]+-[0-9]{2}) \|')
def rows(*fs):
    d={}; dup=[]
    for f in fs:
        for l in open(P+f,encoding='utf-8'):
            m=row.match(l)
            if m:
                if m.group(1) in d: dup.append(m.group(1))
                d[m.group(1)]=l.rstrip('\n')
    return d,dup
v5,_=rows('test-matrix-20261022-klode-kit_v5.md')
v6,dup=rows('test-matrix-20261022-klode-kit_v6.md','test-matrix-20261022-klode-kit_v6_part2.md')
changed=set('00s-16 05b-16 08c-10 11a-13'.split())
new=set()
bad=[k for k in v5 if k not in changed and v5[k]!=v6.get(k)]
extra=sorted(set(v6)-set(v5)-new); missing=sorted(new-set(v6))
print('v5',len(v5),'v6',len(v6),'checked',len(v5)-len(changed),'mismatches:',len(bad),bad,'unexpected_new:',extra,'missing_new:',missing,'dup_ids:',dup)
EOF
```

| 第 4 轮项 | 优先级 | 关闭它的用例 / 位置 | L·S |
|---|---|---|---|
| E：送出的字节与校验的字节可以来自源文件的两次不同读取（K6） | P0 | 修改 05b-16 audit 运行：<br>• 新增 ③：`send.after_source_check` 之前，源文件恰好被读模式打开 1 次，且发生在 `send.after_source_read` 之前；校验前没有 argv 含源路径的子进程。<br>• 修改 ②：观察临时目录内的全部变更事件，包括 rename、replace、link、symlink 的目标，不含建临时目录本身的 mkdir；要求至少出现一次，并且全部在源校验之后。<br>§0.3 同步 | L2·S4 |
| F：00s-16 的事件全集不限路径，正确实现也满足不了（K3/K5/K8） | P1 | 修改 00s-16：<br>• 「持久写」限定为目标在数据项目根、`work/`、`builds/`、receipts 之下的事件；<br>• 排除 devnull、`__pycache__` 和私有 TMPDIR；<br>• 子进程设置 `PYTHONDONTWRITEBYTECODE=1`；<br>• 每段至多一次持久写的断言不变 | L2·S4 |
| G：11a-13 的租约断言绑定在「写模式 open 租约文件」上（K5） | P2 | 修改 11a-13 audit：<br>• 改为「租约目录内与 B 对应路径上的第一个文件系统变更事件」，可以是 open-w、os.open、rename/replace 目标、mkdir 或 symlink；<br>• 该事件必须存在，并且早于 `mcp.after_resolve`，也早于第一次打开 `builds/B/` 内的文件 | L2·S3 |
| 建议 1：08c-10 的单向蕴含放得过宽 | 建议 | 修改 08c-10：对每个被触及的同值段，属于 verified(auto) 记录的行数 ≤ 该段在各路中实际出现的行数的最小值 | L2·S4（不变） |
| 建议 2：00s-16 的清单相等是按参数对应还是取并集 | 建议 | 修改 00s-16：按参数一一对应，writeback↔08c-13、publish 全段↔09d-02、receipts↔00b1-11、run_batch↔04b-13，不取并集；§0.3 同步 | — |
| 建议 3：`crashpoint()` 是否发出 audit 事件 | 建议 | §0.3：`crashpoint()` 始终发出 `sys.audit("klode_kit.crashpoint", name)`，与测试缝是否生效无关；00s-16、05b-16、11a-13 的 fixture 都改为记录这个事件 | — |
| 建议 4：循环里的 crashpoint | 建议 | §0.3 注释：循环点在清单里只算一个名字；09d-02、08c-13 的枚举只在第一次经过时触发，后续迭代交给随机 SIGKILL | — |
| 建议 5：klode 子进程的写入 hook 看不到 | 建议 | 00s-16 加盲区说明：本用例对这部分写入不构成证明，K5 由 09d-02、09d-17「只指向已经过 check 的 build」兜底 | — |

## Deferred

- WI-17（ask-arbiter，可选）：没有 lane，也没有自动测试，由人工核对。
- 计划中 Deferred 清单里的各项一律不设计测试。
- mutation 只作为对 criticalPaths 的度量，不另设用例。
- 第 3 轮建议 2（恢复过程中再次崩溃）和建议 4（F9 残余风险的 doctor 提示）：按主会话指示，不纳入。
- klode 等外部子进程内部的写入顺序：audit hook 观察不到（第 4 轮建议 5）。不另设用例，K5 由 09d-02 和 09d-17 在结果层兜底。
