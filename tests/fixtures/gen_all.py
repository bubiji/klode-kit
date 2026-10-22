"""Generate every synthetic fixture (WI-00a): ``<venv>/bin/python tests/fixtures/gen_all.py``.

    --out DIR              write the complete tree (products, generator copies, README, MANIFEST) into DIR
    --check [--against D]  generate into a tmp dir and compare with D (default: tests/fixtures);
                           exit 0 only when every file is byte-identical, else list the drift, exit 1
    --write                update tests/fixtures in place; any file that would be overwritten or is no
                           longer generated is first moved to <repo>/_trash/fixtures-<UTC time>/ (never deleted)

Regenerate on macOS only (system CJK font, locked Pillow/reportlab); Linux CI never regenerates.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "src"))

import gen_book  # noqa: E402
import gen_chain  # noqa: E402
import gen_common as C  # noqa: E402
import gen_pdf  # noqa: E402
import gen_project  # noqa: E402
import gen_truth  # noqa: E402
from klode_kit.devtools import fixture_manifest  # noqa: E402

SKIP_PARTS = {"__pycache__"}
SKIP_NAMES = {".DS_Store"}


def readme() -> str:
    out = [
        "# tests/fixtures — 合成测试样本",
        "",
        "**本目录全部合成**：所有图像、PDF、文本、JSON 均由本目录的 `gen_*.py` 确定性生成，不含任何真实",
        "人员、单据或医疗数据。人名、编号、药名只用白名单：姓名 `测试患者甲`（`测试患者乙`…），编号",
        "`T-0000001`（`T-0000002`…），药名 `示例片`，机构名 `示例医院`。`privacy_scan` 对本目录 0 命中；",
        "单元测试 00a-05 另做一次独立抽取核对。",
        "",
        "## 生成与校验",
        "",
        "- 生成：`.venv/bin/python tests/fixtures/gen_all.py --write`（原地更新；被替换或不再生成的旧文件先移入",
        "  仓库根 `_trash/fixtures-<UTC 时间>/`，从不删除）。",
        "- 确定性：无时钟、无随机；`--out DIR` 两次生成逐字节相同（00a-01）；`--check` 生成到临时目录后与",
        "  本目录逐字节比较（00a-02）。这两条在 `cli_integration` 的 `macos` 子集里跑；Linux CI 不再生成，",
        "  只用已提交的成品（00a-03 校验 MANIFEST）。",
        "- `MANIFEST.json`：本目录每个文件（含 `gen_*.py` 与本 README，不含 MANIFEST 自身、`__pycache__/`）的",
        "  `path / sha256 / bytes`；`python -m klode_kit.devtools.fixture_manifest verify` 双向校验。",
        "",
        "## 字体来源",
        "",
        "- 位图（PNG）：macOS 系统字体 **Hiragino Sans GB**（`/System/Library/Fonts/Hiragino Sans GB.ttc`，",
        "  index 0），由 Pillow 渲染。字体文件本身不入库、不再分发，只提交渲染后的像素；可再分发条件不明，",
        "  因此**只在 macOS 上再生成**。",
        "- 文字层 PDF：reportlab 内置 CID 字体 **STSong-Light**（不嵌入字体文件，Adobe-GB1 编码）。",
        "- 版本以 `uv.lock` 为准（Pillow、reportlab）；升级任一项都要重新生成并提交，MANIFEST 随之变化。",
        "",
        "## 目录约定（表格类场景）",
        "",
        "- `images/<key>.png`：原件（一张图 = 一条记录，契约 `page_id = <doc_id>/p001`；`<key>` 是批内序号）。",
        "- `engine/<route>/<key>.jsonl`：模拟的四路输出（vision / mlx / claude / codex），行契约",
        "  `{engine, page_id, der_sha256, line_idx, text, bbox_px, tokens}`；云端两路 `bbox_px`、`tokens` 为 null，",
        "  mlx 只有行框，vision 有词级 `tokens[].geometry`（exact/approx）。",
        "- `truth.json`：结构化真值。`fields[]` 记每个字段的真值、各路读到的值及其在该路行内的 `line_idx + span`，",
        "  和设计标签 `designed`（agree / majority / tie / plurality / all_differ / missing）。字段 id 为",
        "  `<key>-l<NN>:<col>`，NN 是 vision 路该行的行号（如 `p002-l07:value`）。",
        "- `scenario.json`：场景说明、页清单、注入的错误、多次尝试（attempts）。",
        "- 错误注入器在 `gen_engine_outputs.py`：swap / colshift / missingrow / degenerate；退化判定",
        "  `klode_kit.ocr.degenerate`（`repeat_ratio > 0.3`）。",
        "",
    ]
    for name, design in gen_truth.SCENARIOS.items():
        out += [f"## `{name}`", "", design["description"], ""]
    out += [
        "## `fx-text-pdf`", "",
        "三页文字层 PDF `guide-2026.pdf`（STSong-Light 合成指南文本），每页字符数远超 `min_chars`；",
        "应判 `text_layer: true` 并跳过四路（U4）。", "",
        "## `fx-mixed-pdf`", "",
        "四页混排 `mixed.pdf`：第 1 页文字层、第 2 页空白、第 3 页文字层为符号噪声（garbled）、第 4 页只有扫描图像；",
        "只有第 1 页跳过四路（`scenario.json` 记每页分类）。", "",
        "## `fx-corrupt-pdf`", "",
        "`corrupt.pdf` 是 `fx-text-pdf/guide-2026.pdf` 的前一半字节（截断、无 `%%EOF`）；应 exit ≠ 0、",
        "receipt 记 `pdf_unreadable`、不产出任何 text 产物（04c-06）。", "",
        "## `fx-book-aurora`", "",
        "3 章合成英文小书 `aurora-kettle.txt`（虚构的 aurora kettle，不含人名），章头 `Chapter N: <title>`；",
        "`src/desk.json` 三维度各 1 题调优题，`src/holdout.jsonl` 5 题冻结留出集（U7、02a/02c）。", "",
        "## `synth_project`", "",
        "假数据项目：`CLAUDE.md`、假 `.mcp.json`（一个无害的 `true` 命令）、`klode-kit.toml`（只覆盖",
        "`route_retry = 5`，00b2-11）。canary 文件由测试在 tmp 拷贝里现场放置，不入库。", "",
        "## `replay/probe-synth`", "",
        "隔离探针事件流（合成内容、真实形状）：`denied`（含 StructuredOutput 被拒的噪声）、`denied-no-guard`（无 guard",
        "日志，只凭 `permission_denials`）、`enoent`、`unattempted`、`mixed`、`narrative-lies`（模型文字声称被拒、",
        "工具结果实为成功）为 `claude -p --output-format stream-json --verbose` 2.1.285 形状，配 `<name>.guard.jsonl`；",
        "`codex-denied`、`codex-read` 为 `codex exec --json` 0.163.0 形状。形状取自 WI-00c2 在 G2 下的真实录制",
        "（2026-10-23）；codex 的拒绝 / ENOENT 输出未实测到（真实运行两个 canary 都读到了），为推定形状。",
        "`expected.json` 给出每个 canary 的判定（denied / enoent / unattempted / read）与结论（只有全部 denied 才 PASS）。", "",
        "## `fx-chain`", "",
        "完整来源链产物（三段不变量 + 版本绑定，02 r3.1）。每个子目录都是一套完整产物，`variant.json` 写明基底、",
        "期望违例与相对基底改动的文件；`clean` 即矩阵里的 `fx-chain`。",
        "",
        "| 变体 | 期望 | 说明 |",
        "|---|---|---|",
    ]
    for name in gen_chain.LEGAL:
        out.append(f"| `{name}` | 0 违例 | {gen_chain.DESCRIPTIONS[name]} |")
    for n, (base, code, desc, _) in gen_chain.BROKEN.items():
        out.append(f"| `broken-{n}` | 恰好一个 `{code}`（基底 `{base}`） | {desc} |")
    out += [
        "",
        "说明：`fx-publish-ab`（两个 build 的发布切换）由测试在 tmp 里用回放构建，不入库。",
        "",
    ]
    return "\n".join(out)


def generate() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for part in (gen_truth.build(), gen_pdf.build(), gen_book.build(), gen_project.build(), gen_chain.build()):
        overlap = set(files) & set(part)
        if overlap:
            raise SystemExit(f"two generators produce the same file: {sorted(overlap)[:5]}")
        files.update(part)
    pngs = Counter(C.sha256(v) for k, v in files.items()
                   if k.endswith(".png") and not k.startswith("fx-chain/"))
    dups = [s for s, n in pngs.items() if n > 1]
    if dups:
        raise SystemExit(f"scenario images must be unique (sha-keyed replay); duplicates: {dups[:3]}")
    for src in sorted(HERE.glob("gen_*.py")):
        files[src.name] = src.read_bytes()
    files["README.md"] = readme().encode("utf-8")
    files[fixture_manifest.MANIFEST_NAME] = fixture_manifest.dumps(fixture_manifest.entries_for(files))
    return files


def tree(root: Path) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not any(x in SKIP_PARTS for x in rel.parts) and p.name not in SKIP_NAMES:
            out[rel.as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def write_tree(files: dict[str, bytes], out: Path) -> None:
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"--out target is not empty: {out}")
    for rel, data in files.items():
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def compare(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    drift = []
    for rel in sorted(set(expected) | set(actual)):
        if rel not in actual:
            drift.append(f"missing: {rel}")
        elif rel not in expected:
            drift.append(f"extra: {rel}")
        elif expected[rel] != actual[rel]:
            drift.append(f"differs: {rel}")
    return drift


def write_in_place(files: dict[str, bytes]) -> int:
    trash = C.REPO / "_trash" / time.strftime("fixtures-%Y%m%dT%H%M%SZ", time.gmtime())
    current = tree(HERE)
    moved = written = 0

    def stash(rel: str) -> None:
        nonlocal moved
        dst = trash / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(HERE / rel), str(dst))
        moved += 1

    for rel in sorted(set(current) - set(files)):
        stash(rel)
    for rel, data in files.items():
        if rel in current and current[rel] == C.sha256(data):
            continue
        if rel.startswith("gen_") and rel.endswith(".py"):
            continue  # generator sources are the input, never rewritten
        if rel in current:
            stash(rel)
        path = HERE / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        written += 1
    for d in sorted((p for p in HERE.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if d.name not in SKIP_PARTS and not any(d.iterdir()):
            dst = trash / d.relative_to(HERE)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(d), str(dst))
            moved += 1
    print(f"gen_all: wrote {written} file(s); moved {moved} old file(s) to {trash if moved else '(nothing)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gen_all")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out", type=Path)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("--against", type=Path, default=HERE)
    args = parser.parse_args(argv)

    files = generate()
    if args.out:
        write_tree(files, args.out)
        print(f"gen_all: {len(files)} file(s) -> {args.out}")
        return 0
    if args.write:
        return write_in_place(files)
    with tempfile.TemporaryDirectory(prefix="klode-kit-fixtures-") as tmp:
        fresh = Path(tmp) / "fixtures"
        write_tree(files, fresh)
        drift = compare(tree(fresh), tree(args.against))
    for line in drift:
        print(line)
    print(f"gen_all --check: {len(drift)} difference(s) against {args.against}")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
