# 计划附录：venv / 路径鲁棒性（2026-10-22 用户要求）

## 起因（前车之鉴）

- **DeanLee2013/project-workflow#180（2026-08-17，16_d 课程库项目）**：项目目录改名后，`.venv/bin/pytest` 等 console script 的 shebang 仍指向旧路径，`bad interpreter`。`bin/python` 是相对软链，照样能跑，所以「python 没坏」是假象；文档里的验收命令 `.venv/bin/pytest -q` 早已失效，没人发现。
- 同类：2026-08-14 目录手动 `mv` 后 MCP 配置里的绝对路径失效，四个 server 断开（`/pfix` SKILL.md 模式 D 记载）。
- 共同点：**坏了但不报错 / 报错被当成别的问题**，以及验收只看「文件在」而不实跑。

## 硬要求（落到 WI-00s、WI-15a、WI-10b、WI-11b）

| # | 要求 | 落点 |
|---|---|---|
| V1 | **永不调用 venv 的 console script**（`.venv/bin/pytest`、`.venv/bin/klode-kit` 之类）。一律 `<venv>/bin/python -m <模块>`；lane runner、CI、文档、SKILL.md 里的命令都按此写 | 00s、15a、30、13a |
| V2 | 启动器 `bin/klode-kit` 运行时用自身真实路径（`realpath "$0"`）定位仓库根和 `.venv`，不写任何绝对路径；`.venv` 缺失或其 python 跑不起来时，**非 0 退出并打印修复命令**（`scripts/install.sh`），不静默回退到系统 python | 00s、15a |
| V3 | **不依赖可编辑安装写进 `.pth` 的绝对路径**：启动器显式设 `PYTHONPATH=<仓库根>/src`；doctor 断言 `klode_kit.__file__` 位于当前仓库根之下，否则 exit 40 并指出「导入的是别处的旧副本」 | 15a、10b |
| V4 | doctor 每次**实跑**而不是看路径：`<venv>/bin/python -c "import klode_kit, klode.lib"`、`klode --version`（真进程）、启动器 `--version`，任何一项失败都 exit 40 | 10b |
| V5 | `.mcp.json` / registry 里登记的启动器绝对路径（WI-11b 不可避免）：`/kb-check` 与 doctor 检查该路径存在且可执行、并实跑一次 `initialize`；目录被改名后给出「运行 `/pfix rename` 或 `bin/klode-kit register --replay`」的提示，而不是静默 disconnected | 11b、10a、10b |
| V6 | `env_fingerprint` / lane receipt 记录仓库根 realpath 与 venv python realpath；与当前不符 → receipt 失效（改名后旧 PASS 不能复用） | 15b、00b1 |
| V7 | 安装脚本幂等：`.venv` 已存在但 python 跑不起来（改名或升级后）→ 自动重建，不要求用户手删 | 15a |

## 新增测试（并入矩阵 v8 对应 WI 实现，ID 以 -V 结尾）

| ID | 测试 | lane | 断言 |
|---|---|---|---|
| 15a-V1 | `test_repo_moved_launcher_still_works` | cli_integration | 把仓库（含 .venv）复制到新目录名下；新位置 `bin/klode-kit --version` 成功；`doctor` exit 0；旧位置删除不影响新位置 |
| 15a-V2 | `test_stale_console_scripts_not_used` | unit | 扫描 lane runner、CI、SKILL.md、docs：不出现 `.venv/bin/<非 python>` 的调用 |
| 10b-V3 | `test_doctor_detects_foreign_klode_kit_import` | cli_integration | 在 PYTHONPATH 前部放一份别处的 `klode_kit` 副本 → doctor exit 40，消息指出导入路径 |
| 10b-V4 | `test_doctor_runs_not_stats` | cli_integration | venv python 存在但不可执行（chmod -x）→ doctor exit 40，且给出 install.sh 修复命令 |
| 11b-V5 | `test_registered_launcher_missing_reported` | contract_replay | `.mcp.json` 里启动器路径不存在 → `/kb-check` 非 0，消息含修复提示，不报「U5 就绪」 |
| 15b-V6 | `test_receipt_invalid_after_repo_move` | unit | receipt 记录的仓库根 realpath ≠ 当前 → release_check 判失效 |
| 15a-V7 | `test_install_rebuilds_broken_venv` | cli_integration | 破坏 `.venv/bin/python` 软链后跑 install.sh → 自动重建并通过 V1 |
