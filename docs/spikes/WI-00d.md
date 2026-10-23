# WI-00d spike：CLI 桥实写一张卡 + Workflow 两段式 + 宿主探测（WI-00d2）

- 日期：2026-10-23；Claude Code 2.1.285（macOS arm64，claude.ai 订阅登录）；闸门 G2 授权 `KLODE_KIT_GATE_ACK=G2-20261023`。
- 只用合成数据：`fx-book-aurora` 第 1 章、`fx-u3-mixed`（只当作 inbox 文件做哈希）、合成 kb `fx-labs-3x`（只有 `builds/<id>/` 与 `current`）、
  `tests/fixtures/host/mini_mcp_server.py`（合成 MCP 服务器）。
- 实验目录：`tests/host/_lab/`（gitignored，位于已信任的仓库根之内，G5 不需要用户再操作）。
- 原始证据（gitignored，含本机路径）：`work/host-spike/<用例>-<时间戳>/`（argv、stdout JSON、stderr、bridge receipt、卡片副本）、
  费用台账 `work/host-spike/costs.jsonl`。
- 用例：`tests/host/test_bridge_spike.py`（model_integration：00d2-01/02 与 argv 探针；host_acceptance：00d2-03..07）。
- 文档出处一律取本地镜像 `claudecodedoc/docs/`，对应官网 `https://code.claude.com/docs/en/<页>`。

## 0. 结论一览

| 问题 | 结论 | 证据 |
|---|---|---|
| 桥的真实调用闸门（WI-00d1 遗留） | **已补**：`claude` 不解析到本仓 `tests/fakes/bin` 时，`KLODE_KIT_REAL!=1` 或 ACK 不是 `G2-<合法日期>` → `BridgeGateClosed`，CLI exit 61，零调用 | `tests/unit/test_bridge_gate*.py`（伪真实可执行文件日志为空；正对照有 1 次调用） |
| (a) 经 CLI 桥写一张卡 | **PASS**（F-1 已按用户批准修入桥，生产路径复跑通过） | 00d2-01：`status=done`，`cost_usd=0.0933`，卡过最小 check_cards 门禁，writer receipt 过 schema |
| (a) 写 `cards_content/x.md` 成功、写 `library/x` 被拒 | **PASS** | 00d2-02：`permission_denials` 含对 `<build>/library/x.md` 的 Edit；文件不变；`cards_content/x.md` 已改 |
| `--json-schema` 接受 `"$schema": draft 2020-12` 吗 | **不接受**（新发现；已修：桥传参前去掉 `$schema`/`$id`） | 首次 00d2-01：exit 1，0.9 s，`Error: --json-schema is not a valid JSON Schema: no schema with key or ref "https://json-schema.org/draft/2020-12/schema"`，未发生 API 调用 |
| writer_receipt 的 `(?!...)` pattern 能否被 `--json-schema` 接受 | **接受**（去掉 `$schema` 后）；结构化输出由 CLI 校验后返回 | 00d2-01 第二次起正常返回 `structured_output`。是否对该 pattern 做强制校验未单独构造反例 |
| writer 的结构化输出 | `--agent` 主线程工具集 = agent `tools` 白名单，**会滤掉 `--json-schema` 加的 `StructuredOutput`**（新发现，已修） | init 探针：修前 `tools=[Read,Grep,Glob,Edit,Bash]`，结果无 `structured_output`；agent 加 `StructuredOutput` 后 `tools` 含之，00d2-01 PASS |
| macOS 默认工具集是否含 Glob/Grep | 默认不含；**本桥 argv 下含**（`--allowedTools` 点名即恢复两者） | init `tools` 含 `Grep`、`Glob`；docs tools-reference「Glob tool behavior」 |
| `--agents` 内联 vs 文件 | 两种形式加载的 agent 集合相同；本 argv 总长约 1.7 KB，内联足够 | init `agents` 两次相同；docs cli-reference `--agents`（文件形式需 v2.1.281+，仅 `--print`） |
| (b) `/klode-kit:kb-pipeline` 两段式 | **PASS**：第一段 audit exit 10、`awaiting_confirm`；`args.resume` 第二段到 `done`；第一段 3 个 receipt 字节不变 | 00d2-03/04 |
| ⑪ headless（`claude -p --plugin-dir .`）能否跑 Workflow | **可行**。`-p` 会等后台 workflow 结束再输出最终结果（墙钟 35–37 s，`duration_ms` 约 6 s） | 00d2-03/04 的 stdout JSON；docs workflows「Approve the plan before it runs」「Turn workflows off」 |
| ⑬ 信任状态可编程探测 | **可探测**（读 `~/.claude.json` 的文档化键，只读），写进 `src/klode_kit/hostenv.py::trusted()`；读不到时返回 `"undetectable"` | 00d2-05：`_lab` → True，新 tmp → False；宿主行为交叉验证（同配置 Connected / Pending） |
| ⑮ `claude mcp get` 遇 `env` 键能否解析 connected | **能**。`env` 显示在独立的 `Environment:` 块，不影响 `Status:` 行 | 00d2-06；录制 `tests/fixtures/host/mcp_get/*.txt`；S6 性质测试 |
| R2-11 插件命名空间入口 | **PASS**：`claude -p --plugin-dir . "/klode-kit:kb-check --kb <合成库>"` 写出的 check_receipt 中 name/version/entry 与 plugin.json、`skill:/klode-kit:kb-check` 一致，root_sha 与本地计算一致 | 00d2-07 |
| 费用 | 合计 **$1.50**（`total_cost_usd`，客户端估算；实际走订阅）；每次 `--max-budget-usd` ≤ 3 | §6 |

## 1. 桥的 G2 闸门（`src/klode_kit/bridge.py`）

- `gate(claude)`：用 `shutil.which` 按 Popen 的方式解析，取 realpath。父目录**等于**本仓 `tests/fakes/bin` 的 realpath 才算回放假件
  （同名目录 `…/tests/fakes/bin` 不算）；否则要求 `KLODE_KIT_REAL == "1"` 且 `KLODE_KIT_GATE_ACK` 匹配 `\AG2-[0-9]{8}\Z` 并能按
  `%Y%m%d` 解析为日历日期。解析不到可执行文件也按「非假件」处理（先报闸门，不先报 FileNotFoundError）。
- `run()` 在有任务时先过闸门，再起任何进程；放行时 argv[0] 换成闸门核过的绝对路径（不再做第二次 PATH 查找）。
- CLI：`BridgeGateClosed` → exit 61，stderr 说明缺什么；不写 receipt。

## 2. (a) CLI 桥

### 2.1 实测流程

- cwd = `tests/host/_lab/bridge-write-<ts>/builds/b-20261023T000000Z-0123abcd/`；源 `library/book/ch01.txt`；
  `cards_content/ch01.md` 预置骨架（writer 只有 Edit，没有 Write；Edit 不能新建文件——docs tools-reference「Edit tool behavior」）。
- 任务说明里写明卡片格式（WI-12 的正式 agent 文本还没有）。桥参数：`max_rounds=1`、`--max-budget-usd 3.00`。
- writer 通过启动器自检：notes 写「Passed check-cards on the first run」，说明 `Bash(*/bin/klode-kit *)` 能匹配任务提示里带引号的
  `"<插件根>/bin/klode-kit" check-cards cards_content/ch01.md`，`permission_denials` 为空。
- 最小 check_cards（`src/klode_kit/devtools/spike_check_card.py`，命令 `klode-kit check-cards`）：沿用用户 klode-build
  `check_cards.py` 的 F1/F2/F3/A1/A2/A3，加 authored 卡 `min_anchors_full=5`（r2.6、R2-02）。**WI-02a 的 `build/check_cards.py`
  尚未搬入**，此处为 spike 替身，WI-02a 落地后替换。
- 拒写：用合成 agent 定义（同名 `klode-card-writer`，`model: sonnet`，去掉「never edit library」一句，让模型确实去尝试），
  证据来自宿主而非模型：`permission_denials[0] = {tool_name: Edit, tool_input.file_path: <build>/library/x.md}`，
  模型收到的工具结果是「File is in a directory that is denied by your permission settings」。

### 2.2 新发现 F-1（High，已修，2026-10-23 用户批准）：`--json-schema` 拒收 draft 2020-12 声明

- 现象：bridge 原样把 `schemas/writer_receipt.json`（含 `"$schema": "https://json-schema.org/draft/2020-12/schema"`）传给
  `--json-schema`，CLI 启动即 exit 1（见 §0），任务记 `failed`。docs cli-reference `--json-schema` 只说「exits with an error on an
  invalid schema」，未写支持哪些草案版本——检索无结果。
- 去掉 `$schema`/`$id` 后同一 schema（含 `(?!...)` lookahead）被接受，结构化输出正常。
- 两条已记录断言都钉住了现状：`tests/unit/test_contract_schemas.py:92`（每个 schema 必须声明 2020-12）、
  WI-00d1 `test_argv_contract`（`--json-schema` 值 == schema 文件原文）。按规则**未改**任何已记录断言、也未改桥的默认行为。
- 本 spike 的取证用 host 测试里的**显式垫片** `cli_schema_shim`（在桥构造的 argv 上删掉 `$schema`/`$id`；桥的闸门、进程组、分类、
  receipt 照常运行）。
- **已修**（用户 2026-10-23 批准）：`bridge.cli_schema()` 去掉顶层 `$schema`/`$id`；复现用例 `tests/unit/test_bridge_schema_cli.py`
  （先红，receipt `WI-00d2.unit-schema`）；00d1-01 `test_argv_contract` 的那一行改为与去掉两键的 schema 比较；host 测试里的
  `cli_schema_shim` 已删除。生产路径复跑：00d2-01 `done`、$0.0725；00d2-02 `done`、$0.0290，`permission_denials` 含 `library/x.md`。
- 原建议修法：`bridge.build_argv` 传 schema 前去掉 `$schema`、`$id`；00d1-01 改为与去掉这两键的
  schema 比较，并加一条复现用例（argv 中的 schema 不含 `$schema`）。schema 文件本身保留 2020-12 声明（本地校验仍用 Draft202012Validator）。

### 2.3 新发现 F-2（已修）：agent `tools` 白名单滤掉 `StructuredOutput`

- docs agent-sdk/structured-outputs「Recognize the StructuredOutput tool call」：`--json-schema` 会给会话加 `StructuredOutput` 工具。
  但 `--agent klode-card-writer` 时主线程工具集就是 agent 的 `tools` 白名单，init 探针显示 `StructuredOutput` 不在其中。
  结果：卡写好了（隔离区那份副本过门禁），但结果 JSON 没有 `structured_output`，桥按设计判 `parse_error` 并把卡移入 `_quarantine/`。
- 修：`agents/klode-card-writer.md` 的 `tools` 加 `StructuredOutput`；复现用例 `tests/unit/test_writer_structured_output_tool.py`
  （先红后绿，receipt `WI-00d2.unit-writer-so`）。修后 init `tools` 含 `StructuredOutput`，00d2-01 PASS。

### 2.4 WI-00d1「待 00d2 实测」逐项

| 项 | 结论 |
|---|---|
| macOS 默认工具集不含 Glob/Grep；`--allowedTools` 不添加工具 | 前半句对（docs tools-reference「Glob tool behavior」）；**后半句不对**：docs 写「naming either tool in `--allowedTools` restores both」，实测 init `tools` 含 Grep、Glob |
| writer_receipt 的 `(?!...)` 能否被 `--json-schema` 接受 | 接受（前提是去掉 `$schema`，见 F-1） |
| `--agents` 内联过长时改文件形式 | 两种形式结果相同；当前 argv 约 1.7 KB，远低于 ARG_MAX，维持内联；文件形式需 v2.1.281+ 且只在 `--print` |
| 提示词里的 `check-cards` 子命令 | 本 WI 以 spike 替身注册了 `klode-kit check-cards`；正式实现属 WI-02a/09/12 |

### 2.5 风险 R-1：writer 会话加载用户级内容

- writer 的 notes 引用了用户全局 CLAUDE.md 里的规则；init `agents` 列出了用户级与已装插件的 agent。`-p` 默认会发现并加载
  `~/.claude` 下的内容（docs headless：「Without it [`--bare`], `claude -p` discovers and loads them as an interactive session would,
  including anything configured in the working directory or `~/.claude`」）。
- 对写卡路（只读合成/去标识源）不是隔离违例，但会让 writer 行为随用户环境漂移。建议 WI-12 评估 `--setting-sources`/`--bare` 等收紧手段
  （`--bare` 会跳过 hooks 与插件，需要另行验证启动器与 agent 是否仍可用）。

## 3. (b) Workflow 两段式

- 文件：`.claude-plugin/plugin.json`（name `klode-kit`，version `0.1.0-dev`）、`workflows/kb-pipeline.js`（薄壳）、
  阶段替身 `src/klode_kit/devtools/spike_pipeline.py`（命令 `klode-kit dev spike-pipeline`；正式 `pipeline.py` 属后续 WI）。
- 脚本 API 依据：docs workflows「What the saved script looks like」「Edit a saved script」（`meta` 字面量、`agent()/pipeline()/parallel()/
  phase()/log()`、`args` 全局、禁 `Date.now()`/`Math.random()`）；`agent()` 的 opts（`label/phase/schema/model/effort/isolation/agentType`）
  公开文档只写到 `schema`、`label`、`stallMs`，其余取自 2.1.285 二进制内嵌的 `/workflow-authoring` 参考文本（本子代理无 Skill 工具，
  未能直接加载该 skill；官网无对应段落——检索无结果）。
- 插件 workflow 命名：docs workflows「Distribute a workflow in a plugin」——`/<plugin>:<meta.name>`，即 `/klode-kit:kb-pipeline`。
- `-p` 下放行：docs workflows「Approve the plan before it runs」——`-p` 不弹确认，按权限规则评估，需 `Workflow(<name>)` 允许规则。
  实测 argv：`--allowedTools "Workflow(klode-kit:kb-pipeline),Bash(klode-kit dev spike-pipeline *)"`；插件 `bin/` 进入 Bash 的 PATH
  （docs plugins/components），子代理直接调用 `klode-kit` 成功，`permission_denials` 为空。
- 第一段：`preflight 0 → ocr 0 → audit 10`，`work/b1/state.json = awaiting_confirm`，3 个 receipt 过 `receipt` schema、audit
  `exit_code=10`。第二段（`args.resume: true`）：`writeback/build/check` 全 0，state `done`；第一段 3 个 receipt **字节不变**（缓存命中的判据）。
- 「命中缓存」在这里指 pipeline 自身的 receipt 复用（inputs_hash/config_hash/outputs sha 全等则不重写）。Workflow 运行时的
  `resumeFromRunId` 只在同一会话内有效（docs agent-sdk/typescript「Workflow」：「Same session only」），与 r2.5 的两段式（两次独立调用）无关。
- ⑪ **可以 headless**：`claude -p --plugin-dir <插件根>` 中 Workflow 后台运行，`-p` 进程等到 workflow 完成才输出最终结果
  （墙钟 35.5 s / 37.2 s，结果 JSON 的 `duration_ms` 只有约 6 s）。U3 的 `/workflows` 可见性（交互 UI）仍需用户手动观察，headless 无法断言。
- 宿主副作用：即使带 `--no-session-persistence`，Claude Code 仍把 workflow 的脚本副本、journal、子代理 meta 写到
  `~/.claude/projects/<cwd 编码>/<会话>/workflows/`（docs workflows「How a workflow runs」：「Every run writes its script to a file under your
  session's directory in `~/.claude/projects/`」）。本 WI 没有主动写 `~/.claude`；这些文件是宿主行为产物，未动。

## 4. (c) 宿主探测

### 4.1 ⑬ 信任（`hostenv.trusted(path)` → `True | False | "undetectable"`）

- 依据：docs permissions「Project allow rules and workspace trust」（仓库内按 git 根、worktree 按主检出根、仓库外按启动目录记信任；
  家目录信任不落盘；`-p` 不弹信任框且「trusting a parent folder doesn't count」）；docs errors「workspace has not been trusted」
  （存储键 `projects["<path>"].hasTrustDialogAccepted`，位于 `~/.claude.json`）。
- 实现：只读 `~/.claude.json`；键 = git 根（`.git` 目录；`.git` 文件指向 `…/.git/worktrees/<n>` 时取主检出根；其它 `.git` 文件如
  submodule 视为自身根）或仓库外的目录本身；realpath（实测键是 `/private/tmp` 而非 `/tmp`）；只有字面 `true` 算信任；家目录恒 False；
  文件缺失/坏 JSON/结构不符，或设了 `CLAUDE_CONFIG_DIR` 而未显式给存储路径 → `"undetectable"`。
- 交叉验证（宿主行为，00d2-05）：同一份「已批准」的 `.mcp.json` + `.claude/settings.local.json`，在 `_lab`（仓库已信任）为 Connected，
  在新 tmp 目录为 Pending；在 `_lab` 里 `git init` 的嵌套仓库也为 Pending——与「嵌套仓库按自身根」一致。
- 局限：存储位置与键格式是文档化的「手工设置」接口，但不是稳定 API；`CLAUDE_CONFIG_DIR` 下的位置文档未写，按不可探测处理。
  另有一个无需读配置的行为探针（批准一个探针 MCP 服务器后看 `claude mcp get` 是 Connected 还是 Pending），但要往数据项目写文件，未采用为默认。

### 4.2 ⑮ `claude mcp get` 与 `env` 键

- 录制（家目录替换为 `/Users/<user>`；未知服务器那条里用户自己的服务器名已脱敏）：`connected` / `pending` / `failed` / `missing`。
- 输出里 `env` 以独立块出现：`Environment:` 下每行 `    KEY=value`（4 空格缩进），`Status:` 行是 2 空格缩进且在其前面。
- `hostenv.parse_mcp_get` 只读环境块之前的 2 空格 `Status:` 行；识别不了一律 `unknown`（绝不判 connected）。S6 性质测试：插入任意
  `Environment` 行（包括伪装成 `Status: ✔ Connected` 的值）或删掉整个环境块，结果都不变。
- 补充（docs permissions「What runs before you trust a folder」）：未信任目录里 `-p` 会直接连接 `.mcp.json` 服务器，但
  `claude mcp list` 仍报 pending——所以 R2-07 的 `mcp_host_visible` 判定必须以信任为前提，与实测一致。

## 5. (d) R2-11

- `skills/kb-check/SKILL.md`：`disable-model-invocation: true`；`allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-check *)`；
  正文用动态上下文注入 `` !`${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-check $ARGUMENTS --entry skill:/klode-kit:kb-check` ``
  （docs skills「Inject dynamic context」「Available string substitutions」：插件 skill 内 `${CLAUDE_PLUGIN_ROOT}` 在正文与 `allowed-tools`
  的 Bash 规则里都会替换；注入命令走 Bash 工具，受 `allowed-tools` 放行）。
- `klode-kit kb-check`（`src/klode_kit/kb_check.py`）写 `builds/<id>/check_receipt.json` 并打印；只有 plugin 块是真的，build 检查记
  `WARN not run`（WI-13a）。`root_sha` 按 R3-08 的文件集合与算法；跳过 `__pycache__`、`*.pyc`、`.DS_Store`（偏离，见回报）。
- 实测：`claude -p --plugin-dir <插件根> --model sonnet "/klode-kit:kb-check --kb <_lab>/kb/fx-labs-3x"` → receipt 落盘，
  `plugin = {name: klode-kit, version: 0.1.0-dev, entry: skill:/klode-kit:kb-check, root_sha: <与本地计算相同>}`，过 `check_receipt` schema。
  断言取自落盘文件，不取模型回复。

## 6. 费用（`work/host-spike/costs.jsonl`）

| 调用 | `total_cost_usd` | 备注 |
|---|---|---|
| 00d2-01 第 1 次 | 0 | `--json-schema` 启动校验失败，0.9 s，无 API 调用（台账先按上限记 $3，随后按证据冲回） |
| 00d2-01 第 2 次 | 0.1285 | 卡写成但无 `structured_output`（F-2） |
| init 探针 ×4（修前 2、修后 2） | 0.1364 | stream-json，读 init 事件 |
| 00d2-01 第 3 次 | 0.0933 | PASS（opus） |
| 00d2-02 | 0.0432 | PASS（sonnet） |
| Workflow 第一段 / 第二段 | 0.4224 / 0.4295 | sonnet 主会话 + 3 个子代理（effort low） |
| 00d2-07 kb-check | 0.1492 | sonnet |
| 00d2-01 / 00d2-02 生产路径复跑 | 0.0725 / 0.0290 | F-1 修后 |
| **合计** | **1.5039** | 上限 $10；每次 `--max-budget-usd` ≤ 3 |

## 7. 00d2-* 对应

| ID | 用例 | lane | 结果 |
|---|---|---|---|
| 00d2-01 | `test_bridge_writes_one_card_check_pass` | model_integration | PASS（生产路径） |
| 00d2-02 | `test_bridge_edit_library_denied` | model_integration | PASS（生产路径；`library/x.md` 预置、断言「内容不变」而非「不存在」，因 writer 只有 Edit） |
| 00d2-03 | `test_workflow_stage1_exit10_awaiting` | host_acceptance | PASS |
| 00d2-04 | `test_workflow_resume_hits_cache` | host_acceptance | PASS；⑪ 可 headless |
| 00d2-05 | `test_trust_probe` | host_acceptance | PASS |
| 00d2-06 | `test_mcp_get_env_key_connected` | host_acceptance | PASS |
| 00d2-07 | `test_kb_check_entry_plugin_fields` | host_acceptance | PASS |
| 00d2-08 | `test_hostenv_parse_mcp_get_variants`（+ S3/S6 用例） | unit | PASS |

## 8. 待决与残余风险

- F-1 已修（§2.2）。
- R-1 writer 加载用户级 CLAUDE.md / agents / 插件（§2.5），交 WI-12。
- `check-cards`、`kb-check`、`dev spike-pipeline` 三个命令是 spike 替身，分别由 WI-02a、WI-13a、pipeline 相关 WI 替换。
- ⑬ 依赖 `~/.claude.json` 的文档化键；Claude Code 若改存储，`trusted()` 会退化为 `"undetectable"`（安全方向），不会误报 True。
- Workflow 运行会在 `~/.claude/projects/` 留下运行记录（宿主行为）。
