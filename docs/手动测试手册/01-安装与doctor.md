# T01 安装与 doctor

层：1 机器层　闸门：G4（安装依赖 / 下载权重）　时间：15–30 分钟　花费：0（全程不调用任何模型）

## 目的

确认一台干净的 macOS 机器能按 `docs/install.md` 把 klode-kit 装起来，`doctor` 能准确报告缺什么，缺项时给出的修复命令能用，插件能被 Claude Code 加载。自动化测试只在维护者的一台机器上跑过，这一项要在**你的**机器上复现。

## 前提

- macOS（唯一支持的平台）。Apple 芯片可选装 MLX 路。
- 已安装 `uv`（<https://docs.astral.sh/uv/>）和本机的 Python 3.12 或更高。安装脚本带 `--no-python-downloads`，不会替你下载解释器。
- 已安装 Claude Code（`claude`，版本不低于 2.1.285）。Codex CLI（不低于 0.163.0）只在第 5 章和真实云端路用，没有也能做本章，`doctor` 会报缺项。
- 本机引擎依赖：`poppler`、`tesseract`（需 `chi_sim` 与 `jpn` 语言包）、`node`。缺的话 `doctor` 会打印安装命令。
- **MLX 权重（几个 GB）不会被自动下载**，是否下载由你决定；不下载时 `doctor` 在 mlx 一项报缺，其余照常。

## 步骤

### 1.1 取代码

```sh
git clone https://github.com/bubiji/klode-kit.git
cd klode-kit
git rev-parse --short HEAD
```

记下最后一行的短 sha，写进报告的「代码版本」。

### 1.2 运行安装脚本

```sh
scripts/install.sh
```

可选参数：`--no-ocr`（不装 `ocrmac`）、`--with-mlx`（另装 `mlx-vlm`，只装库不下权重）、`--dev`（另装 pytest 等开发依赖，第 2 章要用）、`--keep-extra`。第 2 章要跑测试，建议这里直接加 `--dev`：

```sh
scripts/install.sh --dev
```

脚本做三件事：按 `uv.lock` 精确同步 `.venv`；导入冒烟（`klode_kit`、`klode.lib`）与 `bin/klode-kit --version`；写安装指纹 `.venv/klode-kit-install.json`。

应该看到：

- 过程中有 `install.sh: uv sync ...` 之类的行（待核：具体参数文字）。
- 最后一行：`install.sh: ok - klode-kit 0.1.0.dev0, klode 0.5.0`（格式取自脚本源码，待核：未在干净机器上实跑）。
- 退出码 0：`echo $?`。

没有 `uv` 时退出码是 40，提示 `install.sh: 'uv' is not on PATH. Install uv first ...`。

### 1.3 再跑一次（幂等）

```sh
scripts/install.sh --dev
echo $?
```

应该看到：同样的 `install.sh: ok ...` 一行，退出码 0，没有任何包被重装或移除（`docs/install.md` 保证重复运行不改变任何东西）。

### 1.4 版本与启动器

```sh
bin/klode-kit --version
```

应该看到：`0.1.0.dev0`（版本号可能随版本变化，以仓库当前为准）。

换个目录再跑一次，启动器应当仍能找到仓库：

```sh
cd ~ && <klode-kit 目录>/bin/klode-kit --version
cd <klode-kit 目录>
```

应该看到同样的版本号。

### 1.5 doctor

```sh
bin/klode-kit doctor
echo $?
```

应该看到（机器全部满足时）：

- 标准错误或终端里有一行 `doctor: all 16 checks passed`（条数随版本变化），随后是一段 JSON，里面有 `"schema": "klode-kit.doctor/1"`、`"ok": true`、`"missing": 0`。
- 退出码 0。
- JSON 的 `checks` 里依次出现这些 `id`：`platform`、`venv`、`import_origin`、`launcher`、`klode`、`klode_mcp`、`mcp_degrade`、`poppler`、`tesseract`、`ocrmac`、`mlx`、`node`、`claude`、`codex`、`codex_config`、`seams`，每项 `"ok": true`。

有缺项时，应该看到每个缺项一组两行，最后一行汇总，退出码 40：

```
doctor: MISSING project_trust [project]: workspace trust not accepted for .
  fix: 在数据项目目录交互运行一次 claude 并接受工作区信任（R2-07）: cd '.' && claude
doctor: 1 item(s) missing -> exit 40
```

（这是对一个未接受信任的数据项目运行 `doctor --project` 时的真实输出样例。）

**逐项核对**：把你机器上 `doctor` 报的缺项，和你自己知道的真实情况对一遍：

| 你知道的事实 | doctor 应该怎么报 |
|---|---|
| 没装 poppler | `poppler` 一项缺，fix 给出安装命令 |
| tesseract 没有中文或日文语言包 | `tesseract` 一项缺，detail 说明缺哪个语言 |
| 没下载 MLX 权重 | `mlx` 一项缺；**doctor 不会替你下载** |
| `claude` 版本低于 2.1.285 | `claude` 一项缺，detail 形如 `claude 2.1.x >= 2.1.285` 的反面 |
| 没装 Codex | `codex` 一项缺 |

如果事实和 doctor 的报告不一致（说有但其实没有，或反过来），这就是一个值得报的 bug。

照 `fix:` 行逐条修复，再运行 `bin/klode-kit doctor`，直到 `all ... checks passed`。**修复里涉及 `brew install` 或下载权重的，由你自己决定要不要做（G4）。**

### 1.6 数据项目的 doctor（可选，和第 7 章合做）

```sh
mkdir -p <演示目录>/data-demo && cd <演示目录>/data-demo
git init -q .
"$KK/bin/klode-kit" doctor --project .
```

应该看到两项缺：`project_trust`（还没接受信任）和 `project_gitignore`，后者的 fix 是：

```
printf '%s\n' _work/ _raw/ _masked/ _mask_review/ _private/ work/ >> '.gitignore'
```

执行这条 fix，并按第 7 章接受信任之后，再运行应当 `all ... checks passed`（项目段比机器段多 4 项：`project_trust`、`project_settings_local`、`project_gitignore`、`project_mcp`，总数待核）。

### 1.7 负面检查：坏掉的 Python

```sh
KLODE_KIT_PYTHON=/nonexistent/python bin/klode-kit --version
echo $?
```

应该看到（路径部分因机器而异）：

```
klode-kit: python from KLODE_KIT_PYTHON is missing or cannot run (needs >= 3.12): /nonexistent/python
  fix: <klode-kit 目录>/scripts/install.sh
```

退出码 40。启动器不会悄悄换用别的 Python。

### 1.8 插件静态检查

```sh
bin/klode-kit check-agents
```

应该看到：`check-agents: PASS (0 problem(s))`，退出码 0。

### 1.9 让 Claude Code 加载插件

在**任意目录**（比如第 7 章的演示项目）运行：

```sh
claude --plugin-dir <klode-kit 目录>
```

进入会话后，输入 `/klode-kit:` 看补全列表。应该看到五个 skill：`/klode-kit:kb-ocr`、`/klode-kit:kb-audit`、`/klode-kit:kb-build`、`/klode-kit:kb-check`、`/klode-kit:med-intake`，以及 Workflow `/klode-kit:kb-pipeline`（Workflow 需要你的方案支持，见 `docs/install.md` 第 5 节；列表里没有它不算失败，但请在报告里写明你的方案）。

这是会话级加载，退出即失效；本手册不做用户级安装（G1）。

## 通过判据

- [ ] 1.2 退出码 0，最后一行是 `install.sh: ok - klode-kit ...`。
- [ ] 1.3 第二次运行同样退出码 0，没有重装。
- [ ] 1.4 `--version` 在任意目录都能输出版本号。
- [ ] 1.5 `doctor` 的缺项报告与你机器的真实情况一致；按 fix 修复后 `all ... checks passed`，退出码 0。
- [ ] 1.7 退出码 40，并给出 `fix:` 行。
- [ ] 1.8 `check-agents: PASS`。
- [ ] 1.9 会话里能看到五个 skill。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| `install.sh` 退出码 40，提示没有可用的 Python 3.12 | 本机没有 3.12 或更高；用 `brew install python@3.12` 自行安装（你自己决定，G4）。 |
| 改过目录名或把仓库搬走后 `doctor` 异常 | 安装指纹里记的是旧路径。重新运行 `scripts/install.sh`：旧 `.venv` 会被**移动**到 `_trash/venv-<时间戳>-<pid>`，不会删除，确认无用后你自己处理。 |
| `doctor` 说 `claude` 版本太低 | 升级 Claude Code 到 2.1.285 或更高。 |
| `doctor` 的 `codex_config` 缺 | 说明没找到 Codex 配置文件，只影响第 5 章和真实云端路。 |
| `/klode-kit:` 补全里什么都没有 | 确认命令里写的是 `--plugin-dir <klode-kit 目录>`（仓库根，里面有 `.claude-plugin/`）；刚改过插件时运行 `/reload-plugins`。 |
| 想让 `klode-kit` 直接可用 | 会话里插件的 `bin/` 已在 PATH 上，可直接写 `klode-kit <命令>`；会话外请用 `<klode-kit 目录>/bin/klode-kit`。 |

## 费用与隐私

- 全程 0 次模型调用，0 美元。
- 安装脚本只装 PyPI 包，除 uv 自己的缓存外不写仓库以外的地方，不写 `~/.claude`、`~/.claude.json`。
- 报告里的路径请换成 `~`（见 README「如何报告结果」）。
