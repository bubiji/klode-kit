# 安装

这份文档只讲怎么把 klode-kit 装起来、让 Claude Code 认出它、需要哪些前提。项目是什么见 `README.md`，目录各是什么见 `docs/目录结构.md`。

涉及 Claude Code 自身机制的说法都附官网页面；官网查不到的地方明确写了「检索无结果」。

## 1. 环境前提

| 前提 | 说明 |
|---|---|
| macOS | 唯一支持的平台。本机 OCR 用 Apple Vision（`ocrmac`）和 MLX；测试里回放录制的键是衍生图的 PNG 字节，这些字节在 macOS 和 Linux 的 Pillow 上不同，所以 CI 也跑在 macOS 上。 |
| `uv` | 安装脚本用它按锁文件建环境。没有就退出（退出码 40）并提示。<https://docs.astral.sh/uv/> |
| Python 3.12 | 必须是本机已有的 3.12 或更高版本。脚本带 `--no-python-downloads`，不会替你下载解释器。 |
| `klode==0.5.0` | 来自公开 PyPI（MIT），已钉在 `pyproject.toml` 与 `uv.lock`，随安装脚本一起装进 `.venv`。代码是按这个版本写的，不要换版本。 |
| `ocrmac`（extra `ocr-mac`） | 本机 Vision 一路。macOS 上默认安装，`--no-ocr` 跳过。 |
| `mlx-vlm`（extra `mlx`） | 可选，仅 Apple 芯片。用 `--with-mlx` 安装。**只装库，不下载模型权重**；权重（几个 GB）要你自己决定下载，缺权重时 `doctor` 会报出来。 |
| `claude`、`codex` | 云端两路和宿主验收用的命令行。`doctor` 检查版本下限（Claude Code 不低于 2.1.285，Codex CLI 不低于 0.163.0）。 |
| `poppler`、`tesseract`、`node` | 分别用于 PDF 文字层、几何参照路（需 `chi_sim` 与 `jpn` 语言包）、确认页导出与 workflow 测试。缺什么 `doctor` 会打印安装命令，例如 `brew install poppler`。 |

安装脚本只装 PyPI 包，不改你的全局配置，不写 `~/.claude`、`~/.claude.json`，除了 uv 自己的包缓存外不写仓库以外的任何地方。

## 2. 安装脚本

```sh
scripts/install.sh
```

可以在任何目录运行：脚本根据自己的真实路径找到仓库。选项：

| 选项 | 作用 |
|---|---|
| `--no-ocr` | 不装 `ocrmac`。 |
| `--with-mlx` | 另外装 `mlx-vlm`（Apple 芯片）。非 Apple 芯片会打印提示并跳过。 |
| `--dev` | 另外装开发依赖（pytest、hypothesis 等），要跑测试就加。 |
| `--keep-extra` | 不移除锁文件之外的包。默认是精确同步（多余的包会被移除）。 |

退出码：0 成功；2 参数不对；40 环境问题（没有 `uv`、没有可用的 Python 3.12、冒烟失败、`uv sync` 失败）。

脚本做的事：

1. 按 `uv.lock` 精确同步 `.venv`（`uv sync --locked`）。重复运行不改变任何东西。
2. 冒烟：导入 `klode_kit` 与 `klode.lib`，运行 `bin/klode-kit --version`。
3. 写安装指纹 `.venv/klode-kit-install.json`：仓库真实路径、虚拟环境的 Python、锁文件 sha、klode 与 klode-kit 版本（不含时间戳，所以幂等）。

装好后检查先决条件：

```sh
bin/klode-kit doctor
bin/klode-kit doctor --project <数据项目目录>
```

`doctor` 逐项真实运行（环境导入、启动器、klode、poppler、tesseract、node、claude / codex 版本……），缺项时退出码 40 并给出修复命令；带 `--project` 时还检查数据项目的工作区信任、`.gitignore` 与 `.claude/settings.local.json`。它从不下载任何东西。

### 2.1 目录改名或搬走之后

虚拟环境里的路径容易随目录改名失效，所以启动器不用虚拟环境里的 console script，而是直接运行 `.venv/bin/python -m klode_kit`，并从自己的真实路径定位仓库（跟随符号链接）。实际表现：

- 改名后，`bin/klode-kit` 和 `doctor` 照常工作；安装指纹里记的仍是旧路径，这就是「环境是在别处建的」的标记。
- `.venv` 里的 Python 跑不起来时，启动器退出码 40，并打印修复命令 `scripts/install.sh`。不会悄悄换用别的 Python。
- 重新运行 `scripts/install.sh`：发现 Python 失效，或指纹里的路径与当前真实路径不同，就把旧的 `.venv` **移动**到 `_trash/venv-<时间戳>-<pid>` 再重建。脚本里没有任何删除命令，旧环境完整保留，确认无用后你自己处理。
- 可以用环境变量 `KLODE_KIT_PYTHON` 指定另一个解释器（同样要求 3.12 或更高，同样会被检查）。

## 3. 让 Claude Code 加载插件

最简单的是只对一次会话加载：

```sh
claude --plugin-dir <klode-kit 目录>
```

`--plugin-dir` 加载的是会话级插件，退出即失效；官网说明见 <https://code.claude.com/docs/en/plugins/cli-reference>。长期安装到用户级或通过插件市场分发是另一件事，本仓库的测试一律只用 `--plugin-dir`，不替你写用户级配置。

插件的 `bin/` 目录会进 Bash 工具的 PATH（<https://code.claude.com/docs/en/plugins/components>），所以会话里可以直接用 `klode-kit <命令>`。

在数据项目的目录里启动 `claude`（数据都在那里读写），不要在插件目录里启动。

## 4. 命令短名

插件的 skill 命令全名是 `/klode-kit:<skill 名>`，例如 `/klode-kit:kb-check`。官网：没有别的命令占用同一个名字时，短名 `/<skill 名>` 也能调用同一个 skill，所以 `/kb-check` 与 `/klode-kit:kb-check` 并存（<https://code.claude.com/docs/en/skills>，「How a skill gets its command name」一节）。短名被别的命令、个人或项目 skill 占用时，只有全名可靠，所以文档、skill 正文和测试一律写全名。

本插件的命令：

| 全名 | 短名 | 用途 |
|---|---|---|
| `/klode-kit:kb-ocr` | `/kb-ocr` | 一批图或 PDF 四路 OCR。 |
| `/klode-kit:kb-audit` | `/kb-audit` | 对齐、投票、确认页、写回、重开字段。 |
| `/klode-kit:kb-build` | `/kb-build` | 书或核对过的记录建成可逐字引用的 klode 库。 |
| `/klode-kit:kb-check` | `/kb-check` | 整库检查并打印检查回执。 |
| `/klode-kit:med-intake` | `/med-intake` | 当天截图入库（两段式）。 |
| `/klode-kit:kb-pipeline` | （官网只写了全名） | Workflow：流水线两段式（见 5）。 |

Workflow 同样按插件名加前缀：官网说明插件里的 workflow 以 `/<插件名>:<meta.name>` 运行（<https://code.claude.com/docs/en/workflows>，「Distribute a workflow in a plugin」）。

## 5. Workflow 前提

`/klode-kit:kb-pipeline` 是 Claude Code 的 Workflow（动态工作流）。官网：Workflow 在所有付费方案、带 Anthropic API 的使用方式，以及 Amazon Bedrock、Google Cloud Agent Platform、Microsoft Foundry 上可用；Pro 方案要在 `/config` 的 Dynamic workflows 一行里手动打开。官网没有列出免费方案；Pro 要先打开开关（<https://code.claude.com/docs/en/workflows>）。组织若用托管设置关掉了 Workflow，同样不可用。

几点实际影响：

- 没有 Workflow 也能用：同一条流水线可以直接用命令行跑，`bin/klode-kit pipeline --batch <收件箱> --project <数据项目目录>`，Workflow 只是把阶段顺序包了一层。
- Workflow 是两段式。第一段 `preflight`、`ocr`、`audit` 到「待确认」就结束（退出码 10 是正常停止）；你在确认页点选并导出 `choices.json` 之后，带 `resume` 和 `choices` 再跑第二段（`writeback`、`build`、`check`）。官网也建议需要人工确认的阶段各自作为独立 workflow 运行。
- 第二段**默认止于 check，不发布**。只有参数 `publish` 严格等于 `true` 才追加发布，发布会写数据项目的 `.mcp.json` 等文件，见 `README.md` 的「闸门」。
- workflow 脚本里 `${CLAUDE_PLUGIN_ROOT}` 是否被替换：官网检索无结果（文档只列了 skill、agent、hook、MCP 等位置）。脚本因此把它当字面文本写进提示词，并说明 shell 里没有该变量时直接用 `klode-kit`（插件 `bin/` 在 PATH 上）。
- Workflow 启动时 Claude Code 会询问是否允许运行，按提示批准；每个阶段是一个子 agent，要执行 Bash 命令，所以需要下一节的权限规则。

## 6. 信任前提

数据项目目录需要先接受 Claude Code 的**工作区信任**。官网：交互会话在你没信任过的目录里启动时会显示信任对话框；项目 `.claude/settings.json` 里的 `permissions.allow` 规则，只有接受了信任对话框之后才生效；`claude -p`（非交互）永远不显示这个对话框（<https://code.claude.com/docs/en/permissions>，「Project allow rules and workspace trust」；<https://code.claude.com/docs/en/security>，「Trust verification」）。另外在家目录直接启动时，信任只在当次会话有效、不写入磁盘。

所以第一次用之前，在数据项目目录里交互运行一次：

```sh
cd <数据项目目录>
claude
```

接受信任对话框后退出即可。`bin/klode-kit doctor --project <数据项目目录>` 会检查这一点；检测不到（例如设置了 `CLAUDE_CONFIG_DIR`）按未通过处理并给出这条提示，这是有意保守。

项目 `.mcp.json` 里的 MCP 服务器也有自己的批准流程：在没有信任的目录里不会被批准，会停在「待批准」（<https://code.claude.com/docs/en/mcp>，「Project scope」）。发布时登记的 `.claude/settings.local.json` 里 `enabledMcpjsonServers` 就是在做这件事，所以它必须不入库、可写；`doctor` 同样检查。

## 7. Bash 权限规则模板

插件的五个 skill 在 frontmatter 里自带 `allowed-tools: Bash(*/bin/klode-kit *)`，通过 skill 调用时不需要你额外配置。官网：`*` 匹配任意字符序列，结尾的 ` *` 也匹配不带参数的裸命令（<https://code.claude.com/docs/en/permissions>，「Wildcard patterns」）。

如果你要让 Workflow 的子 agent 或你自己直接在会话里运行 `klode-kit` 命令而不被逐条询问，才需要在**数据项目**里加规则。把它写进 `.claude/settings.local.json`（不入库）：

```json
{
  "permissions": {
    "allow": [
      "Bash(<klode-kit 目录>/bin/klode-kit *)"
    ]
  }
}
```

要点：

- 把 `<klode-kit 目录>` 换成你本机仓库的绝对路径。写绝对路径，`*` 只放在末尾。官网对 settings 文件里「`*` 出现在决定命令的那个词之前」的 allow 规则会在启动时警告，因为它会连带放行插入的选项（<https://code.claude.com/docs/en/errors>，「Has a wildcard before the rest of the command」）。skill 自带的 `Bash(*/bin/klode-kit *)` 写在 frontmatter 里，不在 settings 文件里；这个前导 `*` 在宿主里是否被同样警告没有实测，所以 settings 里不要照抄，用绝对路径最稳妥。前导 `*` 还会匹配任意目录下叫 `bin/klode-kit` 的程序，绝对路径没有这个问题。
- 项目 settings 里的 allow 规则只有在接受工作区信任之后才生效（见上节）。
- 只放行 `klode-kit` 这一个程序；不要为了省事放行全部 Bash 命令。云端两路另有隔离与闸门，见 `README.md`。
- 真实模型调用（`claude` / `codex`）不要加进 allow：它们本来就要走发送器和闸门，多一次询问是有意的。

## 8. 数据项目的准备清单

1. 目录是 git 仓库（备份见 `docs/med-intake-backup.md`）。
2. `.gitignore` 含 `_work/ _raw/ _masked/ _mask_review/ _private/ work/`。
3. 在该目录交互运行一次 `claude` 并接受信任。
4. `bin/klode-kit doctor --project <数据项目目录>` 退出码 0。
5. 开始：`/klode-kit:med-intake --inbox _inbox/<日期>/`。
