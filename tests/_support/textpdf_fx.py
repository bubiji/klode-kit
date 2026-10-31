"""WI-33d: a synthetic lab report PDF *with a text layer* (real-data pilot P8; the shape of the pilot's 11 lab PDFs).

Built at test time with reportlab (``invariant=1``: same bytes every run) and the built-in CID font ``STSong-Light``,
the way ``tests/fixtures/gen_pdf.py`` builds the guide PDF. Nothing here is real data: the names, the numbers and the
hospital are made up. The rows are the rows of ``fx-u1-3labs`` page 1, so the text a text-layer reader gives is the
text the recorded OCR routes give for the image of such a report.
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

FONT = "STSong-Light"
ROWS = ("血红蛋白 128 g/L 115-150", "白细胞 6.2 10^9/L 3.5-9.5", "红细胞 4.35 10^12/L 3.8-5.1",
        "血小板 210 10^9/L 125-350", "中性粒细胞百分比 78.5 % H 40-75", "淋巴细胞百分比 15.2 % L 20-50")


def page_lines(patient: str, number: str, day: str, rows=ROWS, title: str = "示例医院 检验报告单") -> list[str]:
    return [title, f"姓名：{patient}", f"编号：{number}", f"日期：{day}", "项目 结果 单位 提示 参考范围", *rows]


def lab_pdf(pages: list[list[str]]) -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1, pageCompression=1)
    c.setAuthor("klode-kit synthetic fixtures")
    c.setTitle("synthetic")
    for lines in pages:
        c.setFont(FONT, 14)
        y = 780
        for line in lines:
            c.drawString(40, y, line)
            y -= 30
        c.showPage()
    c.save()
    return buf.getvalue()


def write_lab_pdf(path: Path, pages: list[list[str]] | None = None) -> Path:
    pages = pages or [page_lines("测试患者甲", "T-0000001", "2026-10-22"),
                      page_lines("测试患者乙", "T-0000002", "2026-10-22")]
    path.write_bytes(lab_pdf(pages))
    return path
