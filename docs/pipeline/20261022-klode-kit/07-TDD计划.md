# 07 · TDD 计划（阶段 4）

> 计划由 `tdd-guardian:tdd-planner`（Opus 5.5）写；测试矩阵由 `tdd-guardian:tdd-test-designer`（Opus 5.5）写；对抗评审由 `tdd-guardian:tdd-spec-adversary`（Opus 5.5）做，共 6 轮。

## 原件

| 产物 | 路径 | 说明 |
|---|---|---|
| TDD 计划 | `.claude/tdd-guardian/plan-20261022-klode-kit.md` | 53 个 WI（含可选 WI-17、WI-19），M0 spike+契约 11 / M1 OCR+审计 15 / M2 build·check·发布·MCP 17 / M3 med-intake·workflow·打包·文档 10 |
| venv 鲁棒性附录 | `.claude/tdd-guardian/plan-20261022-klode-kit_addendum-venv.md` | 用户 2026-10-22 追加：目录改名 / 移动后不得静默失效（前车之鉴 project-workflow#180），硬要求 V1–V7 + 7 条测试 |
| 测试矩阵（定稿） | `.claude/tdd-guardian/test-matrix-20261022-klode-kit_v8.md` | 517 条：unit 261、contract_replay 172、cli_integration 60、model_integration 13、host_acceptance 9、manual 2 |
| 矩阵历史版本 | 同目录 `_v2` … `_v7`、`_v6_full`（`_v6` + `_v6_part2` 是被截断后拆写的两半） | 每版相对上一版的未改行均经脚本逐字节比对，0 处不一致 |
| 对抗评审报告 | 同目录 `spec-attack-20261022-r1.md` … `r6.md` | 缺口数 16 → 10 → 4 → 3 → 2 → 0 |

## 对抗评审结论

**SURVIVED**（第 6 轮，矩阵 v7；v8 只按第 6 轮建议 S1/S2 修正两处可满足性措辞：klode CLI 子进程不装 audit hook、build 内打开事件含 `os.open` 与 `sqlite3.connect`）。

关键不变量 K1–K8（矩阵 §1）各有 S4–S6 级用例覆盖：四路全齐且一致才 auto-agree；uncertain 非空不得 verified；effective 只由 writeback 推进；来源链三段；发布指针完整与 prune 保护；外发只认 MANIFEST sha 且失败零调用；同一 MCP 进程 A→B 切换；13 步固定顺序与 builds/ 之外零写入。

## U1–U8 → 用户感知层验收

见计划 §3 与矩阵 §6，每条 U 至少一条可直接观察的验收测试。

## 手动闸门（阶段 5 实现者到此停下）

| 闸门 | 内容 | 所在 WI |
|---|---|---|
| G1 | 写 `~/.claude`（回修用户 skill B1–B5、装插件） | WI-19（可选） |
| G2 | 第一次真实云端外发 / 真实模型调用（仅合成图） | 00c2、00c3、00d2、06a/06b 录制、11b、12、13a、14、31 |
| G3 | 登记到真实数据项目 MCP / 真实 registry | 阶段 6（自动测试只碰 tmp） |
| G4 | 安装依赖、下载 MLX 权重 | 04a、15a |
| G5 | 宿主目录信任（交互一次） | 00d2、11b、13a、14 |

## 进入阶段 5 前需用户确认的规格待定项

矩阵 §7 列出的「规格待定（推荐）」项（空 inbox、非图片文件、零 verified 时 build、同日第二批、desk tests 为空、同图两次的 batch_id、部分覆盖 choices → exit 30）、测试缝（`crashpoint`/`PAUSE_AT`，只在 `KLODE_KIT_TEST_SEAMS=1` 生效），以及计划风险 R-2（U2 打码对照图放 `_mask_review/` 而非 `_masked/`）。
