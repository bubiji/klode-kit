"""Synthetic PDFs (WI-00a): text-layer guide, mixed text/empty/garbled/scan, truncated copy.

reportlab with ``invariant=1`` (no timestamps or random document IDs). CJK text uses the
built-in CID font STSong-Light (not embedded; pdftotext extracts it through Adobe-GB1).
"""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

import gen_common as C
import gen_images as I
import gen_truth as T

GUIDE_PAGES = [
    ("第一章 示例流程总览",
     "本指南是为测试而编写的合成文本，不对应任何真实机构或个人。示例流程分为登记、识别、核对、"
     "建库四个阶段。登记阶段记录单据类型、日期与来源；识别阶段由多路引擎分别读取页面文字；"
     "核对阶段把各路结果逐格比对，分歧之处交给使用者在本地确认页点选；建库阶段只接收已经确认的记录。"),
    ("第二章 核对规则",
     "多数票只作为推荐值，不会自动改动任何字段。四路一致且没有任何不确定条目的记录才可以自动确认。"
     "平票或四路各不相同的单元格必须进入确认页。缺少某一路结果的页面整体标为争议，修复后重新比对。"
     "人工输入的值需要附带区域证据，说明在图像的哪个位置看到了这个值。"),
    ("第三章 建库与检查",
     "建库时按项目生成时间序列文本，每次观测占一行，行内依次为项目、数值、日期与记录编号。"
     "检查阶段逐条核验卡片中的引文，任何引文找不到原文都会让检查失败并指出回到哪一条回路。"
     "发布时只切换一个指针，旧版本保留不动，回滚只需把指针指回上一个版本。"),
]
MIXED_TEXT = (
    "混排文档第一页为正常文字层。本页内容为合成文本，用于验证按页分流：文字层充足的页面直接使用"
    "文字层结果，不再送入四路识别；空白页、乱码页与扫描页则渲染成图像后进入四路识别。"
)
GARBLED = "ÿþ¤¦§¨©ª«¬®¯°±²³´µ¶·¸¹º»¼½¾¿×÷ÐÞßðþ" * 8

_registered = False


def _font() -> str:
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(C.PDF_CJK_FONT))
        _registered = True
    return C.PDF_CJK_FONT


def _wrap(s: str, width: int) -> list[str]:
    return [s[i:i + width] for i in range(0, len(s), width)]


def _canvas(buf):
    c = canvas.Canvas(buf, pagesize=A4, invariant=1, pageCompression=1)
    c.setAuthor("klode-kit synthetic fixtures")
    c.setTitle("synthetic")
    return c


def _text_page(c, title: str, body: str) -> None:
    font = _font()
    c.setFont(font, 18)
    c.drawString(72, 770, title)
    c.setFont(font, 12)
    y = 740
    for line in _wrap(body, 36):
        c.drawString(72, y, line)
        y -= 20


def text_pdf() -> bytes:
    buf = io.BytesIO()
    c = _canvas(buf)
    for title, body in GUIDE_PAGES:
        _text_page(c, title, body)
        c.showPage()
    c.save()
    return buf.getvalue()


def mixed_pdf() -> bytes:
    buf = io.BytesIO()
    c = _canvas(buf)
    _text_page(c, "混排示例 第 1 页", MIXED_TEXT)
    c.showPage()
    c.showPage()  # page 2: empty (no text layer at all)
    c.setFont("Helvetica", 12)
    y = 770
    for line in _wrap(GARBLED, 48):  # page 3: a text layer of symbol noise
        c.drawString(72, y, line)
        y -= 18
    c.showPage()
    spec = I.PageSpec(key="scan", kind="lab", date="2026-09-25", rows=T.BASE_LAB[:4])
    img, _ = I.render(spec)  # page 4: scanned page, image only
    small = img.resize((img.width // 2, img.height // 2))
    c.drawImage(ImageReader(small), 72, 300, width=img.width * 0.45, height=img.height * 0.45)
    c.showPage()
    c.save()
    return buf.getvalue()


def build() -> dict[str, bytes]:
    guide = text_pdf()
    mixed = mixed_pdf()
    corrupt = guide[: len(guide) // 2]
    files = {
        "fx-text-pdf/guide-2026.pdf": guide,
        "fx-text-pdf/scenario.json": C.jdump({
            "name": "fx-text-pdf", "synthetic": True, "kind": "pdf", "file": "guide-2026.pdf",
            "description": "三页文字层 PDF（STSong-Light），每页字符数远超 min_chars；应整本跳过四路。",
            "pages": [{"page": i + 1, "class": "text", "title": t, "chars": len(t) + len(b)}
                      for i, (t, b) in enumerate(GUIDE_PAGES)],
            "expect": {"text_layer": True, "routes_skipped": list(C.ROUTES)},
        }),
        "fx-mixed-pdf/mixed.pdf": mixed,
        "fx-mixed-pdf/scenario.json": C.jdump({
            "name": "fx-mixed-pdf", "synthetic": True, "kind": "pdf", "file": "mixed.pdf",
            "description": "四页混排：text / empty（无文字层）/ garbled（文字层为符号噪声）/ 扫描页（只有图像，按 empty 分流）。",
            "pages": [
                {"page": 1, "class": "text"},
                {"page": 2, "class": "empty"},
                {"page": 3, "class": "garbled"},
                {"page": 4, "class": "empty", "note": "scan: image only, no text layer"},
            ],
            "expect": {"text_layer": "partial", "ocr_pages": [2, 3, 4], "skipped_pages": [1]},
        }),
        "fx-corrupt-pdf/corrupt.pdf": corrupt,
        "fx-corrupt-pdf/scenario.json": C.jdump({
            "name": "fx-corrupt-pdf", "synthetic": True, "kind": "pdf", "file": "corrupt.pdf",
            "description": "fx-text-pdf/guide-2026.pdf 的前一半字节（截断，缺 xref 与 %%EOF）。",
            "source": "fx-text-pdf/guide-2026.pdf", "truncated_to": len(corrupt), "source_bytes": len(guide),
            "expect": {"exit": "non-zero", "receipt_reason": "pdf_unreadable", "text_outputs": 0},
        }),
    }
    return files
