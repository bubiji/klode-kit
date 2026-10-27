# 数据项目的备份方案

状态：**只有文档**。插件不实现任何备份动作（不建仓库、不推送、不拷贝），下面的命令都由你在数据项目里自己运行。这是需求里明确的决定：

> 备份：数据项目本地 git + 外置盘裸仓。

要备份的是**数据项目**（收件箱原件、记录、知识库），不是插件。插件本身在公开仓库里，丢了可以重装；数据项目里是医疗记录，丢了没有第二份。`docs/med-intake.md` 讲入库流程，本文讲入库之后怎么留住结果。

## 1. 备份什么、不备份什么

| 路径 | 怎么处理 | 理由 |
|---|---|---|
| `records/` | 本地 git 提交，推到外置盘裸仓 | 记录与全部修订正文，是最重要的结果；修订正文写一次、只读，适合 git。 |
| `kb/` | 本地 git 提交，推到外置盘裸仓 | 已发布的知识库、`PUBLISH.log`、`current` 指向。卡片由模型写成、不可逐字复现，丢了要重新花钱。 |
| 项目配置文件 | 本地 git 提交，推到外置盘裸仓 | 项目级配置、`.gitignore`、`.mcp.json` 等小文件。 |
| `_inbox/` | **不进 git**，用 `rsync` 拷到外置盘并校验 | 原始截图与拍照，体积大、二进制，git 不合适，但它们是唯一的原件。 |
| `work/` | 不进 git，随 `_inbox/` 一起拷到外置盘并校验 | 批次工作区：OCR 结果、审计、确认页、回执。能由原件重新生成，但拷下来可以省掉重跑的费用，并保留批次号与日期别名。 |
| `_private/` | **不进 git、不推送**，只拷到外置盘（建议放在加密卷里） | 脱敏密钥等。丢了就无法还原脱敏结果；进了 git 历史就再也撤不回。 |
| `_masked/`、`_mask_review/`、`_raw/` | 不进 git | 打码图与对照图含原始信息；都在数据项目的 `.gitignore` 里，`bin/klode-kit doctor --project <数据项目目录>` 会检查。 |

## 2. 第一次设置

1. 数据项目本地 git（只做一次）：

```sh
git init
git add records kb .gitignore
git commit -m "initial backup"
```

2. 外置盘上建裸仓，并把它加为远端（只做一次）：

```sh
git init --bare <外置盘挂载点>/<数据项目名>.git
git remote add backup <外置盘挂载点>/<数据项目名>.git
git push backup --all
```

远端**只用外置盘上的裸仓**，不要设成任何云端服务：仓库里是医疗记录。

## 3. 每次写回之后

人在确认页点选、`kb-audit --writeback` 写回之后，是备份的自然时机：

```sh
git add records kb
git commit -m "writeback <批次号或日期>"
git push backup --all
```

建库并发布之后同样提交一次（`kb/<库名>/current` 与 `PUBLISH.log` 变了）。

## 4. 原件与工作区：拷贝并校验

git 不管的 `_inbox/`、`work/`、`_private/` 用 `rsync` 拷到外置盘，用校验和而不是时间戳判断是否一致：

```sh
rsync -a --checksum _inbox/ <外置盘挂载点>/<数据项目名>-files/_inbox/
rsync -a --checksum work/ <外置盘挂载点>/<数据项目名>-files/work/
rsync -a --checksum _private/ <外置盘挂载点>/<数据项目名>-files/_private/
```

拷完再演练一遍，**不写入，只列出差异**：

```sh
rsync -nc -a --itemize-changes _inbox/ <外置盘挂载点>/<数据项目名>-files/_inbox/
```

差异清单非空，就说明这份拷贝不能算备份，先查原因再重拷。`work/` 与 `_private/` 同理。

## 5. 恢复

1. 数据项目：`git clone <外置盘挂载点>/<数据项目名>.git <新目录>`，得到 `records/` 与 `kb/`。
2. 把外置盘 `-files/` 下的 `_inbox/`、`work/`、`_private/` 拷回新目录（同样用 `rsync -a --checksum`）。
3. `work/` 恢复后，批次号与日期别名保持不变（别名是相对链接）。
4. 在新目录交互运行一次 `claude` 接受工作区信任，再运行 `bin/klode-kit doctor --project <新目录>`，见 `docs/install.md`。
5. 重新发布登记（`.mcp.json`、registry 等）属于写用户全局配置的动作，按 `README.md` 的「闸门」由你亲自确认后执行。

## 6. 不在范围内

- 自动定时备份、增量加密、云端同步：本方案不包含，也不由插件实现。
- 备份是否可读要靠你定期演练一次「恢复」，而不是只看推送成功。
