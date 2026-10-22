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
