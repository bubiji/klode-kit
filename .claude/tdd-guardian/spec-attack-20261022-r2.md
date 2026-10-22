# 规格攻击报告（第 2 轮）：test-matrix-20261022-klode-kit_v3.md

## 结论：BREACHED（发现 10 个缺口：P0 2 个、P1 4 个、P2 4 个）

第 1 轮的 16 个缺口里，15 个已经被 v3 的新用例真正挡住。没挡住的是缺口 6（TOCTOU）：05b-08 换成了暂停点写法，但这条用例在「暂停点没被触发」时仍然判绿，所以第 1 轮那个「复制后不再校验」的实现照样能通过。

另外有三处问题：
- 新增的两个测试缝（PAUSE_AT / CRASH_AT）在生产环境下的行为没有任何用例约束。
- 有两条新 law 能被空成立：08c-10 的判定条件取自实现自己的输出；11a-08 的加强断言在测试里永远为真。
- 07b-12 在「相邻同值行」这一生成域上，连正确实现也满足不了。

**补完 P0 和 P1 的 6 项就可以定稿。**

依据文件：
- 矩阵 `.claude/tdd-guardian/test-matrix-20261022-klode-kit_v3.md`
- 第 1 轮报告 `.claude/tdd-guardian/spec-attack-20261022-r1.md`
- 计划 `.claude/tdd-guardian/plan-20261022-klode-kit.md`（先读了 Deferred 清单，下面的缺口都不在其中）

---

## 一、第 1 轮 16 个缺口复核

我对每个缺口重新跑了一遍原来的攻击实现。

| 缺口 | 原攻击 | v3 是否挡住 | 挡住它的用例 |
|---|---|---|---|
| 1 | 只在 kb_version 里刷新 pool | 挡住 | 11a-11：切换后第一条请求就是 search，三种降级参数化；11a-02 的生成域里有「只查询」 |
| 2 | no_geometry 没有传到 gate | 挡住（针对这个 fixture） | 08c-17 全链。不过 07b-04 和 08c-10 还有漏洞，见新缺口 5 |
| 3 | 按下标 (i,i) 硬对 | 挡住 | 07b-12 要求 pairs 与真实对应完全相等；07b-07 加强为断言缺行的行号。但 07b-12 的生成域有问题，见新缺口 6 |
| 4 | choices 只覆盖部分字段 | 挡住（前提是 F14 拍板） | 08c-18。重复 field_id 的变体见 P2-8 |
| 5 | 发布未校验的 build | 挡住 | 09d-17 四种参数化；09d-02 加强断言并扩大崩溃点范围 |
| **6** | **只查源文件 sha，复制后不再查** | **没挡住** | 05b-08 的「二选一」断言在暂停点没被触发时为真，见新缺口 1 |
| 7 | 只看 flag 在不在 / 重复 flag / `--x=v` / settings 路径 | 挡住 | 00c1-18、00c1-19 |
| 8 | 执行的 argv 不是审查过的那份 | 只挡住了 argv | 05b-15。实际执行的 env 仍然没人断言，见新缺口 2 |
| 9 | 校验器只对 6 类损坏完备 | 挡住 | 00b3-14…19；00b3-12 扩到全表 × 任意一路 |
| 10 | effective 只推进 rev | 挡住 | 08c-08 要求逐字段相等，并检查 superseded_by |
| 11 | 值以外的列取自最新候选 | 挡住 | 01a-03 四列都不同 |
| 12 | chmod 只作用在顶层 | 挡住 | 09b-12 |
| 13 | codex 版本下限 | 挡住 | 00c1-20 |
| 14 | skipped_newer 按 build_id 判断 | 挡住 | 09d-18 |
| 15 | 对 notification 也回响应 | 挡住 | 11a-12 |
| 16 | 选了缺失 cell 的那一路 | 挡住 | 08c-19 |
| env 白名单 | `ANTHROPIC_*` 通配 | 只挡住了 checker 层 | 00c1-08 |
| 租约窗口 | 先 load 后写租约 | **没挡住**（§9 声称已关闭） | 11a-08 的加强断言能空成立，见 P2-7 |

---

## 二、新缺口

### P0

#### 新缺口 1（K6 / WI-05b）：05b-08 在暂停点没触发时仍然判绿
- **攻击**：静默失败（测试缝根本没生效），加上暂停点的位置由实现者自己决定。
- **能通过全部用例的错误实现**：
  ```
  copy(src, tmp/der.png)                 # 先复制
  assert sha(read(src)) in REG           # 再校验源文件，不校验副本
  crashpoint("send.after_source_check")  # 暂停点放在复制之后；或者干脆不调用它
  spawn(claude, tmp)
  ```
- **为什么能通过**：
  - 05b-08 的断言是「exit 60 且 0 条调用」或「收到的 sha ∈ 登记表」二选一。
  - 暂停点不存在、或者放在复制之后时，测试替换的源文件已经影响不到副本。假 claude 收到的是登记过的字节，走的是「成功」那一支，20 次结果都一致。
  - 上次运行残留的 `.go` 文件也会让暂停失效，结果同样是判绿。
- **生产上的后果**：真正的竞态窗口在「复制」和「校验源文件」之间，这个用例测不到。副本里可能是未登记的字节（没打码的原图）却被外发，K6 被击穿。如果 `crashpoint()` 这一行被 mutation 删掉，这条用例也照样是绿的。
- **要补的用例**：`test_toctou_swap_at_every_send_point`。L2·S4，contract_replay。
  - **做法**：把 send 模块从「读源文件」到「spawn」之间的全部暂停点做成一个**固定清单**，写进规格，不由实现者自己定。对清单上的每个点 p 各跑一次：
    - 子进程到达 p 时写 `<p>.reached`，测试等到这个文件出现才替换源文件。超时没出现就判 **FAIL**，不能走「成功」分支。
    - `.go` 文件由子进程消费后删除。
  - **断言**：对每个 p 都满足：要么 exit 60 且 0 条调用，要么假 claude 收到的 sha ∈ 登记表；并且 `reached` 必须出现过。

#### 新缺口 2（K6 / 隔离，WI-05b）：审查了 env，执行时却继承了完整的 env
- **攻击**：只做了一半（缺口 8 的 env 版）。
- **错误实现**：
  ```
  receipt = isolation_check(argv, env=whitelisted_env(), cwd=tmp)   # PASS
  subprocess.run(argv, cwd=tmp)        # 没传 env=，子进程继承了 os.environ
  ```
- **为什么能通过**：
  - 00c1-08 只测 checker 在给定 env 上的判定。
  - 05b-15 只比较实际执行的 argv 与审查过的 argv。
  - 05b-09 断言了实际的 cwd，但没有断言实际的 env。
  - 测试进程本身的环境里没有 `ANTHROPIC_BASE_URL` 这类变量，所以继承也看不出差别。
- **生产上的后果**：用户 shell 里的 `ANTHROPIC_BASE_URL`、`CLAUDE_CONFIG_DIR` 会进入云端子进程。打码图可能被转发到任意端点，或者子进程读取用户配置，F15 的收窄等于失效。
- **要补的用例**：把 05b-15 扩成 `test_executed_env_cwd_argv_equal_checked`。L2·S3，contract_replay，claude 和 codex 两路参数化。
  - **fixture**：测试进程先在自己的环境里放进 `ANTHROPIC_BASE_URL`、`CLAUDE_CONFIG_DIR` 和一个随机的 `FOO`。
  - **断言**：假可执行文件记录下的 env 键集合 ⊆ `{PATH, HOME, TMPDIR, LANG, ANTHROPIC_API_KEY}`；对记录下的 (argv, env, cwd) 三元组重跑 `isolation_check`，结果为 PASS。

### P1

#### 新缺口 3（测试缝本身，§0.3 / WI-00s）：生产环境误设变量时，测试缝照样生效
- **攻击**：没有任何用例约束测试缝在生产环境下的行为（属于「无状态边界」）。
- **错误实现**：
  ```
  def crashpoint(name):
      if os.environ.get("KLODE_KIT_CRASH_AT") == name: os.kill(os.getpid(), SIGKILL)
      if os.environ.get("KLODE_KIT_PAUSE_AT") == name:
          while not exists(f"{os.environ.get('KLODE_KIT_PAUSE_DIR', '/tmp')}/{name}.go"): sleep(0.1)
  ```
- **为什么能通过**：§0.3 只写了「默认什么都不做」，具体怎么约束交给 WI-00s「记一次决定」，没有对应的用例。所有用到测试缝的用例都只在测试里设变量。
- **生产上的后果**：
  - shell 里残留一个 `PAUSE_AT`，send 或 writeback 就会无提示地永久挂起，同时一直持有 fcntl 锁，之后的运行全部 exit 40。
  - 残留一个 `CRASH_AT`，每次 resume 都在同一个点被 SIGKILL。
  - `PAUSE_DIR` 默认取 `/tmp` 时，本机其他用户可以控制放行时机。叠加新缺口 1 的那种实现，就成了一个能确定性利用的 TOCTOU。
- **要补的用例**：`test_seams_inert_unless_test_mode`。L2·S3，contract_replay。
  - **做法**：不设测试模式开关（例如 `KLODE_KIT_TEST_SEAMS=1`，由 conftest 设置；具体开关名由 WI-00s 决定），或者 `KLODE_KIT_REAL=1` 时，设上 `PAUSE_AT=send.after_source_check` 和 `CRASH_AT=writeback.<任一点>`，运行 send 和 writeback。
  - **断言**：两者都在超时之内正常完成，没有收到 SIGKILL；stderr 给出「测试缝变量已忽略」的警告，或者 doctor 以 exit 40 报出（二选一，在 F9 定下来）。
  - 另加一条：`PAUSE_AT` 已设、`PAUSE_DIR` 未设时，不能退回到全局可写目录，必须拒绝。

#### 新缺口 4（K3/K5/K8，08c-13、09d-02、00b1-11、04b-13）：「枚举所有写点」的「所有」由实现者自己定义
- **攻击**：只覆盖列出的那些点。两个持久写之间只放一个 crashpoint，它们之间的窗口就从来没被枚举到。
- **错误实现**：
  ```
  crashpoint("writeback.before_commit")
  write_effective(rec)          # 持久写 1
  append_commit_marker(cid)     # 持久写 2；两者之间没有 crashpoint，恢复逻辑也没处理这个窗口
  ```
- **为什么能通过**：枚举出来的点都能正确恢复。随机 SIGKILL 落进两次写之间几微秒的窗口，概率和 v1 的 05b-08 一样低，这正是缺口 6 当时批评的那种「靠运气的放行」。
- **生产上的后果**：effective 已经写了，提交标记还没有。重启后既不补完也不回滚，K3 的「全提交或全回滚」被击穿。publish 和 receipt 有同样的问题。
- **要补的用例**：`test_crashpoints_partition_durable_writes`。L2·S4，contract_replay。
  - **做法**：在子进程里装一个 `sys.addaudithook`（这是测试端的观察，没有 mock 任何东西），记录 `open`（写模式）、`os.rename`/`os.replace`、`os.symlink`、`os.remove`、`os.chmod` 这些事件，以及每个事件之前最近的一个 crashpoint 名字。对 writeback、publish、receipts、run_batch 各跑一遍正常流程。
  - **断言**：任意两个相邻的 crashpoint 之间至多一个持久写；08c-13、09d-02 的枚举清单 == 这次运行实际观察到的 crashpoint 集合。

#### 新缺口 5（K1 / WI-07b、08c-10）：08c-10 的判定条件取自实现自己的输出；07b-04 不区分「行级」和「页级」
- **攻击**：新 law 能空成立；另外，approx 是否有佐证，按页判断而不是按行判断。
- **错误实现**：
  ```
  # align：只要页上有一条 exact 基准路，就无条件采用 approx 框
  if page_has_exact_route: use(approx_boxes)            # 应当逐行检查同一行有没有 exact 佐证
  else: mark_all_cells("no_geometry")
  ```
- **为什么能通过**：
  - 07b-04 和 08c-17 的 fixture 都是「这一行或整页只有 approx」。只要页上没有别的 exact 行，按页判断也会把它标出来。
  - 07b-05 的这一行恰好有佐证，与按页判断的结果一致。
  - 08c-10 断言「verified(auto) ⟺ … `uncertain==[]`」。如果 `uncertain` 和 `agree` 读的都是实现自己产出的结果，那么 align 不标 `no_geometry` 时 `uncertain` 就是空的，verified 也就成立，等式两边永远一致。
- **生产上的后果**：一页里 tesseract 只漏掉了某一行，那一行只剩 vision 的 approx 框，四路又恰好读得一样，就会被自动判为 verified，绕过 R2-08。
- **要补的用例**：
  - 加强 07b-04。L1·S3。fixture 是同一页：其余行都有 tesseract 的 exact token，只有目标行只有 approx。**断言**：只有目标行的 cell 是 `no_geometry`，其他行都不是。
  - 修改 08c-10 的 law。L2·S4。等式右边改成**由生成器真值计算**：该记录的每个 cell 四路齐，值全等，所在行有 exact 几何，并且没有删行或拼行碰到这条记录。不能读实现产出的 `uncertain` 或 `agree`。

#### 新缺口 6（K1 / 07b-12）：新 law 在相邻同值行上连正确实现也满足不了
- **这不是可存活的攻击，而是 law 本身不可满足**：两行值相同且没有几何信息（云端路的 `bbox_px` 是 null）时，删掉其中一行，文本上分不出删的是哪一行。正确的对齐也只能任选其中一行，「pairs 完全相等」会随机失败。实现者最后只能把这种情况从生成域里排除，dup10 形态于是又没人测了。
- **要修改的用例**：07b-12。L1·S4。
  - **断言**改为：每个 pair 两侧的文本等于真实对应两侧的文本（按值等价，而不是按下标完全相等）；`unaligned` 落在被删行所在的「同值连续段」之内；不同值的行一律不错位。这样仍然能挡住第 1 轮缺口 3 的「整体错位一格」。

### P2

7. **11a-08 的加强断言能空成立（「租约窗口」没有真正关闭）**：
   - 这条用例运行期间没有并发的 prune，所以「写完租约后目标 build 不在 `_trash`」永远为真。
   - 「租约的 mtime 早于 pool 加载」：pool 加载的时间点没有可观察量。
   - 于是「先 load、后写租约」的实现能通过。
   - 要补的用例：`test_lease_before_load_under_prune`。L2·S3。在 `mcp.after_resolve` 暂停（要求出现 reached 标记，规则同新缺口 1）。暂停期间发布 C，然后以 keep=0 运行 prune，再放行。**断言**：这次请求不报错；它实际使用的 build 不在 `_trash` 里；持有租约期间该 build 没有被移走。
   - 也可以选择在 §9 把这一项改回「未关闭的残余风险」。
8. **08c-18 能被按条数计数绕过**：如果实现只检查 `len(items) >= len(disputed)`，那么用 3 个 item 覆盖 2 个 field（其中一个 field_id 出现两次）就能通过 08c-18。要补 `test_choices_duplicate_field_id_rejected`，L2·S3：exit 30，该记录零写入。也可以改成在 00b1 的 schema 层直接拒绝重复的 field_id。
9. **11a-02 的请求类型可能只覆盖到 search**：如果包装只在 search 的覆写里刷新 pool，切换后第一条请求是 verify（或 zoom）时会读旧 build。要求 11a-02 的请求类型生成域 == 11a-04 给出的完整 TOOLS 集合；11a-11 也对「第一条请求」做同样的参数化。L3·S4。
10. **09d-17 的 ④ 和 ③ 只测了浅层**：
    - ④「目录仍可写」：按 fixture 的构造方式，只检查顶层 `os.access` 的实现也能通过。应改为「顶层只读，但内部某个文件可写」。
    - ③：只测了 MANIFEST 校验失败，没测 MANIFEST 缺失。`if exists(M) and not ok` 这种静默放行能通过。
    - 两项都补成参数化。L2·S3。

---

## 被现有矩阵挡住的攻击（本轮新试的）

| 攻击 | 被哪条用例挡住 |
|---|---|
| choices 校验只取同名 flag 的第一个值 | 00c1-18 ② |
| 发布时只校验 check_receipt，不比对 MANIFEST | 09d-17 ③、09d-02 |
| 校验器对任何损坏都报出全部违例 | 00b3-01（干净链 `[]`），以及 00b3-02…07、14…19 的「恰好一个」 |
| kb_version 只比对 readlink、不看实际服务的 build | 11a-02 v2 law（按内容判断） |
| 先在 A 上查询，切换后继续用缓存的 pool | 11a-11 |
| effective 推进时 audit_rev 没跟上 | 08c-08 v2 |
| 云端路没有 bbox 就跳过 span 校验 | 00b3-19 |
| 发布按 build_id 字典序判断「较新」 | 09d-18 |
| 只读权限没有递归 | 09b-12 |
| 只把 cwd 的字符串前缀当成「在项目内」 | 00c1-06/07 |

## 没有 law 的单元

00d2、00e、08b、02d、10a、10b、13a、14、30 声明没有 law。这些声明成立，本轮没有攻击它们。

---

## 必须补的最小测试（按优先级）

**P0（不补就不能定稿）**
1. `test_toctou_swap_at_every_send_point`：固定的暂停点清单，加上 reached 标记，超时即 FAIL（K6）。
2. 把 05b-15 扩为 `test_executed_env_cwd_argv_equal_checked`：测试进程先放入 `ANTHROPIC_BASE_URL` 和 `CLAUDE_CONFIG_DIR`（K6）。

**P1**
3. `test_seams_inert_unless_test_mode`，并把 F9 定下来：测试模式开关，`PAUSE_DIR` 不能默认取全局可写目录。
4. `test_crashpoints_partition_durable_writes`：用 audit hook 证明崩溃点的枚举是完备的（K3/K5/K8）。
5. 07b-04 改为「同页内有 exact 行」的 fixture；08c-10 的 law 改为由生成器真值判定（K1）。
6. 07b-12 的断言改为按值等价，以及「同值连续段内」的 unaligned。

**P2**
7. `test_lease_before_load_under_prune`，或者在 §9 如实改为未关闭。
8. `test_choices_duplicate_field_id_rejected`。
9. 11a-02 和 11a-11 的请求类型覆盖完整的 TOOLS 集合。
10. 09d-17 增加「内部文件可写」和「缺少 MANIFEST」两种参数化。

本轮只读，没有改任何文件。