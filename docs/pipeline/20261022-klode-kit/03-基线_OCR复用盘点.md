# 03 基线：OCR / 审计代码与经验复用盘点

- 日期：2026-10-22 · build-pipeline 阶段 1（现状盘点）· 只读，未改任何源目录
- 范围：189 `src/ocr/`、189 `doc/plan/`（工作流计划 + 经验汇总 E01–E73）、003 `doc/plan/`（复用手册 / 架构 / 两本专利书计划）、001 `src/wfviz`、本机能力探测
- 路径约定：`189/` = `~/Documents/Claude/Projects/189_d_reading-plan_reading-notes_P1/`；`003/` = `…/003_d_patent-data-center_corpora-mcp-kb_P1/`；`001/` = `…/001-agent-rational-unified-process-P1/`
- 红线：未读取任何私有医疗项目文件；本文不含真实个人 / 医疗信息

## 0 结论（先看这段）

1. **189 `src/ocr/` 是一条已跑通 900 页的「双引擎 + 分歧 + 补丁 + 回图」流水线**，核心算法模块（`engines`、`diff_engines`、`cer`、`apply_fixes`、`quote_crop`）可直接搬入 `src/` 改造；但全部模块通过 `registry.py`（书目硬编码）与 `work/ocr/<book>/…` 目录约定耦合，搬入时要换成 klode-kit 自己的「批次 / 页」寻址。
2. **交接文档里「run_batch 把非 tesseract 一律当 MLX」已过时**：2026-09-22 W1 重构后 `run_batch.py:180` 走 `engines.make_engine(ec)`，按 `ENGINES` 注册表按名建实例（`engines.py:226-246`），新增引擎只改注册表。且 `OcrMacEngine`（Apple Vision 经 `ocrmac`）已在 2026-09-26 W34 接入并成为日文书副引擎。四路里的 A、B 两路引擎代码**已有现成实现**。
3. **本机四路条件**：A Apple Vision 可用（pyobjc + swift 两种途径都验过，`zh-Hans`/`ja-JP` 都在 33 种支持语言内）；B Unlimited-OCR-8bit 权重在本机（3.9 GB，snapshot 与 config 一致），mlx_vlm 0.7.1 只在 189 的 `.venv`（Python 3.12）里；tesseract 5.5.3 有 `chi_sim`/`chi_tra`/`jpn`/`jpn_vert`；D `codex exec -i <image>` 存在（codex-cli 0.162.1）；C Claude Code 的 Read 工具对 PNG/JPG 返回「可见图像」（官方文档），`claude -p` 无专门的图片参数，走 Read 路径即可。
4. **全新要写的东西**（现有代码零覆盖）：统一 JSONL 契约与字段抽取（`number|date|drug|unit|flag`）、四路 N 向比对（现有是两两 + 第三票）、引擎弱点白名单（现有只有一条硬编码的小书きカナ规则）、抬头打码、确认页 HTML + 点选 + 写回、Claude / Codex 读图适配器、`uncertain` 门禁。
5. **经验层最值得继承的四条**：分歧只呈现不自动改字（E12/E14/003 计划 §1）；两引擎共同错的字不进分歧，所以分歧复核不能替代整页金标 CER（003 计划 §1 方法论）；第三票要按「短替换块」选页而不是「相似度最低页」（E34）；换引擎的验收口径是分歧率 + 金标 CER，不是下游检索指标（E35）。

## 1 本机能力探测（只探测，未安装任何东西）

| 能力 | 结果 | 证据 / 命令 | 对应四路 | 备注 |
|---|---|---|---|---|
| Apple Vision（pyobjc） | **可用** | 189 `.venv/bin/python -c "import Vision"` OK；`VNRecognizeTextRequest.supportedRecognitionLanguagesAndReturnError_` 返回 33 种，含 `zh-Hans`、`zh-Hant`、`ja-JP`、`en-US` | A | 系统 `/usr/bin/python3`（3.9.6）**没有** pyobjc Vision；只能用 189 venv 或新建 venv 装 `pyobjc-framework-Vision` |
| Apple Vision（swift） | **可用** | `swift v.swift`（Apple Swift 6.4）打印同一份 33 语言表 | A | 可作无 Python 依赖的兜底 CLI；编译一次需十几秒 |
| `ocrmac` 包 | 1.0.1 在 189 venv | `engines.py::OcrMacEngine` 已基于它 | A | 注意语言标签：直接调 `ocrmac` 用 `ja-JP` / `zh-Hans`；经 docling 则要 `iso:ja`（E31） |
| tesseract | 5.5.3，163 语言包 | `tesseract --list-langs` 含 `chi_sim chi_sim_vert chi_tra jpn jpn_vert eng` | B 兜底 | 日文书实测 CER 0.225（E13），只当兜底 |
| Unlimited-OCR MLX 模型 | **权重在本机** | `~/.cache/huggingface/hub/models--mlx-community--Unlimited-OCR-8bit/` 实占 7.3 G（`model.safetensors` 3.92 GB），snapshot `6d2a0677…` 与 189 `config.json` 的 `model_snapshot` 一致 | B | `du -sh` 不带 `-L` 只显示 9.8 M（全是符号链接），别误判为未下载 |
| mlx / mlx_vlm | mlx 0.32.2、mlx_vlm 0.7.1 | 只在 189 `.venv`（Python 3.12.14）；全机扫描 `Projects/*/.venv*` 只有它同时有 ocrmac + mlx_vlm | B | klode-kit 要么复用该 venv，要么自建；**CLI `python -m mlx_vlm.generate` 对该模型退化成重复串，必须走 Python API + `apply_chat_template`**（E15/E19） |
| poppler / ImageMagick | `pdftoppm pdftotext pdfinfo magick` 都在 `/opt/homebrew/bin` | — | 渲染 / 裁图 | PIL 12.3 在 189 venv，11.3 在系统 python |
| `codex -i <image>` | **支持** | `codex --help` 与 `codex exec --help` 都列 `-i, --image <FILE>...  Optional image(s) to attach to the initial prompt`（codex-cli 0.162.1） | D | 非交互用 `codex exec -i <png> "<prompt>"`；结构化输出格式要自己约定 |
| `claude` CLI 带图 | **经 Read 工具可行**；无专门图片参数 | `claude --version` 2.1.285。`claude --help` 没有 `--image` 类参数（`--file` 是「启动时下载 file_id 资源」，不是本地图）。官方文档 Tools reference：「Images: PNG, JPG, and other image formats are returned as visual content that Claude can see, not as raw bytes」（https://code.claude.com/docs/en/tools-reference）。Agent SDK 文档：单条消息模式「不支持在消息里直接附带图片」，流式模式支持（https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode） | C | 适配器做法：`claude -p --allowedTools Read "读取 <png> 并按契约输出 JSON"`；或用 Agent SDK 流式模式直接附图。**`-p` 下 stdin 直接塞图片：待核实**（文档未见） |
| klode CLI | `~/.local/bin/klode` 可用 | — | kb-build | 交接文档：v0.4.2 |
| wfviz | **可用** | 见 §5 | WUA 图 | — |

## 2 189 `src/ocr/` 逐模块盘点

缩写：**搬** = 直接搬入 `src/` 改造；**参** = 参考重写；**否** = 不用。
「JSONL」列指与统一契约 `{engine,page,line,text,fields:[{kind,value,bbox}]}` 的关系；「9 步」列指审计回路（①打码 ②本机两路 ③云端两路 ④抽字段 ⑤字段比对+白名单 ⑥裁分歧小图 ⑦确认页 ⑧点选 ⑨写回）。

| 模块（行数） | 职责 | 接口 | 依赖 | 四路 | JSONL | 9 步 | 判定 | 理由 / 改造点 |
|---|---|---|---|---|---|---|---|---|
| `engines.py`（245） | 四个引擎统一封装：`TesseractEngine`、`MlxEngine`（Unlimited-OCR via mlx_vlm Python API，主/备 prompt，退化检测 `repeat_ratio`，版面标签剥离 `strip_layout_tags`）、`IaTextLayerEngine`（pdftotext 文字层）、`OcrMacEngine`（Apple Vision，按框竖直重叠归行）；`ENGINES` 注册表 + `make_engine(cfg)` 工厂 | `eng.ocr_page(png, ctx=None) -> (text:str, meta:dict)`；`eng.params`（指纹用）；`make_engine({"engine":name,…})` | mlx_vlm、ocrmac、tesseract CLI、PIL | **A**（OcrMac）**B**（Mlx + Tesseract） | **缺**：返回纯文本 + meta，不出行级 bbox；但 `OcrMacEngine` 内部已有每行框（归一化坐标，原点左下），`MlxEngine` 的 `raw` 含 `<\|det\|>kind [x0,y0,x1,y1]` 块框（0–999）——两路的 bbox 原料都在，只是被丢掉 | ② | **搬** | 把 `ocr_page` 扩成返回 `lines:[{line,text,bbox}]`；OcrMac 加 `lang` 多值（`["zh-Hans","ja-JP"]`）；去掉 `IaTextLayerEngine`（扫描件/截图无文字层）；保留退化检测与 alt_prompt 重试 |
| `registry.py`（156） | 书目 `Book` dataclass 硬编码（4 本书）+ `config.json` 分支摊平 `load_config(branch)` + `pdf_page_count` | `book(slug)`, `load_config(branch)`, `book_config(slug)` | — | — | — | — | **否** | 书目硬编码、字段全是图书版面（`caption_re`、`fig_scheme`…）；klode-kit 的输入是「一批截图/扫描页」，用 manifest/JSONL 登记而不是 Python 常量。只借「配置指纹决定输出目录」的思路 |
| `config.json`（79） | 分支化引擎参数（`ja` / `en` / `ja-ocrmac`），`{dpi, batch_pages, page_fallback, branches.<b>.{primary,secondary}}` | 被 `registry.load_config` 读 | — | A/B 参数样本 | — | — | **参** | 直接复用其 MLX 参数块（model、snapshot、prompt `document parsing.`、`max_tokens 3072`、alt `Free OCR.`）与 ocrmac 参数块；结构改成「四路各一个 engine 块 + 共享 dpi」 |
| `run_batch.py`（234） | 整本书分批 OCR：渲染（pdftoppm + `rotate.json` 转正 + 渲染锁）→ 指纹目录 → 原子写 → `progress.json` 续跑 → 退化重试 / alt prompt / failed 记录 | CLI `--book --engine primary\|secondary [--pages --dry-run --render-only --max-tokens]`；`fingerprint(cfg, role)`、`parse_pages(spec, n)`、`render(pdf, pages, png_dir, dpi, dry, rotate)` | registry、engines、pdftoppm、magick | A/B 的批跑壳 | 写 `page_NNNN.txt` + `.raw.txt`，不是 JSONL | ② | **参** | **核实结果：已不再「非 tesseract 一律当 MLX」**——L180 `eng = make_engine(ec)`，L153-158 按 `ENGINES[...].needs_png` 决定是否渲染。要重写的原因是：输入形态（PDF 整本 vs 截图/单页图目录）、`--engine` 只接受 primary/secondary 两角色（四路需要 N 角色）、与 registry 耦合。值得原样保留的机制：配置指纹目录、`.tmp`+`os.replace` 原子写、`progress.json` 一致性核对、`--max-tokens` 只许配合 `--pages` 的「参数偏离逐页留痕」规则、渲染锁 |
| `diff_engines.py`（100） | 主/副两路文本去空白 + NFKC 后用 `difflib.SequenceMatcher` 按**字符块**对齐，洞 = 分歧块；`kana_only_diff` 过滤 ocrmac 的小书きカナ弱点；写每页 `diff/page_NNNN.jsonl` + `summary.md/json` | `norm(text)`, `kana_only_diff(a,b)`, `diff_page(a, b, min_hole=1) -> [{op,primary,secondary,sim,pos,ctx}]`；CLI `--book [--pages --threshold 0.8 --branch]` | registry、apply_fixes.engine_dir | 两路 | 分歧块格式 `{op,primary,secondary,sim,pos,ctx}` 可直接作为 ⑥⑦ 的输入 | ⑤（文本级） | **搬**（函数层）| `diff_page` 纯函数零依赖，搬入即用；**引擎弱点白名单在此只有一条硬编码**（`SMALL_KANA`），要抽成按引擎配置的规则表（如 `{"engine":"ocrmac","class":"small-kana"}`、tesseract 的 `0/O`、`1/l` 等）；四路要扩成「以某一路为基准的 N-1 次两两 diff」或多序列对齐 |
| `field_check.py`（133） | **字段级**交叉核对：两路各抽字段集合（年代 `DATE`、尺寸 `DIM`、专名 `PROPER`），`core()` 归一到数字内核 + 纪元 + 单位，报「副有主无（疑似漏读）/ 主有副无（疑似幻觉）/ 只差纪元或单位」三栏 | `fields(text) -> {"date":set,"dim":set,"proper":set}`, `core(s)`；CLI `--book [--pages --kinds date,dim]` | registry、apply_fixes、run_batch | 两路 | **最接近 `fields:[{kind,value}]`**，但 kind 是艺术史的 date/dim/proper，无 bbox，无 drug/unit/flag | ④⑤（字段级） | **参** | 结构和「单边出现分两栏、方向意义不同」的报告设计值得照抄；正则要整套换成单据字段：`number`（含小数、范围、×10^n）、`date`（和历/西历/斜杠/中文年月日）、`unit`（mg/dL、mmol/L、×10³/μL…）、`flag`（H/L/↑/↓/＊）、`drug`（词表 + 剂型剂量）。`core()` 的「数字相同只差单位单列」逻辑正是 flag/unit 漏读的检测思路 |
| `quote_crop.py`（70） | 从 MLX `raw.txt` 的 `<\|det\|>` 块框里找含引文的块，按框外扩裁 PNG；找不到退化为裁上/下半页 | `find_block(raw, quote) -> (kind, (x0,y0,x1,y1), text)`；CLI `<book> <pdf页> "<quote>" [--out --margin 12 --half --width 1400]` | PIL、registry、run_batch.fingerprint | B 的 bbox | 用的就是 bbox，但只认 MLX 的 0–999 千分比坐标 | ⑥ | **搬**（函数层）| `find_block` + 裁图逻辑直接用；要统一坐标系（MLX 千分比、Vision 归一化左下原点、Claude/Codex 无 bbox）→ 在 JSONL 层先统一成像素 bbox，裁图只吃像素框。E23：引文跨行时块找不到 → 分歧小图用「分歧块所在行 ± 1 行」而不是整句 |
| `cer.py`（121） | CER = Levenshtein / 金标长度；统一归一化视图（去页标、NFKC、折叠弯引号/破折号/连字、去空白、去目次点线）；`--selftest` | `normalize(text)`, `levenshtein(a,b)`, `cer(gold, hyp) -> {gold_chars,hyp_chars,distance,cer}`；CLI `<gold> <hyp> [--json\|--selftest]` | 纯 stdlib | 任一路 vs 金标 | — | 验收（回路外） | **搬** | 零依赖、自带测试；给合成金标样本做「四路各自 CER」验收用。注意 O(n·m) 纯 Python，整页 2–3k 字可接受，别拿去比整本 |
| `accept_cer.py`（50） | 对验收页金标跑 `cer`，按版式分列，均值 ≤ 0.02 判 ✅/❌；图表页如实列出不剔除 | CLI 无参；`KINDS`/`THRESH` 硬编码 | cer、`work/ocr/gold/MANIFEST.json` | — | — | 验收 | **参** | 只借「门槛 + 不剔除难页」的报告格式；页表硬编码，重写 |
| `apply_fixes.py`（158） | 补丁回放生成 canonical：`fixes.jsonl` 每行 `{page,old,new,src_sha256,by,at,reason}`，`old` 在该页当前文本**唯一命中**否则退出 2，`src_sha256` 不匹配退出 3，幂等，原子写；`--fallback-secondary` 主引擎失败页用副引擎并记 `FALLBACK.json`；`engine_dir()` 被其它模块借用 | `apply_page(text, fixes, page)`, `load_fixes(path)`, `engine_dir(book, role, branch)`; CLI `--book [--check] [--add PAGE OLD NEW --by --reason diff\|accept\|quote-check\|manual] [--fallback-secondary]` | registry | — | 补丁行格式可直接作为 ⑨ 的「写回」记录格式（加 `source: confirm-page`、`chosen_engine`） | ⑨ | **搬**（规则层）| 「原始 OCR 永不改、改动只进追加式补丁、canonical 可重建」是 klode-kit 写回的正确模型；**但 E21 的坑要用测试兜住**：漏 `--fallback-secondary` 会静默少页。klode-kit 里改为：确认页点选结果 → 追加补丁 → 重放出 verified 记录，`uncertain` 非空则拒绝标 verified |
| `check_pages.py`（99） | 页级体检：缺页、字数越界、乱码率（按 `script` 选字符集 `ja`/`latin`）、截断/退化/FALLBACK 页、副引擎字数 > 主 ×3 的漏文、页码跳号表 | CLI `--book [--min-chars 50 --max-chars 2500 --garbage 0.05]` | registry、apply_fixes、page_map | 两路 | — | ②后体检 | **参** | 「主 < 200 字且副 > 3× → 疑似漏文」是四路里判「某路整页漏读」的好启发式；字符集要加 `zh`（汉字 + 中文标点）；页码模型部分不需要 |
| `pages.py`（127） | 页脚页码 → `page_map.json`（pdf↔印刷页、跳号、无页码页、`manual` 人工映射、线性补推） | `candidates(text)`, `extract_printed(text, expected)`; CLI `--book [--source canonical\|primary]` | registry | — | — | — | **否** | 图书专属（印刷页码）。单据 / 截图没有这个概念；图书层留在 189 |
| `docling_arbitrate.py`（161） | 把 docling 当「第三票」投到已有分歧块上：按「短替换块数量」选页（E34），用分歧块左右各 8 字锚在第三路文本中定位，判 `primary`/`secondary`/`third`/`unlocatable`，出计票表 | `conflict_pages(book, top, lo, hi)`, `vote(pri, sec, dl, blocks) -> [{…,verdict}]`; CLI `--book --arm tesserocr\|ocrmac\|rapidocr --top 12 --sim 0.85,0.99` | docling CLI（1.1 GB `.venv-docling`）、diff 产物 | 第三路 | — | ⑤（多数票思路） | **参**（`vote` 算法）/ **否**（docling 依赖） | **这是四路比对的直接前身**：锚定位 + 投票 + 「三方都不同必须人看」的分类，正是 klode-kit「三路一致一路不同时多数值只是推荐值」的实现雏形。不要 docling（E33：它本身不识字）；把 `vote` 推广到 N 路，verdict 改成票型 `{value: [engines]}` |
| `docling_eval.py`（169） | docling 三臂在 12 页金标上的 CER 对照，`strip_docling` 剥 markdown 结构符 | CLI `--arms … [--ocr-mode]` | docling、cer | — | — | — | **否** | 一次性实验；结论已进 E13/E31–E33。只记一条规矩：比对前只剥结构标记、不改一个字 |
| `tune_eval.py`（94） | 金标页上跑多引擎（tess psm3/4/6 + mlx）算 CER 出 `report.md`，引擎输出缓存 | CLI `[--groups tune,accept --engines … --force]` | engines、cer、MANIFEST | A/B | — | 验收 | **参** | 引擎列表硬编码只认 tess*/mlx；重写成「对合成样本跑四路各出 CER」的 eval 脚本，沿用其缓存 + `.meta.json` 的做法 |
| `mlx_probe.py`（87） | 一次加载模型，对一页试 (预处理 × prompt) 组合，看哪种不退化 | CLI `<page.png> [--max-tokens --modes --prompts]` | mlx_vlm、PIL | B | — | — | **否**（留作调参工具）| 不进流水线；B 路换模型 / 换 prompt 时拿来探一次即可 |

### 2.1 跨模块的耦合点（搬入前必须解开）

| 耦合 | 现状 | klode-kit 做法 |
|---|---|---|
| 书目 / 分支 | 所有 CLI 都 `--book <slug>` 查 `registry.BOOKS` | 改为 `--batch <dir>` 或 manifest JSONL；每张图一行 `{id, path, sha256, lang_hint}` |
| 输出目录 | `work/ocr/<book>/<engine>/<fp>/page_NNNN.txt` | 保留「引擎名 + 配置指纹」两级，页改为图 id；每路输出一份 JSONL |
| 页标 `=== page NNNN ===` | `cer.norm`、`diff.norm`、`apply_fixes` 都靠它分头/正文 | JSONL 后不再需要；`normalize` 里的 `PAGE_MARK` 删掉即可 |
| 文本视图 | 全链用「NFKC + 去空白」归一视图比对，原文另存 | 保留；对数值字段**禁止** NFKC 之外的归一（`１０.５` ↔ `10.5` 可归一，`10.5` ↔ `1.05` 必须算分歧） |
| 两角色 | `primary` / `secondary` 到处硬编码 | 角色集 = `{vision, mlx, claude, codex}` + 可选 `tesseract`；「基准路」可配置，默认 mlx |

## 3 189 经验汇总 E01–E73：与本任务相关的条目

只列与 OCR 多引擎、字段比对、确认页、扫描件建库直接相关的；编号即 `189/doc/plan/klode建库经验汇总.md` 原编号。

| 编号 | 一句话 | 对 klode-kit 的含义 |
|---|---|---|
| E01 / E02 | 形态判定：有文字层就跳 OCR；`pdftotext -f N -l N` 一句判定 | `/kb-ocr` 入口先判形态；指南 PDF（场景 4）多半走文字层，不进四路 |
| E03 / E04 | 十五步流水线及顺序硬约束（回图在写卡后、门禁前） | 审计回路必须在导出 txt 之前完成——与交接文档「klode 无法发现表格数值互换，确认必须在导出前」一致 |
| E05 / E16 | 金标每本 6 页、**必须混入最难版式** | 合成测试样本要含：表格化验单、手写/盖章处方、低分辨率截图、竖排/混排 |
| E12 | 补丁层规则（唯一命中 / sha 校验 / 幂等 / 追加写） | ⑨ 写回的记录模型，见 §2 `apply_fixes` |
| E13 | 副引擎 Apple Vision：12 页金标 CER 0.039 vs tesseract 0.225，1.3 s/页 | A 路可信度高、速度快，适合做「基准路」候选；tesseract 只兜底 |
| E14 | 第三票按短替换块选页；亀甲括弧 `〔〕`→`[]` 同类错全书重复，只有第三票查得出 | 四路比对的价值在「系统性同类错」；白名单里要区分「引擎弱点（忽略）」与「系统性错（必须呈现）」 |
| E15 | MLX 必须 Python API；prompt / max_tokens / 退化换 alt prompt；300 dpi | B 路参数直接继承 |
| E19 / E20 | MLX CLI 退化；装反页在渲染步转正（`tesseract --psm 0` 看方向） | 截图场景也要先做方向检测（手机拍摄的单据常旋转） |
| E21 | 漏 `--fallback-secondary` 静默少页 | 任何「某路缺页」都要显式进 `uncertain`，不许静默 |
| E23 | 引文跨行时裁图找不到块 | 分歧小图按行裁，不按句 |
| E26 | 图表页 CER 爆表要**接受并如实标出**，不剔除 | 验收报告不许剔难页凑及格 |
| E31–E33 | docling 不识字、语言标签两套写法、markdown 结构符要剥 | 不引入 docling；比对前只剥结构标记 |
| E34 | 第三票选页策略 | 四路比对里「整页崩掉」与「个别字分歧」要分开处理 |
| E35 | 换引擎验收口径 = 分歧率 + 金标 CER，不是下游检索指标 | `/kb-ocr` 的验收指标定义 |
| E40 / E42 | 回图台账是最大工时；ocrmac 1.3 s/页、MLX 4–7 s/页 | 确认页就是把「回图」从人工全量变成只看分歧；每份 30 份报告级别的批次机器时间可忽略 |
| E45 | `klode check` 绿只证明「引文没腐烂」，不证明内容对 | 所以 verified 的来源必须是确认页，不是 klode check |
| E47 | 卡片中文说明 + 原文逐字锚点 | 单据库同样：L1/L2 中文，锚点是 verified 原文 |
| E68 | 功能回归里加「回放前后页标数相同」 | TDD 阶段必写的断言之一 |
| E73 | 什么该建 klode：逐字引用且引错有后果 → klode | 医疗单据正中此条 |
| C3 / C6 / C7（冲突表） | 副引擎已切 ocrmac 但手册「固定不动」表未改；验收口径之争；台账是否必需按形态分支 | 写 klode-kit 文档时直接采用新说法，不再并列 |
| G2 | 英文扫描件引擎 CER 未测 | 中文单据 / 中文截图的四路 CER 同样**没有任何实测**，阶段 6 试点前要用合成样本先测 |

## 4 003 文档要点（扫描件 OCR 重流水线）

| 文档 | 可直接拿的 | 不适用 / 要改 |
|---|---|---|
| `003/doc/plan/klode建库复用手册.md` | §2 十五步命令→产出表；§3「固定不动」参数（300 dpi、MLX 参数、CER 门槛 0.03/0.02、锚点 ≥ 15 字唯一）；§5 坑表；§7.1–7.3 副引擎切 ocrmac 后的差异与新坑 | 全文以「一本书」为单位；向量库 / 评测臂部分与本任务无关 |
| `003/doc/plan/klode架构.md` | §4 数据流图「扫描图才是原件，OCR 文本只是索引」「错字不改源，进 fixes 再重放」两条设计取舍；§1 边界「正文永不出网」 | klode-kit 云端两路要外发图片，与「正文永不出网」冲突 → 这正是「抬头打码」工作项存在的理由，设计里要显式写明哪些数据出网 |
| `003/doc/plan/两本专利日文书_OCR与klode建设计划.md` | §1 方法论三层（金标选主引擎 / 第二引擎只当分歧探测器 / 引文逐条回图）；§3.1 数据链；§3.2 字符块对齐 + 体检 + 异常页全审；§12 W01–W36 状态戳里的实测数字（MLX 调参页 CER 0.016、tesseract 0.39；分歧率 8.28%→7.14%；第三票 A 29 / B 56 条补丁） | §4 之后是书架 / 书桌 / 向量 / 评测，属 `/kb-build`，本文不盘 |

## 5 001 `src/wfviz`（WUA 渲染器）

| 项 | 结果 |
|---|---|
| 调用方式 | `cd 001 && PYTHONPATH=src python3 -m wfviz <wf.json> <out.svg> --mode inline [--style rup\|color]`。本次实跑 `examples/dinner.json` → 退出 0，SVG 18.7 KB |
| 入口 | `001/src/wfviz/__main__.py::main(argv)`；`--mode shelf`（默认，货架版）/ `inline`（讲稿单行版）；退出 2 = 输入不合法（stderr `error: …`，不写文件） |
| 输入格式定义处 | `001/src/wfviz/README.md`「输入格式」节 + `001/src/wfviz/model.py`（`parse`/`load`，所有校验规则）。顶层 `{title, title_en?, roles[{id,zh,en}], artifacts[{id,kind,zh,en}], steps[{id,zh,en,role,in?,out?,optional?,refers_to?}], loops[{from,to,zh,en}]}`；`kind ∈ artifact/code/data/report/checklist/template`；每个工件至少被一步引用；loop 的 `from` 必须在 `to` 之后（只向左画）；**禁用键** `extends/include/ref/inherits`（必须单文件自包含） |
| 图解页 | `python3 -m wfviz.handout <wf.json> <notes.json> <out.html>`，notes 按步骤 id 写 `one/points/command/cc/pitfall` |
| 依赖 | 纯 stdlib + 冻结的 `src/visual_kit/kit.py`；Python 3.9 语法即可（系统 python3 可跑） |
| 对本仓 | 三张 WUA 图按交接文档 §3-5 在本仓重画：wf.json 放 `docs/wua/`，渲染时 `PYTHONPATH=001/src` 指向 001，不复制渲染器 |

## 6 映射总表

### 6.1 四路读图 × 现有代码

| 路 | 引擎 | 现有实现 | 缺什么 |
|---|---|---|---|
| A | Apple Vision zh-Hans + ja | `engines.OcrMacEngine`（`ocrmac`，已在生产用作副引擎） | 多语言偏好列表；输出行级 bbox（内部已有，未暴露）；中文实测 CER |
| B | Unlimited-OCR MLX + tesseract 兜底 | `engines.MlxEngine`（主引擎，900 页实跑）+ `engines.TesseractEngine` | 块级 bbox 从 `raw` 提取（`quote_crop.find_block` 的正则可复用）；tesseract 改 `chi_sim+jpn`、输出 tsv 取 bbox；「MLX 退化 → tesseract 兜底」的切换规则（现为 alt prompt → failed → 副引擎） |
| C | Claude Opus 读图 | **无** | 适配器：`claude -p` + Read 工具（官方文档确认 Read 返回可见图像）或 Agent SDK 流式附图；固定最高档模型；输出契约 JSON；无 bbox → `bbox: null` |
| D | Codex `codex -i` | **无** | 适配器：`codex exec -i <png> -m <最高档> "<契约 prompt>"`；解析输出；无 bbox；外发前必须是打码图或合成图 |

### 6.2 审计 9 步 × 现有代码

| 步 | 现有可用 | 判定 | 新写 |
|---|---|---|---|
| ① 抬头打码 | 无（`OcrMacEngine` 的行框可用来**定位**姓名/编号区域） | — | 全新：Vision 定位 + 规则（关键词行 / 页顶区域）+ 涂黑 + 打码审计图 |
| ② 本机两路 | `engines` A/B + `run_batch` 的指纹/原子/续跑机制 | 搬 + 参 | 批跑壳重写（图目录为单位） |
| ③ 云端两路 | 无 | — | Claude / Codex 适配器 + 重试 + 成本记录 |
| ④ 抽字段 | `field_check.fields/core` 的结构 | 参 | 单据字段正则 / 词表（number/date/drug/unit/flag）+ 字段到行 bbox 的回链 |
| ⑤ 字段比对 + 弱点白名单 | `diff_engines.diff_page`（文本级）、`field_check`（字段级两栏）、`docling_arbitrate.vote`（投票分类）、`SMALL_KANA`（唯一白名单项） | 搬函数 + 参算法 | N 路票型；白名单配置化；「多数值只是推荐值」；`uncertain` 判定规则 |
| ⑥ 裁分歧小图 | `quote_crop.find_block` + 裁图 | 搬 | 统一像素 bbox；按行 ± 1 裁；无 bbox 的路落到有 bbox 的路的同一行 |
| ⑦ 确认页 | 无（189 是 `magick -append` 拼长图人眼看） | — | 全新：本地 HTML，每条分歧 = 小图 + 四路候选值 + 推荐值 + 「都不对/手输」 |
| ⑧ 点选 | 无 | — | 全新：选择结果落本地 JSON（无服务端：表单导出 / 本地文件写入方案待设计） |
| ⑨ 写回 | `apply_fixes` 的补丁模型（追加式、唯一命中、sha 校验、幂等、原子） | 搬规则 | verified 记录生成；`uncertain` 非空 → 拒绝 verified 的门禁；写回记录带 `chosen_engine` / `by` / `at` |

## 7 缺口清单（全新要写）

| # | 缺口 | 说明 | 可借的最近材料 |
|---|---|---|---|
| N1 | 统一 JSONL 契约 + 各路输出适配 | `{engine,page,line,text,fields:[{kind,value,bbox}]}`；坐标系统一为像素 `[x0,y0,x1,y1]`；无 bbox 路填 `null` | `engines.py` meta 结构、`quote_crop.DET` 正则 |
| N2 | 单据字段抽取器 | `number/date/drug/unit/flag` 五类；中文 / 日文 / 英文单位与标志；药名词表来源待定 | `field_check.py` 的 `core()` 与三栏报告 |
| N3 | N 路比对 + 票型 | 字段级为主、文本级为辅；票型 `{value: [engines]}`；一致 / 多数 / 平票 / 全不同 四类 | `diff_engines.diff_page`、`docling_arbitrate.vote` |
| N4 | 引擎弱点白名单 | 配置文件，按引擎列「该忽略的差异类」与「该高亮的系统性错」 | `SMALL_KANA` + E14 的亀甲括弧案例 |
| N5 | 抬头打码 | Vision 行框定位 + 涂黑 + 打码前后对照图；可选项 a/b/c 由用户在阶段 2 拍板 | `OcrMacEngine._rows` 的框 |
| N6 | Claude 读图适配器 | `claude -p` + Read，或 Agent SDK 流式；固定 Opus 最高档；契约 prompt；超时 / 重试 / 费用记录 | 官方文档 tools-reference、agent-sdk/streaming-vs-single-mode |
| N7 | Codex 读图适配器 | `codex exec -i`；固定最高档；只接受打码图 / 合成图 | `codex exec --help` |
| N8 | 确认页 HTML + 点选 + 写回 | 零服务端的本地页；点选结果如何落盘是设计点（下载 JSON / `file://` 写入限制 / 本地小 server 三选一） | 无（189 是拼图人眼看） |
| N9 | `uncertain` 门禁 | `uncertain` 非空不得 verified；缺页 / 某路失败 / 平票都进 `uncertain` | E21、E68 的断言思路 |
| N10 | 合成测试样本 | 假姓名 / 假编号的化验单、处方、书页；含最难版式 | E05 / E16 |
| N11 | 中文四路 CER 基线 | 现有数字全是日文书；中文截图 / 单据的 A/B 路 CER 为零实测 | G2 同类空白 |
| N12 | 截图拼接 | 交接文档提到私有项目有 `stitch_screenshots.py`（接缝留重复行）；本盘点未读私有项目，若要收进本仓需用户亲自指定 | — |

## 8 搬入时的红线提醒

- 189 的 `registry.py` 含书名等图书元数据，不搬；`config.json` 搬参数块时去掉 `_comment` 里的项目内路径。
- 本仓公开：合成样本一律假数据；Codex 外发只用合成图或打码图；云端两路的任何调试日志不得落真实图。
- 189 venv 是唯一同时有 mlx_vlm + ocrmac + pyobjc Vision 的环境；klode-kit 自建环境时按 `mlx_vlm==0.7.1`、`ocrmac==1.0.1`、`pyobjc-framework-Vision` 锁版本，装之前先问（E57 同类边界）。
- 不引入 docling（1.1 GB、不识字、每页十几秒）。
