# T04 U5 问 expert

层：2 合成数据亲手验　闸门：G3（只对合成演示项目发布，用私有注册表）、G5（演示项目要接受工作区信任）　时间：20–30 分钟　花费：问两个问题用你自己的 Claude Code 额度（自动化测试对每个问题封顶 1.5 美元，实际通常远低于此）

## 目的

这是 issue [#6](https://github.com/bubiji/klode-kit/issues/6) 里「用户亲手问」的一半。自动化已经证明：专家 agent 能被宿主发现、答案里每行带逐字锚点、锚点能被真实的 `klode verify` 核对，库里没有的药不会被编出数值。**没有人做过的**是：由你亲手对着专家提问，看回答是否像一个可信的助手，并判断它「查不到」时的措辞要不要收紧。

对应用户用例 U5：对 `<库名>-klode-expert` 说「示例片最近三次的剂量」，回答逐条带 `(grep: ...)` 逐字锚点，`klode verify` 能核对；库里没有的问题回「查不到」，不编值。

## 前提

- T01 通过；T07（信任）会用到；T03 的 3.1–3.6 流程会再做一遍（这次用处方样本），建议先做过 T03。
- 能用 `claude`（已登录），版本不低于 2.1.285。
- 本章会**在演示项目里发布**（写该项目的 `.mcp.json`、`.claude/settings.local.json`、`.claude/agents/`，并写一份注册表）。这是闸门 G3。我们用环境变量 `KLODE_REGISTRY` 把注册表指到演示目录下的临时文件，**不会碰你真实的 `~/.klode/registry.toml`**。请不要对你的真实数据项目做这一章。
- 样本是合成的「处方笺」（`fx-rx-stamp`）：一张处方，日期 2026-09-19，三行药：`示例片 10mg`、`示例片 5mg`、`示例片 0.25g`。注意：04 需求文档的例句日期写的是 2026-09-18，库里实际是 2026-09-19，以库为准。

## 步骤

### 4.1 准备演示项目（目录名决定库名）

库 id 取项目目录名，专家 agent 叫 `<库 id>-klode-expert`。下面用 `rxdemo`。

```sh
export KK=<klode-kit 目录>
export DEMO=<演示目录>
mkdir -p "$DEMO/_inbox/2026-09-19" "$DEMO/rxdemo" "$DEMO/registry"
cp "$KK"/tests/fixtures/fx-rx-stamp/images/*.png "$DEMO/_inbox/2026-09-19/"
cd "$DEMO/rxdemo"
git init -q .
printf '%s\n' _work/ _raw/ _masked/ _mask_review/ _private/ work/ kb/ > .gitignore
claude        # 接受工作区信任后 /exit，不要提问；见第 7 章
"$KK/bin/klode-kit" doctor --project .
```

应该看到 `doctor: all <N> checks passed`，退出码 0。

### 4.2 入库（回放，不调用模型）

```sh
KLODE_KIT_TEST_SEAMS=1 \
KLODE_KIT_ENGINE_REPLAY="$KK/tests/fixtures/fx-rx-stamp/engine" \
"$KK/bin/klode-kit" med-intake --inbox ../_inbox/2026-09-19 --project .
echo $?
```

应该看到退出码 10，输出末三行：

```
ocr: 1 页 × 4 路 OK
audit: 2 条分歧 → confirm/20260919.html
status: awaiting_confirm
```

（第一行的页数为 1，是按样本只有一页推出的，待核。）

打开 `confirm/20260919/confirm.html`。两条分歧是处方里同一行的规格（5mg）和数量（2），其中一路（mlx）读错了，另外三路一致。**对两条都选带 ★推荐 的那个值**，点「导出 choices.json」，把下载的文件移到 `$DEMO/choices.json`。

```sh
KLODE_KIT_TEST_SEAMS=1 \
KLODE_KIT_ENGINE_REPLAY="$KK/tests/fixtures/fx-rx-stamp/engine" \
"$KK/bin/klode-kit" pipeline --batch ../_inbox/2026-09-19 \
  --choices "$DEMO/choices.json" --stage writeback --project .
echo $?
```

应该看到退出码 0，输出里有 `r2 verified`，末尾 JSON 里 `"exit_code": 0`、`"reason": "ok"`。`verified/2026-09-19-rx.md` 存在。

### 4.3 建库、检查、发布（私有注册表）

```sh
"$KK/bin/klode-kit" build --project .
```

这是「记录路径」：卡片与书桌合成都由程序确定性生成，**零模型调用**。输出很长，看最后一行（标准输出）：

```
{"build_dir": "kb/rxdemo/builds/b-<时间戳>-<hash>", "build_id": "b-<时间戳>-<hash>", "exit_code": 0, "reason": "ok", "schema": "klode-kit.build/1", "status": "PASS"}
```

中间应当能看到 `build: desk_verify: PASS`、`build: gen_agent: PASS - rxdemo-klode-expert.md`、`build: self_check: PASS` 和一段冒烟（`initialize`、`tools/list — 8 tools`、`verify_quote 真句 VERIFIED`、`verify_quote 伪句 NOT FOUND`，全是 PASS，最后一行「全部通过」）。

```sh
export BUILD=$(ls kb/rxdemo/builds | grep '^b-' | tail -1)
"$KK/bin/klode-kit" kb-check --kb kb/rxdemo --build "$BUILD" --entry manual
```

应该看到一段 JSON，`"status": "PASS"`，所有检查项 PASS，其中 `holdout` 是 `5/5`、`smoke_mcp` 是 `6 checks on ...`；`"u5_ready": false` 是**正常的**（发布前宿主 MCP 还没连过，见已知坑 C01）。

发布到演示项目，**务必先设置私有注册表**：

```sh
export KLODE_REGISTRY="$DEMO/registry/registry.toml"
"$KK/bin/klode-kit" kb-publish --kb kb/rxdemo --project . --build "$BUILD"
```

应该看到五行 `done`：

```
kb-publish: done          switch
kb-publish: done          mcp_json  rxdemo-klode -> build b-...
kb-publish: done          registry  rxdemo -> build b-...
kb-publish: done          agent  rxdemo-klode-expert.md
kb-publish: done          approve  rxdemo-klode enabled
```

再检查四样东西落在了该落的地方：

```sh
ls -l kb/rxdemo/current           # 指向 builds/<构建号>
cat .mcp.json                      # 只有 rxdemo-klode 一项，env.KLODE_KIT_BUILD 是构建号
cat .claude/settings.local.json    # enabledMcpjsonServers 含 rxdemo-klode
ls .claude/agents                  # rxdemo-klode-expert.md
cat "$DEMO/registry/registry.toml" # 有一个 [[kb]] rxdemo 条目
```

`.mcp.json` 里的 `command` 和 `args` 是绝对路径（你的本机路径），**贴报告前请替换**。

### 4.4 直连探针（不调用模型）

```sh
"$KK/bin/klode-kit" u5-probe --kb kb/rxdemo --project .
```

应该看到一行 JSON：`"direct": {"ok": true, "build_id": "<构建号>", ...}`，`"host_state": "not_run"`，`"exit_code": 0`，`"reason": "ok"`。`"u5_ready": false` 在这里是正常的，因为 u5_ready 还要「宿主真的连上了 MCP」。**不要加 `--host`**：那会真实调用 `claude`，属于 G2（第 5 章）。

### 4.5 亲手提问

仍在 `rxdemo` 目录：

```sh
claude --agent rxdemo-klode-expert
```

第一次可能要你确认 MCP 服务 `rxdemo-klode` 的权限或工具调用，按提示允许（`.claude/settings.local.json` 已预批准该服务，前提是信任已接受）。

**问题一：**

```
示例片最近三次的剂量
```

应该看到：

- 先有一句中文结论，再逐条列出，**至少三条**，每条以日期开头、末尾带逐字锚点，形如：

  ```
  - 2026-09-19 10mg (grep: 示例片 10mg 2026-09-19) — 卡 sample-tablet
  ```

  三条分别对应 `示例片 10mg 2026-09-19`、`示例片 5mg 2026-09-19`、`示例片 0.25g 2026-09-19`（具体措辞和排版因模型而异；自动化测试只要求「每行有锚点、三个剂量都被引用」）。
- 会话里能看到它调用了 `rxdemo-klode` 的工具（`search_sources`、`zoom_card`、`verify_quote` 之类）。

**逐条核对锚点**（这一步是机器判）：先取库根目录再逐条验：

```sh
cd kb/rxdemo/current
klode -c library.toml verify sample-tablet "示例片 5mg 2026-09-19"
cd ../../..
```

应该看到：

```
[rxdemo] FOUND — '示例片 5mg 2026-09-19' in library/items/sample-tablet.txt
  2: 示例片 5mg 2026-09-19 ⟨r-<记录号>⟩
```

退出码 0。klode 0.5.0 的命令行用语是 `FOUND`（MCP 里叫 `VERIFIED`，是同一件事）。对回答里的**每条**锚点都做一次；锚点如果带了行尾的 `⟨r-...⟩` 记录标记，先去掉再验。伪造一个对照：

```sh
cd kb/rxdemo/current
klode -c library.toml verify sample-tablet "示例片 99mg 2026-09-19"
echo $?
cd ../../..
```

应该看到 `NOT-FOUND — '示例片 99mg 2026-09-19' in library/items/sample-tablet.txt`，退出码 1。

**问题二（库里没有的药）：**

```
阿莫西林最近三次的剂量
```

应该看到：

- 回答说库里**没有**这个药的记录（可能的措辞：「查不到」「没有找到……记录」「库里没有」），并说明搜过哪些词；
- **没有任何剂量数字**（不应出现 `10mg` 之类），**没有** `(grep:` 锚点——没有东西可引。

**请记下它的原话**，并回答 issue #6 里留的那个问题：专家的「查不到」措辞要不要收紧？背景：专家 agent 文件要求回答「查不到」，但维护者实测里模型有时换一种说法（观察到「没有找到……记录」「库里没有阿莫西林的记录」），自动化只断言「意思对：明确说没找到、且无数值」，没有断言字面。你觉得这样够用，还是希望必须出现字面「查不到」？写进报告。

**加问一个（可选）：**

```
示例片的最大安全剂量是多少
```

库里只有「记录了什么」，没有医学常识。期望它**不凭记忆回答**，说明库里没有这类信息（专家文件的纪律：「cite, don't recall」，禁止用训练记忆补；这一条的预期是按文件纪律推出的，待核）。如果它给出了一个剂量数字，请把原话记入报告，这是个重要发现。

### 4.6 非交互变体（可选）

如果想复现自动化测试的调用方式（也会花你的额度）：

```sh
claude -p --agent rxdemo-klode-expert --output-format json \
  --allowedTools "mcp__rxdemo-klode__search_sources mcp__rxdemo-klode__zoom_card mcp__rxdemo-klode__verify_quote mcp__rxdemo-klode__list_kbs mcp__rxdemo-klode__list_lenses mcp__rxdemo-klode__diagnose mcp__rxdemo-klode__consult_dimension Read Grep Glob" \
  --no-session-persistence --max-budget-usd 1.50 \
  "示例片最近三次的剂量"
```

`--max-budget-usd 1.50` 是单次花费上限。

## 通过判据

- [ ] 4.3 `build` 的 `status` 是 `PASS`，`kb-check` 是 `PASS`，发布五行全 `done`，且没有改动 `~/.klode/registry.toml`。
- [ ] 4.4 直连探针 `direct.ok` 为 true。
- [ ] 4.5 问题一：至少三条，每条带 `(grep: ...)` 锚点，三个剂量（10mg、5mg、0.25g）都被引用，每条锚点 `klode verify` 为 `FOUND`。
- [ ] 4.5 问题二：明确说没找到，无剂量数字，无锚点。
- [ ] 报告里附上你对「查不到」措辞的意见。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| `claude --agent rxdemo-klode-expert` 提示找不到 agent | 新写的 agent 文件要新启动一次会话才生效；确认你在 `rxdemo` 目录里启动，且 `.claude/agents/rxdemo-klode-expert.md` 存在。 |
| 提问时工具被拒绝或一直在等批准 | 工作区信任没接受（`.claude/settings.local.json` 的预批准依赖信任）。回到第 7 章。 |
| MCP 服务连不上 / 待批准 | `.mcp.json` 里的服务在没信任的目录里不会被批准。接受信任后重开会话。 |
| `kb-publish` 退出码 30，提示 `check_receipt.json: missing or unreadable (the build was not checked)` | 构建没过 `kb-check` 就不许发布。先运行 `kb-check`。 |
| `build` 在 `desk_apply` 退出 20 | 那是**书籍路径**（带 `--source`）的正常第一步；本章走记录路径，不带 `--source`，不应出现。 |
| 回答里的日期是 2026-09-19，不是 2026-09-18 | 库里就是 09-19，04 文档例句的日期是笔误（见 08 验收对照表）。 |
| 答案里剂量排序和你想的不同 | 样本里三行药在同一天；「最近三次」就是这三行，顺序不作要求。 |

## 费用与隐私

- 4.1–4.4 全部 0 次模型调用。只有 4.5 / 4.6 的提问会用到你的 Claude Code 额度，问题与回答只涉及合成数据。
- 注册表用的是演示目录里的私有文件。结束后把 `$DEMO/rxdemo` 和 `$DEMO/registry` 整个**移走**（不要删），并 `unset KLODE_REGISTRY`。
- 报告里的 `.mcp.json`、终端输出请先把路径换成占位符。
