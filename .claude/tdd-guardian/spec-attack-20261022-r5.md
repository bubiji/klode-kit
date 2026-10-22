# 规格攻击报告（第 5 轮）：test-matrix-20261022-klode-kit_v6_full.md

## 结论：BREACHED，有 2 个缺口（P0 1 个、P2 1 个），各补一句就能堵上

- 第 4 轮的 E、F、G 原攻击及我能想到的变种，v6 都挡住了。
- 08c-10 的计数约束和 §0.3 的改动没有引入空成立，也没有引入不可满足的情况。
- **缺口 H（K6，P0）**：05b-16（以及 05b-08）没有规定「把源文件换成未登记字节」用什么方式换，是原地覆盖还是 rename 替换。如果测试作者选了 rename，那么「只开一次 fd，先读一遍做校验，再 seek(0) 从同一个 fd 复制，不重验副本」这个错误实现能通过全部断言。
- **缺口 I（K5，P2）**：11a-13 的断言是「早于第一次打开 `builds/B/` 内文件」，但它没有按 F19 自己定的规则要求这个事件非空，路径也是按字面比对。实现如果经由 `current/` 符号链接去 load，这一条就空成立，第 2 轮 P2-7（先 load 后写租约）又回来了。

依据文件：
- `.claude/tdd-guardian/test-matrix-20261022-klode-kit_v6_full.md`（§0.3 第 48–126 行；00s-16 第 174 行；05b-08 第 529 行；05b-16 第 537 行；08c-10 第 720 行；11a-13 第 1088 行；F19 第 1302 行）
- `.claude/tdd-guardian/spec-attack-20261022-r4.md`

攻击模型边界照旧：
- 不篡改 0700 临时目录内的任何东西。
- 不考虑残留的 `TEST_SEAMS=1`。
- klode 子进程的写入按已声明的盲区处理。

---

## 一、第 4 轮重放

| 项 | 原攻击或变种 | v6 是否挡住 | 依据 |
|---|---|---|---|
| E | 校验前读两次源文件：一次的字节拿去送出，另一次只用来校验 | 挡住 | ③ 要求校验前只读打开恰好 1 次 |
| E 变种 | 校验前先复制到 `tmp/x.part`，校验后再 rename | 挡住 | ② 的观察范围已覆盖临时目录内的全部变更事件，`.part` 的写入发生在校验之前 |
| E 变种 | 校验前把源文件 copy 或 hardlink 到 `work/` 下（对源文件只读打开 1 次），再拿这份副本做校验并送出 | 不构成缺口 | 校验的字节和送出的字节是同一份 |
| E 变种 | 校验前起一个 argv 含源路径的子进程去读 | 挡住 | ③ 的子进程条款 |
| E 变种 | 原子写的正确实现 | 不再空成立，也不会误判红 | ② 已包含 rename 目标，并要求事件非空 |
| F | 解释器自身的写入（pyc、`/dev/null`、mkdtemp） | 挡住，正确实现可以满足 | 持久写已限定路径范围；设置了 `PYTHONDONTWRITEBYTECODE=1`；排除私有 TMPDIR |
| G | 租约走「临时名 + rename」、`mkdir` 或 `symlink` | 挡住 | 事件已泛化，并要求必须存在 |
| G 变种 | 「先开 `B.tmp` → 暂停点 → load → rename 成正式租约」，第一个事件很早，但租约要到 rename 后才生效 | 挡住 | 暂停期间 prune 看不到正式租约，会把 B 移走，行为断言「返回 B 的标记行、B 不在 `_trash`」判红 |
| 建议 1 | 「忽略 None、其余三路一致就 verified」 | 挡住 | 被删的那一行自成一段时，最小行数为 0，所以 verified 行数必须 ≤ 0；行数 n 的段里删一行，verified 行数必须 ≤ n−1 |
| 建议 2–5 | — | 已落实 | §0.3 第 59、111–120 行；00s-16 的盲区说明 |

---

## 二、新缺口

### P0 — 缺口 H（K6 / 05b-16、05b-08）：没有规定换源文件的方式

- **攻击**：同一个 fd 读两遍（对应第 9 类攻击「只对列出的那一种顺序正确」的变种）。
- **能通过全部用例的错误实现**：
  ```
  f = open(src, 'rb')                        # 校验前唯一一次打开，满足 ③
  h = sha256(f.read()); crashpoint("send.after_source_read")
  if h not in REG: exit(60)
  crashpoint("send.after_source_check")
  f.seek(0); copyfileobj(f, open(tmp/f"{h}.png", 'wb'))   # 第二次读取的字节被送出，从未校验
  crashpoint("send.after_copy"); crashpoint("send.after_copy_check")  # 不重验副本
  crashpoint("send.before_spawn"); spawn(...)
  ```
- **为什么每条用例都能通过**：
  - 测试如果用「写新文件再 `os.replace`」来换源文件，源路径会指向一个新 inode，而已打开的 fd 仍然读旧 inode，也就是登记过的字节。5 个暂停点上都走成功分支，收到的 sha 都在登记表内。05b-08 同理。
  - audit ①：顺序正确。
  - audit ②：临时目录内的写入全部发生在校验之后。
  - audit ③：只打开了 1 次，而且在 `after_source_read` 之前；没有子进程。
- **生产上的后果**：如果用户的标注工具或同步客户端在校验与复制之间原地改写了这张图，未登记、可能未打码的字节会外发。
- **为什么原地覆盖就能杀掉它**：
  - 在 `after_source_check` 处原地覆盖源文件后，`seek(0)` 读到的是新字节，副本又没有重验，假 claude 会收到未登记的 sha，判 FAIL。
  - 正确设计在原地覆盖下都是绿的：「读一次进内存 → 校验 → 从内存写副本」不受影响；「校验 → 按路径复制 → 重验副本」会以 exit 60 结束。
  - 所以原地覆盖严格优于 rename 替换。
- **要补的断言**（Level S4：仍是逐点 TOCTOU 等价性；Lane：contract_replay）：
  - 在 05b-16 和 05b-08 的「动作」栏写死替换方式：**原地覆盖**，即以 `r+b` 打开源路径、truncate 后写入未登记的字节。
  - 测试先断言替换前后 `st_ino` 不变，否则判 FAIL。
  - 可以再参数化一个 rename 替换，但不能只用 rename。

### P2 — 缺口 I（K5 / 11a-13）：「早于第一次打开 builds/B/」可能空成立

- **攻击**：经由 `current/` 去 load，并把 load 放在暂停点之前。这正是第 2 轮的 P2-7。
- **能通过全部用例的错误实现**：
  ```
  b = readlink(current)
  cfg = Config.load(project/"current")   # 打开的路径是 current/...，不是 builds/B/...；连带打开 sqlite 连接
  write_lease(b)
  crashpoint("mcp.after_resolve")
  serve(cfg)
  ```
- **为什么每条用例都能通过**：
  - audit：
    - 租约事件存在，且早于 `after_resolve`。
    - 按字面路径匹配，记录不到任何「打开 `builds/B/` 内文件」的事件，`lease_idx < min(空集, default=inf)` 空成立。
    - 这一行没有像 F19 规定的那样，同时要求被比较的事件至少出现一次。
  - 行为断言：暂停时 load 已经完成，fd 指向 B 的 inode，B 有租约所以不会被 prune 移走，返回的是 B 的标记行。
  - 11a-08、11a-11、11a-02 都没有这种竞态。
- **生产上的后果**：readlink 和 load 之间是无租约窗口。窗口里发生「发布 C + prune」时：
  - load 会经 `current/` 读到 C，或者读到 B 和 C 的混合；
  - 租约记在 B 名下，C 没有租约，C 在下一轮「发布 D + prune」时可能在服务中途被移走；
  - `kb_version` 报 B，实际用的却是 C。
- **要补的断言**（Level S3，保持原级别；Lane：cli_integration）：
  - 11a-13 audit 中「打开 build 内文件」的事件，按**事件发生时的 realpath**归类到 `builds/B/`，这样经由 `current/` 的打开也算在内；
  - 并且要求这类事件**至少出现一次**（不出现判 FAIL）。
  - §0.3 第 106 行同步改。

---

## 三、建议（不击穿不变量，可选）

1. **05b-16 ③ 的路径匹配**：建议按 `(st_dev, st_ino)` 或 realpath 判定「源文件」，argv 也按 realpath 比对。这样第二次打开改用 `Path.resolve()`、相对路径或 `dir_fd` 时，计数也不会漏。pytest 的 `tmp_path` 本来就已经 resolve 过，所以现实中漏计的概率低。
2. **00s-16 的 hook 覆盖不到本项目自己的 Python 子孙进程**：
   - 如果 writeback 或 run_batch 把实际写入交给自家起的 worker 子进程，而把 crashpoint 全留在父进程里，00s-16 会空成立；08c-13 在父进程崩溃时什么都还没写，也会全绿。
   - 建议：hook 通过 `sitecustomize`（`PYTHONPATH` 注入）安装，并合并所有 Python 子孙进程的事件；只有非 Python 的外部进程才算盲区。
3. **00s-16 下正确的原子写，在同一段里会有 2 次持久写**：同目录 `mkstemp` 加上 `os.replace` 是两个事件，`makedirs` 一次可能产生多个 `mkdir` 事件。这仍然可以满足：在两者之间加 crashpoint，08c-13 会在那一点崩溃，检查是否回滚，这本身也有意义。建议在 §0.3 写明这一预期，免得实现者为了变绿去过滤 hook。
4. **08c-10 计数约束的用词**：「该段在各路中实际出现的行数」，建议注明拼行按 1 行计、被拼进来的段外行不计入。现在这样写也能满足，只是可能有歧义。

## 被现有矩阵挡住的攻击（本轮新试）

| 攻击 | 被哪条用例挡住 |
|---|---|
| 校验前 hardlink 源文件到 `work/`，按路径校验，再从 hardlink 送出 | 05b-16 在 `after_source_check` 处换源文件（hardlink 和源文件共用 inode，原地覆盖时两边一起变；rename 替换时 hardlink 保留旧字节，并不泄漏） |
| 租约先写成 `B.tmp`，暂停点之后才 rename | 11a-13 的行为断言（prune 会移走 B） |
| 正确顺序（先写租约），但经由 `current/` 去 load | 11a-13 的行为断言（暂停时 current 已切到 C，结果返回 C 的标记行） |
| 08c-10 中「忽略 None、其余三路一致就 verified」 | v6 的计数约束 |
| 用 TMPDIR 私有目录做 build 的暂存，再 rename 进 `builds/` | 00s-16：rename 目标落在 `builds/` 下，计为 1 次持久写，语义正确 |

## 没有 law 的单元

00d2、00e、08b、02d、10a、10b、13a、14、30 的「无 law」声明仍然成立。

## 最小补丁

| # | 优先级 | 改动 | L·S |
|---|---|---|---|
| H | P0 | 05b-16 和 05b-08 的动作栏：「换源文件」固定为**原地覆盖**（`r+b` 打开、truncate 后写入），并断言替换前后 `st_ino` 不变；rename 替换只能作为附加参数 | L2·S4 / L2·S3 |
| I | P2 | 11a-13 audit 和 §0.3 第 106 行：「打开 build 内文件」按事件发生时的 realpath 归类到 `builds/B/`，并要求至少出现一次 | L2·S3 |

本轮只读，没有改任何文件。