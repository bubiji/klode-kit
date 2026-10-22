"""Raster pages for the table scenarios (WI-00a): lab reports and prescriptions.

``render(spec)`` draws one page with Pillow and returns ``(image, layout)``. The layout is the
ground-truth geometry of every drawn text segment, in pixels of the returned image:
``[{"kind", "row", "joiner", "segments": [{"col", "text", "bbox"}]}]`` in reading order.
Simulated OCR routes (``gen_engine_outputs``) take their text and boxes from it.

Font: Hiragino Sans GB (macOS system font, ``gen_common.FONT_PATH``); only the rendered
pixels are committed. Regenerate on macOS only (README).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PIL import Image, ImageDraw, ImageFont

import gen_common as C

WIDTH = 960
COLS = {"item": 40, "value": 330, "unit": 470, "flag": 620, "ref": 690}
LAB_HEADER = {"item": "项目", "value": "结果", "unit": "单位", "flag": "提示", "ref": "参考范围"}
RX_HEADER = {"item": "药品", "value": "规格", "ref": "用法"}
ROW_H = 46
STAMP_RED = (200, 30, 30)


@dataclass
class PageSpec:
    key: str
    kind: str  # "lab" | "rx"
    date: str
    rows: list[dict]
    patient: str = C.PATIENT
    patient_id: str = C.PATIENT_ID
    stamp_row: int | None = None
    vertical_text: str | None = None
    scale: float = 1.0
    extra: dict = field(default_factory=dict)


_FONTS: dict[int, ImageFont.FreeTypeFont] = {}


def font(size: int) -> ImageFont.FreeTypeFont:
    if not C.FONT_PATH.is_file():
        raise SystemExit(f"font not found: {C.FONT_PATH} (fixtures are regenerated on macOS only)")
    if size not in _FONTS:
        _FONTS[size] = ImageFont.truetype(str(C.FONT_PATH), size, index=C.FONT_INDEX)
    return _FONTS[size]


def _union(boxes: list[list[int]]) -> list[int]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def line_bbox(line: dict) -> list[int]:
    return _union([s["bbox"] for s in line["segments"]])


def render(spec: PageSpec):
    title = f"{C.HOSPITAL} {'检验报告单' if spec.kind == 'lab' else '处方笺'}"
    header = LAB_HEADER if spec.kind == "lab" else RX_HEADER
    height = 290 + ROW_H * len(spec.rows) + 50
    if spec.vertical_text:
        height = max(height, 290 + 34 * len(spec.vertical_text) + 40)
    mode = "RGB" if spec.stamp_row is not None else "L"
    img = Image.new(mode, (WIDTH, height), "white")
    draw = ImageDraw.Draw(img)
    f_title, f_body = font(30), font(24)
    layout: list[dict] = []

    def seg(x: float, y: int, s: str, f, col: str) -> dict:
        draw.text((x, y), s, font=f, fill="black")
        return {"col": col, "text": s, "bbox": [int(v) for v in draw.textbbox((x, y), s, font=f)]}

    def kv(y: int, label: str, value: str, col: str) -> dict:
        a = seg(40, y, label, f_body, "label")
        b = seg(40 + draw.textlength(label, font=f_body), y, value, f_body, col)
        return {"kind": col, "row": None, "joiner": "", "segments": [a, b]}

    layout.append({"kind": "title", "row": None, "joiner": "", "segments": [seg(40, 24, title, f_title, "title")]})
    layout.append(kv(90, "姓名：", spec.patient, "patient"))
    layout.append(kv(130, "编号：", spec.patient_id, "patient_id"))
    layout.append(kv(170, "日期：", spec.date, "date"))
    layout.append({"kind": "header", "row": None, "joiner": " ",
                   "segments": [seg(COLS[c], 230, t, f_body, c) for c, t in header.items()]})
    for i, row in enumerate(spec.rows):
        y = 280 + ROW_H * i
        segs = [seg(COLS[c], y, row[c], f_body, c) for c in ("item", "value", "unit", "flag", "ref") if row.get(c)]
        layout.append({"kind": "row", "row": i, "joiner": " ", "segments": segs})
    if spec.vertical_text:
        segs = [seg(900, 280 + 34 * k, ch, f_body, "vertical") for k, ch in enumerate(spec.vertical_text)]
        layout.append({"kind": "vertical", "row": None, "joiner": "", "segments": segs})

    if spec.stamp_row is not None:
        target = next(s for s in layout if s["row"] == spec.stamp_row)["segments"][1]["bbox"]
        cx, cy = (target[0] + target[2]) // 2 + 20, (target[1] + target[3]) // 2
        draw.ellipse([cx - 62, cy - 62, cx + 62, cy + 62], outline=STAMP_RED, width=4)
        f_stamp = font(18)
        label = "示例专用章"
        draw.text((cx - draw.textlength(label, font=f_stamp) / 2, cy - 10), label, font=f_stamp, fill=STAMP_RED)

    if spec.scale != 1.0:
        s = spec.scale
        img = img.resize((round(img.width * s), round(img.height * s)), Image.Resampling.BILINEAR)
        for line in layout:
            for sg in line["segments"]:
                x0, y0, x1, y1 = sg["bbox"]
                sg["bbox"] = [math.floor(x0 * s), math.floor(y0 * s), math.ceil(x1 * s), math.ceil(y1 * s)]
    return img, layout


def header_box(layout: list[dict], pad: int = 6) -> list[int]:
    """Box covering the name and ID lines (what the masker must black out)."""
    box = _union([line_bbox(x) for x in layout if x["kind"] in ("patient", "patient_id")])
    return [max(0, box[0] - pad), max(0, box[1] - pad), box[2] + pad, box[3] + pad]


def table_box(layout: list[dict]) -> list[int]:
    return _union([line_bbox(x) for x in layout if x["kind"] in ("header", "row")])


def masked(img, box: list[int]):
    out = img.copy()
    ImageDraw.Draw(out).rectangle(box, fill="black")
    return out
