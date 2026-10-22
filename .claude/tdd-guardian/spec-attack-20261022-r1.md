# 规格攻击报告：.claude/tdd-guardian/test-matrix-20261022-klode-kit.md

## 结论：BREACHED（发现 16 个缺口）

你点名的 6 个攻击里，有 4 个被矩阵挡住，2 个能存活：
- **发布先切指针、后写完 build**：能存活。09d-02 只断言指针「可解析」。
- **发送器只在调用前查 sha，复制后不再查**：能存活。05b-08 是靠时序碰运气的竞态，不是确定性用例。

另外有 3 个 P0 缺口不在你的例子里：
- MCP 包装只在 kb_version 里刷新 pool（K7）。
- `no_geometry` 没有传到 gate（K1/K2）。
- 对齐属性在「什么都不对齐」时也能空成立（K1）。

矩阵还不能定稿。至少补完 P0 的 8 条再定稿。

依据文件：
- 计划：`.claude/tdd-guardian/plan-20261022-klode-kit.md`（先读了 Deferred 清单，下面的缺口都不在其中）
- 设计：`docs/pipeline/20261022-klode-kit/02-概要设计.md` 的 r3.1、r3.2、r4.1–r4.3 段

---

## P0：直接击穿某个关键不变量

### 缺口 1（K7 / WI-11a）：只有 kb_version 会刷新 pool
- **攻击**：只做了一半的操作（变体：kb_version 自己去 readlink，查询仍用旧 pool）
- **能通过全部用例的错误实现**：
  ```
  def kb_version(): ensure_pool(); return basename(readlink(current))
  def tools_call(msg):
      if msg.name == "kb_version": return kb_version()
      return handle(self.pool, msg)   # 不调 ensure_pool，pool 只在 kb_version 里换
  ```
- **为什么能存活**：
  - 11a-01 的顺序固定是「切换 → 先调 kb_version → 再 search/verify」。kb_version 顺手换了 pool，所以后面的查询拿到的正好是 B。
  - 11a-02 断言 `kb_version == readlink`，这个实现对 readlink 永远为真，pool 是新是旧都不影响。
  - 11a-06/07/09 都沿用同样的顺序。
- **生产上的后果**：宿主是直接调 search/verify 的，不会先调 kb_version。用户切到 B 之后，问答拿到的一直是 A 的内容；而 u5_ready 探针用的是 kb_version，会报 B 已生效。
- **要补的用例**：`test_query_first_after_switch_serves_new_build`。L3·S5，lane 为 cli_integration；降级的两条路径（11a-06/07）也参数化跑一遍。
  - **断言**：发布 B 之后，第一条请求就是 search；结果只含 B 独有的标记行、不含 A 独有的标记行。之后再调 kb_version，`build_id == B`。
  - 同时把 11a-02 的 law 改成：kb_version 返回的 `build_id` 等于上一次查询实际用的 build（用内容来判断），而不只是等于 readlink 的结果。

### 缺口 2（K1/K2 / WI-07b→08a）：cell 级的 `no_geometry` 没有传到 gate
- **攻击**：结论正确地算出来了，但没传到下一层（跨层只做了一半）
- **错误实现**：
  ```
  align():  基准行没有 exact tokens → cell.flags = ["no_geometry"]   # 07b-04 通过
  gate():   uncertain = 页级原因 + 行级原因      # cell.flags 被忽略
            候选 = verified(auto) 的条件：四路齐 且 全部 agree 且 uncertain == []
  ```
- **为什么能存活**：
  - 07b-04 只断言 cell 被标了 `no_geometry`。
  - 08a-07 的 uncertain 列表是 hypothesis 直接生成的输入，不是从 align 来的。
  - 08c-10 的链从 compare 开始，没有经过 align。
  - U3-02 翻转的是一个值，走的不是这条路径。
- **后果**：一行只有 approx 几何、四路恰好读得一样时，这行会被自动判为 verified。按 R2-08，它本应等人工点选才能解除。
- **要补的用例**：`test_no_geometry_row_never_auto_verified`。L2·S3，lane 为 contract_replay，链路是 align→compare→gate→writeback 全链。
  - **断言**：该记录的候选是 `pending_confirm`；`uncertain` 里有 `no_geometry` 条目；`effective` 没有被创建。
  - 建议把 08c-10 的起点前移到 align，并让生成域里包含只有 approx 几何的行。这样一条 S4 用例就能同时挡住 `no_geometry`、`unaligned`、`merged_rows` 三种不传递。

### 缺口 3（K1 / WI-07b）：「下标对严格递增」在什么都不对齐时也成立
- **攻击**：只看下标、不处理行缺失，或者干脆返回空对齐（变体：按下标逐行硬对）
- **错误实现**：
  ```
  pairs = [(i, i) for i in range(min(len(base), len(cloud)))]   # 不处理行缺失
  for 多出来的基准行: mark("unaligned:<engine>")
  ```
- **为什么能存活**：
  - 07b-06 只要求严格递增：`(i, i)` 满足，空列表也满足。
  - 07b-09 只要求排列不变性：这个实现也满足。
  - 07b-07 只断言「记了 `unaligned:<engine>`」，没有断言记在哪一行。所以 fx-missingrow 里被标记的是最后一行而不是真正缺的那一行，也能通过。
- **后果**：缺了一行之后，下面所有行都错位一格。如果相邻两行的值相同（比如 dup10 这种形态），错位后四路看起来「一致」，会被误判 agree，而且这是自动 verified 的错值。
- **要补的用例**：`test_alignment_recovers_known_correspondence`。L1·S4，lane 为 unit。
  - **fixture**：hypothesis 生成基准行，并在云端路里做已知位置的删行、插行或拼行，记录下真实的对应关系。
  - **断言**：对齐得到的 pairs 恰好等于真实对应关系；`unaligned` 标在被删的那一行上。
  - 同时把 07b-07 加强成「`unaligned` 的行号 == 真正缺的那一行，其余各行都正确对上」。

### 缺口 4（K2 / WI-08c）：choices 只覆盖了部分字段
- **攻击**：只做了一半（只校验提交了的字段，没看没提交的）
- **错误实现**：
  ```
  for rec, items in group_by_record(choices):
      if all(valid(i) for i in items): commit(rec, status="verified", by="human")
  # 这条记录里不在 choices 中的 disputed 字段没有被检查
  ```
- **为什么能存活**：
  - 08c-05 测的是「提交了的字段里有一个不合格」。
  - 08c-08 的随机序列里没有「只覆盖一部分字段」这种情况。
  - U8-01 的 fixture 是全部点完的。
- **后果**：一条记录有 3 处分歧，用户只点了 2 处，这条记录就整条变成 verified。第 3 处分歧的值会作为已审计的值进入 build。
- **要补的用例**：`test_partial_choices_record_not_verified`。L2·S3，lane 为 contract_replay。
  - **断言**：该记录的候选不是 verified（保持 `pending_confirm`，或者 exit 30 并列出漏掉的 field_id）；`effective` 的字节不变；`fixes.jsonl` 里不出现这条记录的 commit。
  - 是 exit 30 还是保持 pending，实现前要先定下来，记作 §7-F 的一条。

### 缺口 5（K5 / WI-09d）：可能发布一个不完整或没过 check 的 build
- **攻击**：静默失败或边界写错（`if receipt.status == "FAIL": reject`），以及只验指针、不验目标
- **错误实现**：
  ```
  r = load_json_or_none(B/"check_receipt.json")
  if r and r["status"] == "FAIL": reject()            # 文件缺失也会放行；
  os.replace(tmp_link -> B, current)                   # 不比对 build_id，不校验 MANIFEST
  ```
- **为什么能存活**：
  - 09d-16 只测了「receipt 为 FAIL」这一种情况。
  - 09d-02 只断言 `current` 存在、能解析、不悬空。
  - 而且 `fx-publish-ab` 是预先构建好的完整 build，崩溃点只枚举了 publish 内部，没有覆盖 build→check→publish 这一整段。
- **后果**：pipeline resume 时，build 在第 12 步失败或者在 chmod 之前崩溃，留下的目录仍然可能被发布。这正好是你说的「先改指针后写完 build」。
- **要补的用例**：
  - `test_publish_rejects_unverified_target`。L2·S3，contract_replay。参数化四种情况：check_receipt 缺失；check_receipt 的 build_id ≠ 目标；MANIFEST.sha256 校验失败；目录仍然可写。
    - **断言**：四种情况都拒绝发布；`readlink(current)` 不变；`PUBLISH.log` 不新增 switch 行。
  - 把 09d-02 的断言加强为：`current` 指向的那个 build，`MANIFEST.sha256` 能校验通过，`check_receipt.status == PASS` 且 `check_receipt.build_id == basename(target)`。崩溃点的枚举范围扩到 `pipeline --resume` 的 build、check、publish 全段（S5）。

### 缺口 6（K6 / WI-05b）：TOCTOU 用例不是确定性的
- **攻击**：只在调用前查 sha，复制后不再查（就是你给的例子）
- **错误实现**：
  ```
  assert sha(read(src)) in REG      # 查源文件
  shutil.copy(src, tmp/der.png)     # 重新读源文件，不对副本重算 sha
  spawn(claude, tmp)
  ```
- **为什么能存活**：05b-08 靠「一个在检查之后改写源文件的线程」。没有同步点，这个线程几乎不可能正好落在检查和复制之间那几微秒里。所以这条用例对这个实现几乎总是绿的，属于 flaky 的放行。05b-03 是在检查之前就改了字节，挡不住这种情况。
- **后果**：外发的字节不在登记表里，可能是没打码的原图。K6 被击穿。
- **要补的用例**：`test_toctou_swap_after_check_deterministic`。L2·S3，contract_replay。
  - **做法**：沿用 §0.3 已经接受的测试缝，加一个 `KLODE_KIT_PAUSE_AT=send.after_source_check`。在这个点上把源文件换成未登记的字节，然后放行。
  - **断言**：要么 exit 60 且假 claude 的日志 0 条；要么假 claude 收到的 sha ∈ 登记表。两者必居其一，而且每次运行结果都确定。

### 缺口 7（WI-00c1）：checker 只看 flag 在不在，不看值
- **攻击**：忽略参数（不看值），加上同一 flag 出现两次、`--flag=value` 写法绕过
- **错误实现**：
  ```
  ok_required  = all(f in argv for f in REQUIRED_FLAGS)
  ok_forbidden = not any(tok in FORBIDDEN for tok in argv)   # 按整 token 精确比较
  settings_sha = sha(PLUGIN/"config/cloud_settings.json")    # 不看 argv 里 --settings 实际指向哪个文件
  ```
- **为什么能存活**：
  - 00c1-04 只测删掉必含项，00c1-05 只测插入独立的禁用 token。
  - 00c1-10 改的是插件里那份固定的 `cloud_settings.json`，checker 只看这份固定文件时也会照样 FAIL，所以挡不住。
- **后果**：下面这些写法全部会判 PASS：`--tools "Read,Bash"`、`--permission-mode bypassPermissions`（重复出现时最后一个生效）、`--mcp-config=x.json`、`--settings /tmp/permissive.json`。隔离等于失效。
- **要补的用例**：
  - `test_required_flag_value_mutation_or_duplicate_fails`。L1·S4，unit。
    - **fixture**：hypothesis 从三种变异里任选一种：把某个必含 flag 的值换成别的值；在末尾追加一个同名 flag、值不同；把某个禁用项写成 `--x=v` 的形式。
    - **断言**：FAIL，并点名对应的检查项。
  - `test_settings_arg_must_be_pinned_file`。L1·S3。
    - **断言**：`--settings` 指向内容不同的另一个文件时 FAIL。

### 缺口 8（WI-00c1 / 05b）：实际执行的 argv 不是审查过的那份
- **攻击**：只做了一半（审查了，但执行时又改了）
- **错误实现**：
  ```
  receipt = isolation_check(build_argv())    # PASS
  spawn(build_argv() + ["--add-dir", masked_dir])
  ```
- **为什么能存活**：「argv_sha == 实际执行的 argv」只在 00c2-09 断言过，那是 model_integration lane、要过 G2 闸门。replay lane 的 05b-01/06 只看退出码、调用条数和图的 sha。
- **要补的用例**：`test_executed_argv_equals_checked_argv`。L2·S3，contract_replay，claude 和 codex 两路参数化。
  - **断言**：假可执行文件记下的完整 argv 满足两点：`sha == isolation_receipt.argv_sha`，并且对这份 argv 重新跑 `isolation_check` 结果为 PASS。

---

## P1：不变量被削弱，或者某一层能放过错误

### 缺口 9（K4 / WI-00b3）：校验器只对 6 类损坏是完备的
- **攻击**：边界写成「近似但错」（`len(decisions) <= 1`），以及有几段干脆不检查
- **错误实现**：「恰有一条生效决定」只检查 `<= 1`；段②不比较 `route` 决定的值和 `candidates[route]` 的值；段③不检查导出文本里是否逐字出现 `apply(transforms, 修订值)`；段①不检查 pdftext 的 dpi 换算；span 只在 `bbox_px` 非空的路上检查（于是跳过了两条云端路）。
- **为什么能存活**：00b3-12 的损坏域被写死成「6 种之一」，00b3-02…07 各测一种，这几条都不在其中。00b3-12 只随机挑记录，不随机挑路。
- **后果**：K4 名义上是 S5，实际上 P-01/P-02 会对下面几种错误产物给出 0 违例：
  - 写回写成了★推荐值，而不是用户点的那一路的值；
  - 导出行里的值被改动了；
  - 一个 verified 字段根本没有决定。
- **要补的用例**：
  - 新增变体：`test_variant_verified_field_no_decision`（`missing_effective_decision`）、`test_variant_route_value_ne_candidate`、`test_variant_record_value_ne_decision`、`test_variant_export_text_ne_applied`、`test_variant_pdftext_bbox_wrong_dpi`、`test_variant_cloud_route_span_mismatch`。都是 L1·S3。
  - 把 00b3-12 的损坏域扩大到 r3.1 表里列出的全部不变量 × 任意一路（S4）。

### 缺口 10（K3 / WI-08c）：effective 只推进了一半
- **攻击**：只做了一半（rev 往前走了，其余字段没跟上）
- **错误实现**：
  ```
  eff.rev = cand.rev                 # 只改 rev
  # eff.audit_rev、status、verified_by 还是旧值，cand 也没有标 superseded_by
  ```
- **为什么能存活**：
  - 08c-01 只断言 rev+1 和 `verified_by`。
  - 08c-08 只看 rev 是否单调。
  - 09b-05 只测了「候选失败」这一种情况。
  - 00b3 的 6 类损坏里没有 audit_rev。
- **后果**：`audit_bindings` 变成 `{3, a1}` 这样的组合，build 绑定到错的审计版本。
- **要补的用例**：在 08c-08 的性质里加一条断言（L2·S5）：每次推进之后，`effective` 的 `{rev, audit_rev, status}` 都和被提升的那个候选逐字段相等；被替代的候选带有 `superseded_by`。

### 缺口 11（WI-01a）：值之外的列取自最新候选
- **攻击**：只做了一半
- **错误实现**：value 按 `audit_bindings.rev` 读，item、date、flag 却从最新候选读。
- **为什么能存活**：01a-03 只让两个 rev 的值不同（A 对 B），其他列都一样。
- **要补的用例**：把 01a-03 改为 r1 和 r2 的 item、value、date、flag 四列全都不同。L1·S3。
  - **断言**：整行 == 由 r1 投影出来的那一行。

### 缺口 12（WI-09b）：只读权限没有递归
- **攻击**：只做了一半，`chmod a-w` 只作用在顶层目录
- **为什么能存活**：09b-04 写的是「目录为 a-w」。测试里那步「再改一个文件」到底要不要先自己 chmod，没有说清楚。
- **后果**：build 目录里的文件仍然可写，「build 不可变」这个前提（11a-09 的调用中途切换、mid-call 用旧 build）就不成立了。
- **要补的用例**：`test_build_tree_fully_readonly`。L2·S3。
  - **断言**：`builds/<id>/` 下除 `.readers/` 以外的每个文件和目录都没有写权限位；以写模式打开任意一个文件都抛 `PermissionError`。

---

## P2

13. **00c1 里 codex 的版本下限没有测**：00c1-11 只测了 claude。把它参数化，加一组 codex 的「锁定值−1 / 锁定值 / 跨位数比较」。L1·S2。
14. **09d 的 `skipped_newer` 可能按 build_id 字典序判断，而设计要求按 `PUBLISH.log.seq`**：09d-05 的 fixture 里两种顺序一致，所以挡不住。新增 `test_skipped_newer_uses_publish_seq`：先 rollback 到旧的 A，再对 A 做 replay，这时登记产物上的标记是 B（build_id 更大，但 seq 更小）。
    - **断言**：该动作被重放，不记 `skipped_newer`。L2·S3。
15. **11a 可能对 notification 也回一条响应**：11a-05 只覆盖了 initialize、tools/list、ping。新增 `test_notification_no_response`：发送 `notifications/initialized`。
    - **断言**：stdout 没有新增任何行；下一个请求仍然正常。L3·S2。
16. **08c 可能接受点在缺失 cell 上的 route**：伪造一份 choices，选的那一路在这一格是 None。新增 `test_choice_on_missing_route_cell_rejected`。
    - **断言**：exit 30；该记录零写入。L2·S3。

另有一条风险，暂不列为必补：keep N=0 时连续切换两次，包装在 readlink 之后、写租约之前的窗口里，它要读的 build 可能已经被 prune 移走。09d-11 的读者从头到尾都持有租约，覆盖不到这个窗口。建议在 11a-08 里断言「先写租约，再 load」，并在写完租约后重新确认这个 build 不在 `_trash` 里。

---

## 被现有矩阵挡住的攻击

| 攻击 | 被哪条用例挡住 |
|---|---|
| auto-agree 只看前三路 | 07c-05（随机四路网格，双向蕴含）、08c-10、U3-02 |
| writeback 在候选 disputed 时清空 effective | 08c-09（effective 字节不变）、08d-06 / U6-01 |
| prune 只保护 current | 09d-08（受保护集合的五类都要在）、09d-14 |
| MCP 包装只在 tools/list 时刷新 build_id | 11a-01（中间不发 tools/list，B 永远不会出现） |
| 切换指针时先 unlink 再 symlink | 09d-03 和 09d-02 的随机 SIGKILL |
| 只按路径判断登记，不看字节 | 05b-03、05b-07 |
| `_mask_review/` 里的副本因 sha 碰巧在登记表里而被外发 | 05b-04 |
| cwd 按字符串前缀判断，或用符号链接绕过 | 00c1-06/07 |
| 版本号按字典序比较 | 00c1-11（只覆盖 claude，codex 见缺口 13） |
| 用伪造的 choices 把页级 uncertain 的记录点成 verified | 08c-11 |
| 旧候选覆盖了更新的 rev，或重复提交产生新 rev | 08c-08、08c-06 |
| 写回崩溃后只提交了一半 | 08c-13 |
| build 的步骤执行顺序乱了 | 09b-03（失败点 k 由 hypothesis 生成，k 之后的步不能有 receipt） |
| build 写到 `builds/` 之外 | 09b-02、09b-03 |
| 导出时读 `cells.jsonl` 里的候选值 | 01a-04、01a-03 |
| 同日、同项目、同值的导出行重复 | 01a-05 |
| 白名单作用到数值字段 | 07c-10 |
| 2:2 平票时给出推荐值 | 07c-08 |

## 没有 law 的单元

矩阵声明 00d2、00e、08b、02d、10a、10b、13a、14、30 这几个 WI 没有 law。我核对了一遍，这些声明成立（宿主取证、静态检查、判据表），没有攻击它们。

## 设计层的提示（不是矩阵缺口）

r4.3 的 env 白名单写的是 `ANTHROPIC_*`，这会放行 `ANTHROPIC_BASE_URL`，可以把打码图转发到任意端点。建议在设计里收窄白名单，然后把它加进 00c1-08 的非法样例。

## 必须补的测试（按优先级）

**P0**
1. `test_query_first_after_switch_serves_new_build`，并修改 11a-02 的 law（K7）
2. `test_no_geometry_row_never_auto_verified`，并把 08c-10 的起点前移到 align（K1/K2）
3. `test_alignment_recovers_known_correspondence`，并加强 07b-07（K1）
4. `test_partial_choices_record_not_verified`（K2，要先定规格）
5. `test_publish_rejects_unverified_target`，并加强 09d-02 的断言和崩溃点范围（K5）
6. `test_toctou_swap_after_check_deterministic`，把 05b-08 的竞态换成确定性用例（K6）
7. `test_required_flag_value_mutation_or_duplicate_fails` 和 `test_settings_arg_must_be_pinned_file`
8. `test_executed_argv_equals_checked_argv`

**P1**
9. 00b3 新增 6 个变体，并扩大 00b3-12 的损坏域（K4）
10. 在 08c-08 里断言 effective 与被提升的候选逐字段相等（K3）
11. 01a-03 改为四列都不同
12. `test_build_tree_fully_readonly`

**P2**
13. codex 版本下限（13）
14. `test_skipped_newer_uses_publish_seq`（14）
15. `test_notification_no_response`（15）
16. `test_choice_on_missing_route_cell_rejected`（16）

只读，没有改任何文件。