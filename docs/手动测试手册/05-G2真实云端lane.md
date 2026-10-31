# T05 G2 真实云端 lane

层：1 机器层（会花钱）　闸门：G2（真实云端外发 / 真实模型调用）、G5（宿主信任）　时间：数小时（待核：没有在手册编写时完整计时）　花费：**估计 20 美元以上**（issue [#4](https://github.com/bubiji/klode-kit/issues/4) 的估计，含 Claude 与 Codex；上一轮维护者的 G2 实跑累计 15.38 美元，且仍缺这两条 lane 的完整回执）

> **这是本手册里唯一花钱的一章，也是最贵的一章。没有预算或没有把握，请跳过——其余章节不依赖它。** 想帮忙但不想出钱，做 T01–T04、T06、T07 就够了。

## 目的

补齐 `release-check` 现在缺的最后两项：`lane:model_integration` 与 `lane:host_acceptance` 的发布回执（issue #4）。这两条 lane 要真的调用 `claude` / `codex`，只用仓库登记过 sha 的**合成图**，验证：

- 四路 OCR 里的两条云端路（Claude、Codex）读合成最难页的真实表现；
- 云端路的**隔离**：模型被限制在工作目录里，读不到目录外的「金丝雀」文件；
- 真实写卡（便宜模型一卡一派）后卡片过门禁；
- 宿主验收：`claude -p --plugin-dir .` 下 skill / Workflow / 专家 agent / MCP 的真实表现；
- U1（入库）、U3（`/klode-kit:kb-pipeline` 两段式）、U5（专家问答）的真跑版本。

## 前提

- T01、T02 已通过；`bin/klode-kit doctor` 全绿，**包括** `claude`（≥ 2.1.285）、`codex`（≥ 0.163.0）、`codex_config`、`mlx` 四项。
- `claude` 和 `codex` 都已登录、能正常用，PATH 上能找到。
- 已按第 7 章接受 **klode-kit 仓库根目录**的工作区信任（在 `<klode-kit 目录>` 里交互运行一次 `claude` 并接受）。宿主测试用的实验目录 `tests/host/_lab/` 在这个仓库里，信任的键是仓库根，所以一次就够。
- 工作树干净：`git status --short` 无输出。发布回执绑定代码树的 sha，之后改任何运行文件，回执作废。
- **你明确愿意花这笔钱。** 建议先在你的模型账号控制台里设置一个花费上限（比如 25 美元），作为软件封顶之外的第二道保险。
- 只使用 `tests/fixtures/MANIFEST.json` 里登记过的合成图。**本章任何环节都不要放进你自己的图片。**

## 钱是怎么被管住的

1. **闸门**：不设置 `KLODE_KIT_REAL=1` 和 `KLODE_KIT_GATE_ACK=G2-<yyyymmdd>`（一个真实存在的日历日期）就一次调用都不会发生。选了带 `gate_cloud` 标记的测试却没有有效的 ack，整个 pytest 会话直接以退出码 2 结束，零调用，输出形如：

   ```
   usage error: gate_cloud tests selected but KLODE_KIT_GATE_ACK is missing or not G2-<yyyymmdd> (got ''); refusing to run N gated test(s)
   ```

   （文字取自 `src/klode_kit/devtools/pytest_plugin.py`，待核：本次编写未实跑。）脚本和 skill 不会替你设置这两个变量，**你自己设置才算授权**。
2. **每次调用有 `--max-budget-usd` 上限**（见下表）。
3. **账本**：宿主类测试在每次调用前预留该次上限，账本总额加上这次上限超过累计上限就拒绝调用，调用后记入实际花费。账本在 `work/host-spike/costs.jsonl`（累计上限在 `tests/_support/host.py` 的 `WI_CAP_USD`，当前为 21.1 美元；新检出的账本为空）。模型类测试另有账本 `work/isolation/ledger.jsonl`（四路最难页：Claude 累计上限 8 美元、Codex 最多 20 次调用；隔离探针按 WI 分别有 5 / 15 美元上限）。两个账本都在被 `.gitignore` 忽略的 `work/` 下。
4. 超限时测试会明确失败，而不是悄悄多花。形如 `...: ledger 20.9 + cap 1.5 > WI cap 21.1` 或 `claude spent $7.50; next call could pass $8.0 - stop (G2)`。

主要调用上限（取自测试源码）：

| 测试 | 单次上限 |
|---|---|
| U1 真跑（`test_u1_med_intake_real`） | 5.0 美元 |
| U1 自动打码真跑（`..._wired_real`） | 2.0 美元 |
| U5 专家问答（两个问题） | 各 1.5 美元 |
| U3 宿主两段式（两个测试） | 各 2.0 美元 |
| 专家 agent 发现 | 0.5 美元 |
| `kb-check` 入口 | 1.0 美元 |
| 桥与 Workflow 两段式 | 各 1–5 美元 |
| 四路最难页 | Claude 单次 5 美元、累计 8 美元；Codex ≤ 20 次 |
| 隔离探针 | Claude 单次 5 美元 |

上限是「最多」，实际花费以账本为准。上限之和远大于 20 美元，所以真正的约束是累计上限和你自己的账号上限。

## 步骤

### 5.1 核对要跑的东西（不花钱）

```sh
cd <klode-kit 目录>
KLODE_KIT_GATE_ACK=G2-19990101 .venv/bin/python -m pytest -m model_integration --collect-only -q -p no:cacheprovider | tail -3
KLODE_KIT_GATE_ACK=G2-19990101 .venv/bin/python -m pytest -m host_acceptance --collect-only -q -p no:cacheprovider | tail -3
```

`--collect-only` 只列出用例、不执行；这里随便给一个格式合法的 ack 只是为了让收集过得去。应该看到：

```
23/<总数> tests collected (<其余> deselected) in ...s
13/<总数> tests collected (<其余> deselected) in ...s
```

（23 与 13 是编写手册时的数量，会随版本变化。）

`model_integration` 的 23 个用例（按文件）：

| 文件 | 用例 | 大意（据用例名与文件说明） |
|---|---|---|
| `tests/acceptance/` | `test_u1_med_intake_real`、`test_u1_med_intake_masks_by_itself_real` | U1 入库的真跑；自动打码后只发打码图 |
| `tests/acceptance/` | `test_u5_expert_qa`、`test_u5_expert_not_found` | U5 专家问答两问 |
| `tests/host/test_bridge_spike.py` | `test_bridge_writes_one_card_check_pass`、`test_bridge_edit_library_denied`、`test_writer_argv_tools_and_agents_forms` | 桥派发写卡、不许改库、参数形态 |
| `tests/model/` | `test_live_vs_replay_shape_claude`、`..._codex` | 真实输出与录制回放的形状一致 |
| `tests/model/` | `test_real_writer_card_check_pass` | 真实写卡过门禁 |
| `tests/model/` | `test_codex_host_check_real`、`test_codex_ocr_production_argv` | Codex 宿主检查与生产参数 |
| `tests/model/` | `test_hardest_pages_4routes_live`、`test_spike_doc_records_r305_and_codex_effort`、`test_codex_max_effort_live` | 四路最难页实跑与 Codex 最高档 |
| `tests/model/` | `test_host_probe_tool_result` | 宿主探针 |
| `tests/model/test_isolation_probe.py` | 7 个 | Claude / Codex 隔离探针、守卫钩子、隔离回执、`setting_sources` |

`host_acceptance` 的 13 个用例：`test_u3_workflows_host`、`test_u3_second_segment_writes_back`、桥的 3 个（与上共用）、`test_workflow_stage1_exit10_awaiting`、`test_workflow_resume_hits_cache`、`test_trust_probe`、`test_mcp_get_env_key_connected`、`test_kb_check_entry_plugin_fields`、`test_host_discovers_expert`、`test_kb_check_entry`、`test_workflow_two_stage`。

### 5.2 授权（你亲手设置）

在**这一个终端**里设置，不要写进 `~/.zshrc` 之类的配置：

```sh
export KLODE_KIT_REAL=1
export KLODE_KIT_GATE_ACK=G2-<日期>      # 例如 G2-20261031，必须是真实日历日期
```

确认现在是在你自己的 Claude Code 账号 / Codex 账号下，且没有别人共用。

### 5.3 另开一个终端盯账本

```sh
cd <klode-kit 目录>
tail -f work/host-spike/costs.jsonl
```

每行形如 `{"label": ..., "cap_usd": ..., "reported_usd": ..., "booked_usd": ..., "at": ...}`；`reported_usd` 为空表示那次调用没报告花费，账本就按整个上限入账（保守方向）。看到累计明显超出你的预期，就在运行终端按 Ctrl-C。

### 5.4 跑 `model_integration`

```sh
bin/klode-kit dev lane model_integration
```

**不要加 `--not-release`**（它标记回执为 CI 用，`release-check` 会拒认）。**不要加 `-- -k ...` 之类只跑一部分**：`dev lane` 会为这一部分也写一份 PASS 回执，被误当成整条 lane 的发布回执。想先试一部分，请直接用 pytest（不写回执）：

```sh
.venv/bin/python -m pytest -m model_integration tests/acceptance/test_u5_expert_qa.py
```

四路最难页用例还认环境变量 `KLODE_KIT_SPIKE_PAGES`（逗号分隔，如 `fx-rx-stamp/p001`）选页，同样只用于试跑。

应该看到：测试陆续执行（每个真实调用要几十秒到几分钟），结束时最后一行形如

```
model_integration: PASS (23 discovered, 0 failed, 0 skipped)
```

退出码 0，并写出 `work/lanes/model_integration.json`。

### 5.5 跑 `host_acceptance`

```sh
bin/klode-kit dev lane host_acceptance
```

应该看到最后一行形如 `host_acceptance: PASS (13 discovered, 0 failed, 0 skipped)`，退出码 0，写出 `work/lanes/host_acceptance.json`。

### 5.6 收回授权，跑 release-check

```sh
unset KLODE_KIT_REAL KLODE_KIT_GATE_ACK
bin/klode-kit dev release-check --mutation skip
echo $?
```

应该看到五条 lane 都 `"ok": true`，缺项为空（树干净的话），标准错误最后一行：

```
release_check: releasable (<N> items, 1 waived)
```

退出码 0。`--mutation skip` 是显式放弃变异得分，会列在 `waived`；仓库里已有 `work/mutation.json`（得分 0.84）时可以不加。

### 5.7 对账

```sh
python3 -c "import json;print(sum(json.loads(l)['booked_usd'] for l in open('work/host-spike/costs.jsonl')))"
```

把这个数、`work/isolation/ledger.jsonl` 里各行 `cost_usd` 之和，和你模型账号控制台上的实际扣费对一遍。**两边差得大**，说明账本漏记了调用——这是比测试失败更严重的发现，请务必报告。

## 通过判据

- [ ] 5.4 与 5.5 的退出码 0、`PASS`、0 failed、0 skipped。
- [ ] 5.6 `release-check` 退出码 0（或只剩你清楚原因的 waived / dirty）。
- [ ] 5.7 账本合计与你账号上的实际扣费大体吻合，且总额没有超出你设的上限。
- [ ] 运行期间没有任何一次调用发生在你设置 `KLODE_KIT_REAL=1` 之前。

## 失败了怎么办

- 失败的真实模型测试可能是模型输出的正常波动，也可能是真 bug。**先只重跑那一个用例一次**（用 pytest 直接指定用例 id，注意会再花钱），两次都失败再报。
- 报告里附：失败用例的 id、pytest 摘要、`work/lanes/<lane>.json`、该用例在 `work/host-spike/<标签>/` 下的原始输出（`*.argv.json`、`*.stdout.json`、`*.stderr.txt`、`*.meta.json`）。这些文件可能含本机路径和你的用户名，**贴之前先替换**；`stdout.json` 里是模型对合成图的回答，不含你的数据。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| pytest 以退出码 2 结束，提示 `gate_cloud tests selected but KLODE_KIT_GATE_ACK is missing ...` | 没设置或格式不对的 ack。检查是 `G2-` 加 8 位日期，且是真实存在的日历日期。 |
| 命令退出码 61 | 真实调用闸门没开（`KLODE_KIT_REAL` 不是 `1`，或 ack 无效）。命令本身一次调用都没发。 |
| `no real claude on PATH` / `claude not on PATH` | PATH 里只有仓库的回放假件 `tests/fakes/bin/claude`，或没装。确认 `which -a claude` 里有真的。 |
| `BudgetExceeded` / `stop (G2)` | 账本累计触顶。不要去改上限常量；停下来，核对账本，向维护者说明。 |
| 宿主用例等很久 | `claude -p` 要等后台 Workflow 跑完才输出；单次最长约 15 分钟（超时 900 秒）。 |
| 宿主用例提示信任相关错误 | 第 7 章没做，或做在了别的目录。信任要接受在 klode-kit 仓库根。 |
| `release-check` 仍说回执不对 | 回执绑定代码树和解释器。中途改过运行文件、换过 Python，或回执超过 7 天，都需要重跑那条 lane。 |
| `release-check` 报 `dirty` | 跑测试期间工作树被改了（比如你在别处编辑了文件）。`git status` 看是什么。`work/`、`tests/host/_lab/` 已被忽略，不应造成 dirty。 |

## 费用与隐私

- **真钱。** 全部调用只涉及合成图与合成项目；云端路只收到抬头打码后的图（`_masked/`），发送器和 `hooks/` 里的钩子各查一遍 sha。
- 原始输出落在 `work/host-spike/`、`work/isolation/` 和 `tests/host/_lab/`，都被 `.gitignore` 忽略，里面可能有你的本机路径。不要提交，不要整个目录打包贴出去。
- 做完 `unset` 那两个变量；不要把 ack 留在 shell 配置里。
