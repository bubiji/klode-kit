# T02 release_check 机器层

层：1 机器层　闸门：无　时间：约 20–40 分钟（`unit` lane 在维护者机器上约 5 分钟，另两条 lane 的耗时待核）　花费：0（不调用任何模型）

## 目的

在你的机器上把自动化测试的离线三条 lane（`unit`、`contract_replay`、`cli_integration`）、隐私扫描、发布清单和 `release-check` 各跑一遍，确认「在维护者机器上全绿」不是巧合。这里**不**跑两条要花钱的 lane（`model_integration`、`host_acceptance`，见第 5 章），所以 `release-check` 在你这里的预期结果就是**恰好缺这两项**。

## 前提

- 已完成 T01，且安装时带了 `--dev`（`scripts/install.sh --dev`）；`bin/klode-kit doctor` 通过。
- 在 klode-kit 目录里操作，工作树干净（`git status` 无输出）。要做修改后的试验请另开一个工作副本。
- `cli_integration` 是「缺依赖就失败、绝不跳过」：需要真实的 `klode` / `klode-mcp`、`pdftotext`、本机 OCR 引擎、MLX 权重、`node`。没有 MLX 权重就会有 `macos_mlx` 标记的用例失败，这是预期（并且这样的失败正是我们想知道的信息）。
- 渲染 WUA 工作流图的用例需要环境变量 `WFVIZ_SRC` 指向 wfviz 工具的 `src` 目录（见仓库 `README.md` 第 6 节）；你没有这个工具时，`tests/docs/test_wua.py` 里那几个渲染用例会失败，属已知，报告里注明即可（待核：具体失败条数未验证）。

## 步骤

### 2.1 三条离线 lane

三条 lane 各自写一份回执 `work/lanes/<lane>.json`，不要并行跑。

```sh
bin/klode-kit dev lane unit
bin/klode-kit dev lane contract_replay
bin/klode-kit dev lane cli_integration
```

每条命令跑完，最后一行形如：

```
unit: PASS (3487 discovered, 0 failed, 0 skipped)
```

数字会随版本增加（维护者回执：`unit` 3487、`contract_replay` 916、`cli_integration` 306，均为 PASS；当前代码树上收集到的数量略多，以你的输出为准）。重点看三件事：状态是 `PASS`，`0 failed`，`0 skipped`。退出码 0。

`unit` 与 `contract_replay` 两条是离线 lane：测试里任何网络连接都会抛异常，没有设置场景就调用 `claude` 也会报错。所以它们**不可能**悄悄花钱。

也可以直接用 pytest 复现某条 lane（不写回执）：

```sh
.venv/bin/python -m pytest -m unit --disable-socket
```

### 2.2 看一眼回执

```sh
cat work/lanes/unit.json
```

应该看到：`"status": "PASS"`、`"tests_failed": 0`、`"tests_skipped": 0`，`env_fingerprint` 里有 `code_tree_sha`、`repo_root`（你的路径）、`python`（你的 Python）。回执绑定「这个代码树 + 这台机器 + 这个解释器」，别处拷来的回执 `release-check` 不认。

### 2.3 隐私扫描

```sh
bin/klode-kit dev privacy-scan
echo $?
```

应该看到 `privacy_scan: 0 finding(s)`，退出码 0。

**负面试验**（证明扫描真的会报）：在一个**临时目录**（不要放进仓库）里造两行假数据再扫它：

```sh
mkdir -p <演示目录>/ps
python3 -c "print('姓'+'名：'+'张三丰'); print('电话 '+'138'+'00138000')" > <演示目录>/ps/a.md
bin/klode-kit dev privacy-scan --root <演示目录>/ps
echo $?
```

（命令里把关键字拆开拼接，是为了让本手册文件自己不触发仓库的隐私扫描；生成的 `a.md` 里是一行带「姓名」标签的编造姓名，和一个编造的 11 位手机号。）

应该看到（sha 值是哈希，不回显原文）：

```
a.md:1: name_label sha256=...
a.md:2: cn_mobile sha256=...
privacy_scan: 2 finding(s)
```

退出码 1。这里用的姓名和号码都是编造的，试验完把 `<演示目录>/ps` 整个移走。

### 2.4 发布清单

```sh
bin/klode-kit dev release-manifest --check
echo $?
```

应该看到 `release_manifest: current (<64 位十六进制>)`，退出码 0。如果输出不是 `current`，说明有运行文件和登记的 sha 对不上，请报。

### 2.5 release-check

```sh
bin/klode-kit dev release-check --mutation skip
echo $?
```

`--mutation skip` 是显式放弃变异测试得分一项（变异测试要跑很多小时，不属于手动测试范围；放弃会在报告的 `waived` 里列出，不是悄悄跳过）。

应该看到：

- 标准输出是一段 JSON（`"schema": "klode-kit.release_check/1"`），逐项列出 `items`，其中 `lane:unit`、`lane:contract_replay`、`lane:cli_integration`、`release_manifest`、`privacy_scan`、`doctor`、`install_fingerprint` 均为 `"ok": true`；`mutation` 为 waived。
- 标准错误里恰好两条缺项（以及你的树不干净时多一条 `dirty`）：

```
release_check: MISSING lane:model_integration: no receipt work/lanes/model_integration.json
  fix: python -m klode_kit.devtools.run_lane model_integration (a release run, not --not-release)
release_check: MISSING lane:host_acceptance: no receipt work/lanes/host_acceptance.json
  fix: python -m klode_kit.devtools.run_lane host_acceptance (a release run, not --not-release)
release_check: NOT releasable: 2 item(s) unmet -> exit 20
```

- 退出码 20。

这就是预期结果：**缺且只缺**两条要花钱的 lane。如果你还缺别的项，说明你的机器上有别的问题，请把那一项的 `MISSING` 行贴进报告。

`release-check` 完整检查项（来自源码文档）：五条 lane 回执（必须 PASS、非 CI 标记、绑定当前代码树、本机本解释器、7 天内）、`RELEASE.sha256` 最新、`git status` 干净（未跟踪文件也算）、隐私扫描 0 命中、`doctor` 0 缺项、安装指纹对得上这个检出和 `uv.lock`、变异得分 ≥ 0.80。

### 2.6 树变脏试验（可选）

目的：确认未跟踪的文件也会让 `release-check` 报 `dirty`。

```sh
touch dirty-probe.txt
bin/klode-kit dev release-check --mutation skip 2>&1 >/dev/null | grep MISSING
mv dirty-probe.txt <演示目录>/
```

应该多出一条 `MISSING dirty: 1 changed path(s): dirty-probe.txt ...`，并提示 `commit or stash the changes (the release is made from a clean, committed tree)`。最后一行把探针文件移走（不要用删除命令）。

改动**运行文件**（`src/` 下的 py 之类）会让三条 lane 回执同时作废，原因是 `code_tree_sha` 对不上；这个更重的试验不要求你做。

## 通过判据

- [ ] 2.1 三条 lane 均 `PASS`，0 failed、0 skipped（`cli_integration` 里因缺 MLX 权重或缺 wfviz 的失败要在报告里逐项说明）。
- [ ] 2.3 隐私扫描 0 命中；负面试验报出 2 条，退出码 1。
- [ ] 2.4 `release_manifest: current (...)`。
- [ ] 2.5 `release-check` 退出码 20，且缺项**恰好**是 `lane:model_integration` 和 `lane:host_acceptance`（树干净时）。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| `lane` 输出 `0 tests discovered` 并 FAIL | 这条 lane 以前有过用例，0 用例算失败。检查是否在仓库根运行、`.venv` 是否装了 dev 依赖（`scripts/install.sh --dev`）。 |
| `cli_integration` 有用例因缺依赖失败 | 设计如此（缺依赖就失败，不跳过）。照 `doctor` 的 fix 行补装，或把失败项写进报告。 |
| `release-check` 报 `lane:unit` 回执作废 | 你改过运行文件，或回执来自别的检出 / 别的 Python。重新运行 `bin/klode-kit dev lane unit`。 |
| `release-check` 报 `privacy_scan ... could not run` | 扫描时文件被改动或移走（例如另一个窗口在整理文件）。等工作树稳定后重跑。 |
| `release-check` 的 `install_fingerprint` 失败 | 安装之后又改过 `uv.lock`，或目录被移走。重新运行 `scripts/install.sh`。 |
| 想知道变异测试 | 维护者已跑过：`work/mutation.json` 汇总得分 0.84（阈值 0.80）。你不需要跑，它要很多小时；命令在 `README.md` 第 7 节。 |

## 费用与隐私

- 0 美元、0 次模型调用。`unit` 与 `contract_replay` 禁止网络连接。
- 负面试验只用编造的姓名和号码，且放在仓库之外的临时目录。回执里有你的本机路径，贴到 issue 前换成 `~`。
