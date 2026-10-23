# 阶段 5 实现偏离记录（主会话审核）

| WI | 偏离 | 主会话处置 |
|---|---|---|
| WI-00s | 计划 lane 命令里的 `-p pytest_socket` 与 entry point 自动加载重复注册而崩溃，去掉，保留 `--disable-socket` | 接受（计划笔误） |
| WI-00s | 00s-11 原断言「任意插入后命中数单调 +1」在把已有命中劈开时不可满足，改为 `after ≥ before + 1 − 跨插入点的命中数`，并以 `@example` 固定反例；在 red 记录前修改，不涉及已记录断言 | 接受（修正规格不可满足性，同 F18 性质） |
| WI-00s | 00s-14/15 也采用「合成流程 PASS + 真实流程 strict xfail」，因 send/writeback 尚不存在 | 接受；真实流程由 WI-05b/08c 完成后 XPASS 强制去掉 xfail |
| WI-00s | 自定行为：暂停点只首次经过生效；PAUSE_DIR 不存在也 exit 2；hook 无法解析目标路径的写入按持久写计（保守）；启动器 venv 异常 exit 40 | 接受 |
| WI-00s | 执行过 `rm -rf work/lanes work/coverage`、`rm -f .coverage`（本会话生成、已 gitignore、可重建的产物） | 已向用户如实报告；后续派工明确要求改为移入 `_trash/` |
| WI-00s | `criticalPaths` 暂空，由建文件的 WI 补条目 | 接受；WI-15b release_check 时核对齐全 |
| WI-00a | `test_manifest_complete` 比较 `sorted(entries)`（字符串序）与 `tree_files`（Path 序），`X.ext` 与目录 `X/` 并存时顺序不同而误红；实现者未改已记录断言，改 fixture 目录名 `derived/`→`derived_img/` 规避 | 暂接受规避；修断言（改为 `sorted(files)`）属改已记录行，留待下次向用户批量确认 |
| WI-00a | privacy_scan 的 SKIP_DIRS 加 `_trash`（gitignored，CI 不可见） | 接受 |
| WI-00a | 新增 gen_common/gen_chain/gen_project；fixture 各产物格式为暂定，WI-00b1/00b3/00c2 可调整后重生成 | 接受；对应 WI 派工时提醒 |
| WI-00a | `repeat_ratio` 行级定义为自定，WI-04a 与 189 MLX 检测对齐 | 接受；WI-04a 派工时提醒 |
| WI-00a | 新增 pyproject `[tool.pyright]`（主会话，消 IDE 误报） | — |
| WI-00b1 | **SEPARATION-BROKEN**（unit）：00b1-12 往返性质测试把目标路径 `rt/<名>.json` 改为 `rt/<名>/<名>.json`（hypothesis 复用 tmp_path 致「目录只含目标文件」不可满足），断言行未改 | **用户 2026-10-22 AskUserQuestion 认可** |
| WI-00b1 | schema 示例 `vision_tokens.json` 曾改名，已原名恢复 | 接受 |
| WI-00b1 | 自定 13 步名：export, init_kb, ingest, split_manifest, klode_build, dispatch_writers, gate1, write_card, check_strict, desk_apply, desk_verify, gen_agent, self_check；writer_receipt 字段集；check/publish 状态枚举；fixes.manual 必含 evidence.region 等额外 schema 规则 | 接受；WI-09b 实现时以此为准 |
| WI-00b1 | lane_receipt.release_sha 暂为非空字符串（git HEAD 或 unversioned），正式定义归 WI-15b | 接受 |
| WI-00a（追认） | 00a-03 断言 `sorted(entries) == files` 改为 `sorted(entries) == sorted(files)`（主会话改，已记录行） | **用户 2026-10-22 AskUserQuestion 认可** |
| WI-00b2 | config 做成包 `src/klode_kit/config/{__init__.py,defaults.toml}`（计划写 `config.py` + 根目录 `config/defaults.toml`，二者不能干净共存）；R3-08 的 `config/**` 由 `src/**` 覆盖 | 接受 |
| WI-00b2 | 自定键公式（doc_id、rec_id、batch_id、decision_id=`<audit_rev>/d<NNNN>`、commit_id、幂等键、cfg_fp 输入、attempt 目录 `<batch>/ocr/<engine>/<cfg_fp>.t<seq>`）；CLOUD_ENV_ALLOWLIST 为代码常量不可被配置放宽 | 接受；与 WI-00a fixtures 一致 |
| WI-00b2 | red 为 opaque-failure（receipt.js 解析不到计数，lane 输出含覆盖率段）；首次被判 runner-missing 因缺 defaults.toml，补空占位后重录 | 接受（MEDIUM，不影响 HELD） |
| WI-00b3 | fx-chain 每个变体只有一条记录，00b3-12「随机挑记录」退化为挑字段×路×不变量；混淆记录的校验器缺陷挡不住 | **待补**：WI-31 的 `test_provenance_roundtrip_on_pipeline_output` 必须用含 ≥2 条记录/页的产物（fx-u1-3labs 有 3 条记录的那页），并加一条「两条记录互换字段值 → 违例」用例；WI-31 派工时写明 |
| WI-00b3 | 自定规则：生效决定取 `rev_new ≤ R` 中最大者；未登记 der_sha256 归 der_parent_mismatch；pdftext dpi 容差 ±1px；value_audited 比对 field_ids 中第一个 .value 字段；新增 12 个违例码（共 23） | 接受 |
| WI-00b3 | 覆盖率按 lane 单看 contract_replay 77%、合并 100%；coverage_gate 必须用合并报告 | 接受；WI-15b release_check 用合并报告 |
| WI-00b3 | criticalPaths 补 `contract/ids.py`（主会话补，WI-00b2 漏） | — |
| WI-00c1 | CLI 版本下限做成代码常量（claude ≥ 2.1.259 据 cli-reference `--permission-prompts`；codex ≥ 0.163.0 暂定），不进 defaults.toml，防项目覆盖放宽 | 接受；WI-00c2 实测可上调 |
| WI-00c1 | 审查比 r4.3 更严：`-p` 必含、未知 flag 一律 FAIL（argv:closed）、补 cwd:system_tmp / cwd:mode_0700 / guard:on_path；codex 加 `--skip-git-repo-check`、`--json`、`-i`、`--output-schema` | 接受；WI-00c2 实跑确认 |
| WI-00c1 | `receipt_still_valid(receipt, env)` 签名与矩阵 `receipt_still_valid(env)` 不同；guard 日志在 `<send dir>.guard.jsonl`，WI-05b 负责搬入 receipt 目录 | 接受；WI-05b 派工时写明 |
| WI-00c1 | 子代理工具集无 cc-doc-search MCP，直接只读本地文档镜像原文并附官网 URL；⑫ `--setting-sources ""`、⑭ `--restricted` 下 `--settings` hook 是否触发：文档检索无结果，留 WI-00c2 实测 | 接受 |
| WI-00c2 | 子代理原地改 README（Layout 加 docs/spikes 行）→ 主会话恢复原 README，diff 存 `_trash/wi-00c2-readme/README.diff`，由 WI-30 写入 README_v2.md | 按用户「单文件更新另存」规则处理 |
| WI-00c2 | **BLOCKED**：Claude 路宿主隔离成立（两 canary 均 hook+permission_denials 拒绝、无 nonce）但 receipt FAIL：macOS OAuth 读 Keychain 需 `USER`，不在 r5 env 白名单；Codex 路 `codex exec -s read-only` 读到项目与 $HOME canary → FAIL。两路 routes_enabled=false，真实数据路径关闭 | 待用户决定（见会话 AskUserQuestion） |
| WI-00c2 | 发现 WI-00c1 缺陷：`--json-schema` 产生 StructuredOutput 工具调用，guard（matcher `*`、无 file_path 一律 exit 2）把每次结构化输出都拒掉 | 需修复（新 WI-00c1b，带复现测试） |
| WI-00c2 | ⑫ `--setting-sources ""` 合法且不加载任何来源；⑭ `--restricted` 下 `--settings` 注入的 hook 会触发；R4-02 祖先/项目上下文不加载（输入 token Δ=0，阳性对照 Δ=+8841）；user memory 只有旁证（残余风险 RR-1） | 记录；详见 docs/spikes/WI-00c-isolation.md |
| WI-00c2 | 费用：Claude ≈ $3.16（订阅计）；Codex 8 次 ≈ 20.2 万输入 token | 在授权内 |
| WI-00c2 决定 | **用户 2026-10-23 AskUserQuestion**：① `USER` 加入 CLOUD_ENV_ALLOWLIST（只供本地 claude 读 Keychain）；② Codex 路先试更强沙箱（codex permissionProfile 受限读取 → 不行再 macOS sandbox-exec 只许读发送目录），期间保持关闭，仍不过再问用户；③ Claude 版本下限上调 2.1.285、cloud_settings 加 `autoMemoryEnabled:false`（允许修改 00c1-11 已记录边界断言与 CLOUD_SETTINGS_SHA256） | 新 WI-00c1b（修复+Claude 复测）、WI-00c2b（Codex 沙箱 spike） |
| WI-00c1b | 按用户批准修改已记录断言：test_config 允许集加 USER（LOGNAME 仍拒）；test_cloud_isolation ALLOWLIST 加 USER；00c1-11 边界改为 2.1.284 FAIL / 2.1.285 PASS；test_cloud_settings_file_content 加 autoMemoryEnabled is False。矩阵 v8 的 00c1-08、00c1-11 文字以此为准 | **用户 2026-10-23 批准** |
| WI-00c1b | guard 对 `tool_name == "StructuredOutput"` 放行（仍受 cwd/事件检查），其余无 file_path 工具仍 exit 2；Claude 复测 PASS（$0.29），routes_enabled.claude = true；isolation receipt 脱敏副本存 docs/spikes/receipts/ | 接受 |
| WI-00c1b | autoMemoryEnabled:false 无独立阳性对照（只证明无回退）；model 测试按 WI 常量计预算，后续 WI 需更新 | 残余风险 RR-1 仍在 |
| WI-00c2b | Codex 沙箱 spike：方案 A（codex permission profile，`-c default_permissions` + `:minimal` + 发送目录只读，去掉 `-s`）宿主层面拦住项目/HOME canary、凭据、仓库读取；但该 profile 下模型从不尝试读 canary（9/9），模型探针拿不到证据。方案 B（sandbox-exec 外包）macOS 不允许嵌套沙箱，不可行。另发现：codex 默认把 ~/.codex/skills 目录发给模型（含私有技能描述），`--ignore-user-config` 也不阻止 | 记录；证据在 work/isolation/wi-00c2b-discovery-20261022T225300Z/（gitignored） |
| WI-00c2b 决定 | **用户 2026-10-23 AskUserQuestion**：采用方案 A 并启用 Codex 路；Codex 隔离证据改为「用与生产相同的 profile 跑 `codex sandbox -P`，两 canary 都读不到（exit≠0 且无 nonce）」的确定性宿主检查，不再要求模型探针；同时关闭 skills.include_instructions、features.apps、agents.enabled、features.view_image、features.image_generation、web_search、allow_login_shell，并 fail-closed 检查 ~/.codex/AGENTS.md / AGENTS.override.md 缺失或为空；允许修改 00c1-02「必含 -s read-only」已记录断言 | 新 WI-00c2c 实施 |
| WI-00c2c | Codex 改用 permission profile（去 `-s`，加 3 个 profile 项 + 7 个关闭项），isolation_check 禁 `-s/--sandbox/danger-full-access`，新增 `codex_home:agents_md` fail-closed；宿主检查（`codex sandbox -P`，两 canary exit 1 `Operation not permitted`，阳性对照 exit 0）PASS；生产 argv OCR 1 次通过；routes_enabled.codex = true | 用户 2026-10-23 决定的落实 |
| WI-00c2c | 改已记录断言：00c1-02（删 `-s read-only`、加 profile/关闭项/禁 `-s`）、CODEX_REQUIRED 删 `-s`、CODEX_BAD_VALUES 改为 `-C` 值；00c2-07 改为 skip（主会话改）、00c2-09 只参数化 claude | 视为用户「改用宿主检查、不再要求模型探针」决定的直接后果 |
| WI-00c2c | 残余风险 RR-7 `:minimal` 允许读 /tmp（数据项目放 /tmp 下会被读到）→ 待在 isolation_check 对 /tmp 下项目判 FAIL；RR-8 `codex sandbox` 读 ~/.codex/config.toml；RR-9 若干关闭项在 config-reference 快照中查不到，靠 spike 捕获证明，codex 升级需复测；RR-10 `features.shell_snapshot` 未关 | 记录；RR-7 并入 WI-05b（发送器）派工 |
| WI-00c3 | 四路实跑（7 页合成最难页）：CER vision 0.021 / mlx 0.107 / claude 0.005 / codex 0.005 / tesseract 0.110；错误自动通过 0/64；四路一致 64 ≥ 20；R3-05：表格单元格 69/69 exact（ocrmac observation 框≠词，抬头「标签：值」行跨段） | 记录；docs/spikes/WI-00c-4routes.md |
| WI-00c3 决定（主会话） | ① 录制改放 `tests/recordings/<engine>/`（独立 MANIFEST），不进生成目录，免改 00a-05/00a-02；② Codex effort 锁定 `max`（落实已确认的「最高档不降级」），连带改已记录断言：test_cloud_isolation_edges（max→ultra 作为非法样例）、test_cloud_isolation:280、test_config:42、test_ids:335；③ RR-12：r2.7 前三项阈值按人工确认后的值判定，确认前合并值仅作人工工作量参考（本次 77 字段中 6 个需人工） | 主会话依已确认需求裁定 |
| WI-00c3 | codex effort 闭集仍允许 `minimal`（默认已钉 max，无实际降级）；后续加固：isolation_check 要求 effort == 配置值 | 待 WI-05b/06b 处理 |
| WI-00c3 | 发现 F-1（189 strip_layout_tags 把表压成一行，spike 适配器已修）、F-2 MLX 单位写成 LaTeX、F-3 MLX 印章页按列输出、F-4 Vision 印章压字处误读 | WI-04a 搬 MLX 适配器时带上 F-1 修正 |
| WI-00c3 | 本地 OCR 依赖在独立依赖组 `ocr-local`；不带 `--group ocr-local` 的 `uv sync` 会卸掉它们 | WI-15a install.sh 必须带该组（macOS） |
| WI-00d1 | 桥运行 receipt 外层 `klode-kit.bridge_run/1` 只在代码定义（contract NAMES 被钉死 16 个）；writer 结构化输出过 writer_receipt schema；writer 预算为常量 DEFAULT_MAX_BUDGET_USD=5.0（可覆盖） | 接受 |
| WI-00d1 | 自定：只重试 parse_error/failed；超时 SIGTERM→宽限 2s→SIGKILL；失败尝试的半成品卡移入 `builds/<id>/_quarantine/` 并恢复原内容 | 接受 |
| WI-00d1 | **桥本身无 KLODE_KIT_REAL / G2 闸门**（PATH 上若是真实 claude 就会真调） | **必须补**：WI-00d2 加闸门（非 REAL 或缺 GATE_ACK 时拒绝调用真实 claude，exit 61）并带测试 |
| WI-00d1 | 待 WI-00d2 实测：macOS 默认工具集不含 Glob/Grep（`--allowedTools` 不添加工具）；writer_receipt 的 `(?!...)` 能否被 `--json-schema` 接受；`--agents` 内联过长时改文件形式；提示词里的 `check-cards` 子命令待 WI-09/12 | 记录 |
| WI-00d1 | contract red 首次被判 runner-missing，在有效 red 前把 3 处日志读取改为 `log_entries`（断言含义不变）；edges/agents 测试为实现后补充覆盖，未先跑红 | 接受 |
| WI-00d2 | 补桥的真实调用闸门（非 REAL 或 ACK 不符 → BridgeGateClosed / exit 61，零调用）；宿主实测：写卡 PASS、library Edit 被 permission_denials 拒、两段式 Workflow headless 可行（⑪）、信任探测读 ~/.claude.json hasTrustDialogAccepted（⑬）、`claude mcp get` env 键不影响 connected（⑮）、`/klode-kit:kb-check` receipt 插件字段断言 PASS（R2-11）；费用 $1.50 | 接受 |
| WI-00d2 | Claude Code 2.1.285 的 `--json-schema` 拒绝声明 2020-12 的 schema → 桥传参前去掉顶层 `$schema/$id`（仓库 schema 不变），改已记录断言 00d1-01 | **用户 2026-10-23 批准** |
| WI-00d2 | writer agent tools 补 `StructuredOutput`（`--agent` 下工具受 agent tools 限制）；Glob/Grep 实测存在（与 00d1 记录相反） | 接受 |
| WI-00d2 | 风险 R-1：writer 会话加载用户全局 CLAUDE.md 与用户级 agents/plugins | 交 WI-12（writer argv 加 `--setting-sources`/隔离评估） |
| WI-00d2 | 临时替身命令 `check-cards`（WI-02a 替换）、`kb-check`（WI-13a 补全）、`dev spike-pipeline`；新增顶层 `.claude-plugin/ skills/ workflows/` 尚未写进目录说明 | WI-30 文档补齐 |
| WI-00e | 三张 WUA 图（scenarios 6 场景 / usecases U1–U8 / pipeline 7 阶段、唯一人工「点选」、L1–L3 回路、状态机 extracted→pending_confirm→verified|disputed）；auto-verified 与缺路 disputed 两条直达路径只写在 pipeline.wf.json 的 source 说明（00e-06 要求恰三条边）；00e-10 依赖 001 wfviz 输出不变，wfviz 升级需重渲染 | 接受 |
| 主会话（M0 收尾） | WI-00d2 的 `tests/fixtures/host/`（手工录制 + mini MCP server）移到 `tests/recordings/host/`（同 WI-00c3 方案 B），改 3 个测试文件的路径常量与文档路径；fixtures MANIFEST 重生成、recordings MANIFEST 重写（旧件在 _trash/manifest-fix/、_trash/fixtures-20261023T022211Z/） | 机械搬迁，断言未变 |
| WI-04a | 本机引擎 OcrMac/Tesseract/Mlx 与几何换算搬入；带 F-1 修正（表格逐行）、F-2 LaTeX 展平；退化检测加 189 的 6-gram 信号（>0.5，固定阈值）与行级 repeat_ratio 并用；04a-04/05/06 放 contract_replay（回放录制）；Vision token 全为 approx（livetext 字符框延后）；MLX 多行块 bbox_px=null、保留 block_bbox（F-3 交 WI-07 重建行）；`MlxWeightsMissing` 直接抛出供 doctor 报告；tesseract psm 6 | 接受 |
| WI-04b | run_batch / orient / manifest；新测试缝 `KLODE_KIT_ENGINE_REPLAY`（仅测试模式生效）→ doctor（WI-10b）须把它列入残留测试缝检查；页键 `p<NNN>` 按文件名排序的不重复图位置；批次 receipt `work/<batch>/receipt.json`（schema `klode-kit.ocr_batch/1`，代码内定义）；exit 0=跑完（route 问题记 receipt+stderr）、2=用法（含 no images）、40=配置/锁；不可用的路记 route_failed（route_disabled / route_unavailable）不致命；orient 为左对齐横排文档的启发式；degenerate_output 并入批次 receipt 留给 WI-08a | 接受；WI-10b 派工时写明 ENGINE_REPLAY |
| WI-04b | Pillow 由 dev 组移到运行时依赖（主会话执行 `uv add pillow>=13.0`，G4） | — |
| WI-04b | 已记录测试 test_apply_rejects_non_right_angles 在 `-W error` 下有 ResourceWarning（图像未关闭）；常规 lane 不受影响 | 记录，不改 |
| WI-04c | 文字层分流：乱码字符集面向中日文（Latin-1 字母算乱码 → 走 OCR，安全方向）；渲染 dpi 为代码常量 300（不进 config 以免改 cfg_fp）；所有 PDF 页都渲染（使 pdftext 行的 der_sha256 是已登记衍生图）；PDF 页不转正；坏 PDF 整批 exit 2；批次 receipt 恒带 text_layer / routes_skipped | 接受 |
| WI-04c | **待定**：pdftext 行写在 `work/<batch>/text/<key>.jsonl`（r2.4 / 04c-06），而 fx-chain `legal-pdftext_dpi` 放 `ocr/pdftext/<fp>.t1/`、provenance.Layout 只扫 `ocr/`；pdftext 行无 run_id | WI-07a 派工时决定：provenance Layout 增扫 `text/`（推荐，保持 04c 落盘位置） |
| WI-05a | 打码规则：页顶 header_band 8% 恒涂黑 + 关键词行（CJK 去空格子串、ASCII 整词）+ 标识符模式（`[A-Za-z]{1,3}-?\d{6,}`、`\d{9,}`、0 开头带横线电话）；padding max(4px, 0.3×行高)，full_width；「日期」不作关键词；MANIFEST 多 `path` 字段；对 MANIFEST 文件本身加排他锁；规则变更后旧行 sha 仍在白名单（文件已不在） | 接受 |
| WI-05a | **待接线**：打码图尚未登记进 `work/<batch>/derived.jsonl`（provenance 需要）；kb-ocr 尚未调用 mask_batch；无 `klode-kit mask` 子命令 | WI-05b/06a 派工时写明：云端路只发打码图，打码图须登记为 derived（transform.mask_boxes） |
| WI-05a | WI-05b 白名单 = `{r["der_sha256"] for r in mask.read_manifest(project)}`，并拒绝 `_mask_review/` 下任何路径 | WI-05b 派工时写明 |
| WI-05b | 发送器：路由开关 → `_mask_review` 拒绝 → 源文件读一次进内存 → sha==expect 且在白名单（打码 MANIFEST ∪ fixtures MANIFEST）→ 副本重算 → 审查（effort==配置、项目不在 /tmp）+ 存档 isolation receipt 核对 → 原样执行；exit 60/61 零模型调用；send receipt 不含路径/像素；privacy_guard hook（hooks/hooks.json，PreToolUse Bash，`if` Bash(claude *)/Bash(codex *)）；打码图登记进 derived.jsonl；CI 设 TMPDIR=runner.temp | 接受 |
| WI-05b 决定（主会话） | `isolation_check.check()` 的 `expected_effort` / `refuse_shared_tmp` 默认关、发送器恒开（默认开会翻红 00c1 已记录用例且 Linux CI 的 pytest tmp 在 /tmp）——接受；**send 补 G2 真实调用闸门**（同 bridge）→ 并入 WI-06a | 主会话裁定 |
| WI-05b | 改 WI-00s 已结算测试文件的 xfail 标记（send 已实现，XPASS 强制移除）；05b-07 加严：字节换成另一张已登记图也拒（来源链） | 接受 |
