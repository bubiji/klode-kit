# 规格攻击报告（第 6 轮）

## 结论：SURVIVED

缺口 H、I 已经堵住。v7 改的三行（05b-08、05b-16、11a-13）都没有引入空成立。v7 补充一节里有一处措辞可能让 00s-16 对正确实现判红（见建议 S1），但它只会让正确实现判红，不会放过错误实现，不击穿 K1–K8，所以列为建议。按收敛规则判 SURVIVED。

依据文件：
- .claude/tdd-guardian/test-matrix-20261022-klode-kit_v7.md：§0.3 第 48–133 行；00s-16 第 181 行；05b-08 第 536 行；05b-16 第 544 行；11a-13 第 1095 行
- .claude/tdd-guardian/spec-attack-20261022-r5.md

攻击模型边界同前：不篡改 0700 临时目录；不考虑残留的 TEST_SEAMS；klode 子进程的写入按已声明的盲区处理。

---

## 一、第 5 轮重放

| 项 | 攻击或变种 | v7 是否挡住 | 依据 |
|---|---|---|---|
| H | 同一个 fd 读两遍：读一次做校验，`seek(0)` 再复制，副本不重验 | 挡住 | 05b-08 和 05b-16 都要求原地覆盖，并断言 `st_ino` 不变。在 `after_source_read` 或 `after_source_check` 暂停时原地覆盖，`seek(0)` 读到的是未登记的字节，副本又不重验，假 claude 收到的 sha 不在登记表内，判 FAIL |
| H 变种 | 用 mmap 读一次：对映射区算 hash，再从映射区复制 | 挡住 | 共享映射会反映原地写入，结果同上 |
| H 变种 | 用 `sendfile`、`copy_file_range` 或 `/dev/fd/N` 从已打开的 fd 复制 | 挡住 | 读到的都是当前 inode 的当前字节 |
| H 变种 | 校验前把源文件 hardlink 到 `work/`，读 hardlink 做校验，再从 hardlink 送出 | 挡住（两条独立理由） | ① 原地覆盖时 hardlink 也一起变；② ③ 要求源路径在校验前恰好被读打开 1 次，这里是 0 次 |
| H 变种 | 校验后用 stat 比对 `st_ino` 和 size，相同就按 fd 或路径复制，不重验 | 挡住 | 测试写入同样长度的未登记字节时，size 和 `st_ino` 都不变，未登记的字节会被送出 |
| I | 先经由 `current/` 做 `Config.load`，再写租约，然后才到 `after_resolve` | 挡住 | 按事件发生时的 realpath 归类：暂停前 `current` 指向 B，所以 `current/...` 的打开会计入 `builds/B/`。租约事件晚于这次打开，判 FAIL |
| I 变种 | 先 load，再 readlink，再写租约 | 挡住 | 原因同上 |
| I 变种 | 整个过程不在 Python 层打开 B 内的文件 | 挡住 | 「至少出现一次」判 FAIL |

## 二、v7 改动的空成立与可满足性检查

- **05b-08 和 05b-16 的原地覆盖**：
  - 正确设计都能变绿：
    - 「读一次进内存 → 校验 → 从内存写副本」不受影响；
    - 「校验 → 按路径复制 → 重验副本」会以 exit 60 结束，这是允许的结果；
    - 「先复制再校验副本」本来就被 ② 禁止。
  - `r+b` 加 truncate 的覆盖在暂停期间完成，不存在竞态。05b-08 要求的「20 次结果一致」仍然是确定的。
  - rename 替换只作为附加参数，所以挡住能力只会更强。
- **11a-13 按 realpath 归类，并要求非空**：
  - 这条断言没有空成立。
  - 只有一个可满足性前提，见建议 S2。
- **§0.3 建议 3（原子写在两步之间加 crashpoint，不得过滤 hook 事件）**：
  - 可以满足。`makedirs` 需要改写成手写的 mkdir 循环；按 §0.3 v6 的规定，循环里的点只算一个名字。
- **§0.3 建议 4（08c-10 的计数用词）**：只是把用词说清楚，不改变语义。
- **§0.3 建议 2（`sitecustomize` 注入）**：见建议 S1。

## 三、新攻击

没有找到能存活、并且击穿 K1–K8 的新攻击。另外试过下面三种：

| 攻击 | 被哪条用例挡住 |
|---|---|
| 校验后的复制改走 `clonefile`（APFS 的写时复制克隆），落点在临时目录外，克隆品不重验 | 05b-16 暂停参数：在 `after_source_check` 原地覆盖之后再克隆，克隆到的是未登记字节，判 FAIL |
| 校验前 clone 到临时目录 | ② 要求临时目录内的变更事件全部发生在校验之后 |
| mcp 在启动时预先 load，请求时只做 readlink | 11a-13：预先 load 打开 B 内的文件早于租约事件，判 FAIL |

## 四、建议（不击穿不变量）

**S1（最优先，可满足性问题）：§0.3 第 111 行和 00s-16 第 181 行对 klode 的定性前后不一。**
- §0.3 把「klode CLI」算作「非 Python 外部进程」。但 klode 是 Python 包：15-03 断言了 `import klode.lib` 和 `klode.__version__`，它的 CLI 应该也是 Python console script。
- 通过 `PYTHONPATH` 注入的 `sitecustomize` 会被 klode 子进程继承。这样 klode 在 `builds/` 下的大量写入会并入事件，而 klode 内部无法加 crashpoint。结果是 publish 全段里含 `klode build` 的那一段必然多于 1 次持久写，正确实现也无法通过 00s-16。§0.3 又禁止过滤 hook 事件，实现者只能靠「起 klode 时清掉 `PYTHONPATH`」碰运气变绿。
- 建议改为：`sitecustomize` 只在本项目进程里安装 hook，用一个专用环境标记（如 `KLODE_KIT_AUDIT_ROLE`）判断，或检查 `__main__` 是否属于 klode_kit；klode 进程即使是 Python，也明确算作盲区。
- 00s-16 那一行同步写明 hook 的安装方式（现在仍写「子进程安装 `sys.addaudithook`」），盲区说明也改用同一套措辞。

**S2：11a-13 的「打开 B 内文件」事件类型太窄。**
- 如果 klode 0.5.0 的 `Config.load` 只经 C 层的 sqlite 打开库文件，Python 的 `open` audit 事件不会触发，「至少出现一次」会让正确实现判红。
- 建议把 `sqlite3.connect`（参数是 database 路径）和 `os.open` 也算进这一类，同样按事件发生时的 realpath 归类。
- 写红用例时，先确认 klode 0.5.0 下这一类事件确实非空。

**S3：用 `dir_fd` 的相对路径打开时，在 hook 里求 realpath 会解析错。**
- 建议 hook 遇到 `dir_fd` 不为 None 的事件时直接判 FAIL，或用 `/dev/fd/<dir_fd>` 拼出完整路径。
- 05b-16 ③ 括号里的「建议：按 `(st_dev, st_ino)` 或 realpath 判定」也可以借机转成硬性规定。现实中漏计的概率低，所以不强制。

## 被现有矩阵挡住的攻击

| 攻击 | 被哪条用例挡住 |
|---|---|
| 同一个 fd `seek(0)` 再复制，副本不重验 | 05b-16 和 05b-08 的原地覆盖加 `st_ino` 断言 |
| 用 mmap 或已打开 fd 的零拷贝复制 | 同上 |
| hardlink 校验加送出 | 原地覆盖；05b-16 ③ |
| 校验后只比对 stat，不重验 | 原地覆盖时写入等长的未登记字节 |
| 经由 `current/` 先 load 后写租约 | 11a-13：按 realpath 归类、要求非空、事件顺序 |
| 启动时预先 load | 11a-13 的事件顺序 |
| 两步原子写的正确实现 | §0.3 v7 建议 3：加 crashpoint，由 08c-13 和 09d-02 枚举 |

## 没有 law 的单元

00d2、00e、08b、02d、10a、10b、13a、14、30 的「无 law」声明仍然成立。

## 最小补丁

无，结论是 SURVIVED。建议 S1 虽然不击穿不变量，但会让 00s-16 对正确实现判红，建议在开始实现前顺手改掉。

本轮只读，没有改任何文件。