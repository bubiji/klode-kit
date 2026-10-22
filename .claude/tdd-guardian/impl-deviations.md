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
