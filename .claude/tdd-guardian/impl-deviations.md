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
