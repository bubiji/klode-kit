"""Structured truth and table-scenario designs (WI-00a).

Each table scenario is a list of pages (one image = one record, ``p001`` inside its document)
plus per-route deviations. ``build_table_scenario`` renders the images, derives the four
simulated routes from the truth rows, and writes:

- ``<scenario>/images/<key>.png`` (and ``derived/`` when the routes read a transformed image);
- ``<scenario>/engine/<route>/<key>.jsonl`` (extra attempts in ``engine_t2/``, ``engine_t3/``);
- ``<scenario>/truth.json``: records, the fields with what each route read and where
  (``line_idx`` + ``span`` in that route's line), and a ``designed`` label
  (agree / majority / tie / plurality / all_differ / missing / agree_wrong);
- ``<scenario>/scenario.json``: description, pages, injections, attempts.

Field ids are fixture-local: ``<key>-l<NN>:<col>`` with NN = the vision line index of the row.
"""

from __future__ import annotations

import copy
from collections import Counter

from PIL import Image

import gen_common as C
import gen_engine_outputs as G
import gen_images as I

TRUTH_SCHEMA = "klode-kit.fixture-truth/1"


def lab(item, value, unit, flag="", ref=""):
    return {"item": item, "value": value, "unit": unit, "flag": flag, "ref": ref}


def rx(dose, usage="每日1次"):
    return {"item": C.DRUG, "value": dose, "unit": "", "flag": "", "ref": usage}


BASE_LAB = [
    lab("血红蛋白", "128", "g/L", "", "115-150"),
    lab("白细胞", "6.2", "10^9/L", "", "3.5-9.5"),
    lab("红细胞", "4.35", "10^12/L", "", "3.8-5.1"),
    lab("血小板", "210", "10^9/L", "", "125-350"),
    lab("中性粒细胞百分比", "78.5", "%", "H", "40-75"),
    lab("淋巴细胞百分比", "15.2", "%", "L", "20-50"),
]


def rows_with(base, **changes):
    """Copy of ``base`` with ``changes = {"r<i>_<col>": value}``."""
    out = copy.deepcopy(base)
    for k, v in changes.items():
        idx, col = k[1:].split("_", 1)
        out[int(idx)][col] = v
    return out


def label(routes: dict, truth: str) -> str:
    vals = [None if routes.get(e) is None else routes[e]["value"] for e in C.ROUTES]
    if any(v is None for v in vals):
        return "missing"
    if len(set(vals)) == 1:
        return "agree" if vals[0] == truth else "agree_wrong"
    counts = Counter(vals).most_common()
    if counts[0][1] == 3:
        return "majority"
    if counts[0][1] == 2 and counts[1][1] == 2:
        return "tie"
    if counts[0][1] == 2:
        return "plurality"
    return "all_differ"


# --------------------------------------------------------------------------- designs
# page: {"key", "kind", "date", "rows", optional "patient"/"patient_id", "stamp_row",
#        "vertical_text", "scale", "rotate", "approx_rows"}
# deviations per page: {"values": [(row, col, route, value)], "swap": (route, i),
#        "colshift": (route, i), "missingrow": (route, i), "merged": (route, i)}

SCENARIOS: dict[str, dict] = {
    "fx-labs-1x": {
        "description": "单张血常规，6 行，四路完全一致（基线；第 5 行用于裁图 ±1 行）。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-18", "rows": BASE_LAB}],
    },
    "fx-labs-3x": {
        "description": "同一患者三个日期各一张化验单；血红蛋白三次观测中两次数值相同（128、128、135）。四路一致。",
        "pages": [
            {"key": "p001", "kind": "lab", "date": "2026-07-18",
             "rows": [lab("血红蛋白", "128", "g/L", "", "115-150"), lab("白细胞", "5.8", "10^9/L", "", "3.5-9.5"),
                      lab("血小板", "198", "10^9/L", "", "125-350")]},
            {"key": "p002", "kind": "lab", "date": "2026-08-18",
             "rows": [lab("血红蛋白", "128", "g/L", "", "115-150"), lab("白细胞", "6.4", "10^9/L", "", "3.5-9.5"),
                      lab("血小板", "205", "10^9/L", "", "125-350")]},
            {"key": "p003", "kind": "lab", "date": "2026-09-18",
             "rows": [lab("血红蛋白", "135", "g/L", "", "115-150"), lab("白细胞", "7.1", "10^9/L", "", "3.5-9.5"),
                      lab("血小板", "221", "10^9/L", "", "125-350")]},
        ],
    },
    "fx-dup10": {
        "description": "两行数值都是 10（白细胞、尿素），四路一致；对齐必须按单元格保留 ordinal，不得合并。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-15",
                   "rows": [lab("血红蛋白", "128", "g/L", "", "115-150"), lab("白细胞", "10", "10^9/L", "H", "3.5-9.5"),
                            lab("尿素", "10", "mmol/L", "H", "3.1-8.0"), lab("血小板", "210", "10^9/L", "", "125-350")]}],
    },
    "fx-colshift": {
        "description": "mlx 路第 3 行（row 2）列错位：数值落进单位列、数值列为空；其余三路正确。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-11", "rows": BASE_LAB}],
        "deviations": {"p001": {"colshift": ("mlx", 2)}},
    },
    "fx-swap": {
        "description": "codex 路第 3/4 行（row 2、row 3）数值对调；值的多重集合不变。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-12", "rows": BASE_LAB}],
        "deviations": {"p001": {"swap": ("codex", 2)}},
    },
    "fx-missingrow": {
        "description": "claude 路缺第 5 行（row 4）；codex 路把 row 1、row 2 拼成一行（merged_rows）。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-13", "rows": BASE_LAB}],
        "deviations": {"p001": {"missingrow": ("claude", 4), "merged": ("codex", 1)}},
    },
    "fx-manualfix": {
        "description": "处方单一行示例片，图上规格为 1.05，四路都读成 10.5；人工确认时改为 1.05（日期 2026-09-18）。",
        "pages": [{"key": "p001", "kind": "rx", "date": "2026-09-18", "rows": [rx("1.05")]}],
        "deviations": {"p001": {"values": [(0, "value", e, "10.5") for e in C.ROUTES]}},
    },
    "fx-redo": {
        "description": "U6 重做：t1 四路齐、codex 在 row 0 数值有分歧（供人工点选为 r1）；t2 重做时 codex 缺 p001；t3 修复后四路一致。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-10-21", "rows": BASE_LAB[:4]}],
        "deviations": {"p001": {"values": [(0, "value", "codex", "126")]}},
        "attempts": {
            "t2": {"dir": "engine_t2", "missing": {"codex": ["p001"]}, "deviations": {}},
            "t3": {"dir": "engine_t3", "missing": {}, "deviations": {}},
        },
    },
    "fx-rx-stamp": {
        "description": "处方笺，三行示例片，红色印章压在第 2 行规格上；mlx 路把该格读错（多数票分歧）。抬头含姓名与编号，供打码测试。",
        "pages": [{"key": "p001", "kind": "rx", "date": "2026-09-19", "stamp_row": 1,
                   "rows": [rx("10mg"), rx("5mg", "每日2次"), rx("0.25g", "必要时")]}],
        "deviations": {"p001": {"values": [(1, "value", "mlx", "5rng")]}},
    },
    "fx-lowres": {
        "description": "低分辨率截图（按 0.4 倍缩小）；mlx 路把 4.35 读成 4.36（多数票分歧）。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-16", "rows": BASE_LAB[:4], "scale": 0.4}],
        "deviations": {"p001": {"values": [(2, "value", "mlx", "4.36")]}},
    },
    "fx-vertical": {
        "description": "横排表格右侧混排一列竖排文字（合成样本仅供测试）；四路一致。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-17", "rows": BASE_LAB[:5],
                   "vertical_text": "合成样本仅供测试"}],
    },
    "fx-rotated90": {
        "description": "整页顺时针旋转 90° 存储；逆时针转 90°（PIL ROTATE_90）复原。各路结果针对复原后的 derived 图。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-20", "rows": BASE_LAB, "rotate": True}],
    },
    "fx-u1-3labs": {
        "description": "U1：同日（2026-10-22）三张化验单，共恰好 5 条分歧：p001 三条（含一条四路全不同），p002 两条，p003 无分歧。",
        "pages": [
            {"key": "p001", "kind": "lab", "date": "2026-10-22", "rows": BASE_LAB},
            {"key": "p002", "kind": "lab", "date": "2026-10-22",
             "rows": rows_with(BASE_LAB, r0_value="131", r1_value="5.6", r3_value="188")},
            {"key": "p003", "kind": "lab", "date": "2026-10-22",
             "rows": rows_with(BASE_LAB, r0_value="119", r2_value="4.02", r5_value="22.4", r5_flag="")},
        ],
        "deviations": {
            "p001": {"values": [(0, "value", "codex", "126"), (2, "value", "mlx", "4.36"), (2, "value", "claude", "4.85"),
                                (2, "value", "codex", "4.33"), (4, "unit", "mlx", "‰")]},
            "p002": {"values": [(1, "value", "claude", "5.8"), (3, "value", "mlx", "183")]},
        },
    },
    "fx-u3-mixed": {
        "description": "U3：两张化验单，p001 四路完全一致（应 auto verified），p002 只有多数票分歧（两处）。",
        "pages": [
            {"key": "p001", "kind": "lab", "date": "2026-10-22", "rows": rows_with(BASE_LAB, r1_value="6.9", r3_value="243")},
            {"key": "p002", "kind": "lab", "date": "2026-10-22", "rows": rows_with(BASE_LAB, r0_value="133", r3_value="236")},
        ],
        "deviations": {"p002": {"values": [(0, "value", "mlx", "138"), (3, "value", "codex", "286")]}},
    },
    "fx-u8": {
        "description": "U8：两张化验单；p001 一处多数票、一处四路全不同；p002 的 p002-l07（row 2 数值）2:2 平票，另一处多数票。",
        "pages": [
            {"key": "p001", "kind": "lab", "date": "2026-10-22", "rows": rows_with(BASE_LAB, r0_value="124", r5_value="18.8")},
            {"key": "p002", "kind": "lab", "date": "2026-10-22", "rows": rows_with(BASE_LAB, r2_value="4.61", r4_value="70.1", r4_flag="")},
        ],
        "deviations": {
            "p001": {"values": [(1, "value", "codex", "8.2"), (3, "value", "mlx", "218"), (3, "value", "claude", "270"),
                                (3, "value", "codex", "216")]},
            "p002": {"values": [(2, "value", "claude", "4.67"), (2, "value", "codex", "4.67"), (4, "value", "vision", "70.7")]},
        },
    },
    "fx-approx-only": {
        "description": "row 2 只有 approx 几何（vision 该行 token 标 approx，且没有其他 exact 几何路），四路完全一致；该行应进 no_geometry，不能 auto verified。",
        "pages": [{"key": "p001", "kind": "lab", "date": "2026-09-14", "rows": BASE_LAB[:4], "approx_rows": [2]}],
    },
}


# --------------------------------------------------------------------------- assembly


def _route_rows(rows: list[dict], dev: dict) -> tuple[dict[str, list[dict]], list[dict]]:
    base = [dict(r, _src=i) for i, r in enumerate(rows)]
    routes = {e: copy.deepcopy(base) for e in C.ROUTES}
    injections = []
    for row, col, engine, value in dev.get("values", []):
        routes[engine][row][col] = value
        injections.append({"kind": "value", "route": engine, "row": row, "col": col, "value": value})
    if "swap" in dev:
        engine, i = dev["swap"]
        routes[engine] = G.inject_swap(routes[engine], i)
        injections.append({"kind": "swap", "route": engine, "rows": [i, i + 1]})
    if "colshift" in dev:
        engine, i = dev["colshift"]
        routes[engine] = G.inject_colshift(routes[engine], i)
        injections.append({"kind": "colshift", "route": engine, "row": i})
    if "missingrow" in dev:
        engine, i = dev["missingrow"]
        routes = G.inject_missingrow(routes, engine, i)
        injections.append({"kind": "missingrow", "route": engine, "row": i})
    if "merged" in dev:
        engine, i = dev["merged"]
        injections.append({"kind": "merged_rows", "route": engine, "rows": [i, i + 1]})
    return routes, injections


def _write_routes(files, name, engine_dir, key, layout, rows, dev, page_id, der, approx, skip=()):
    routes, injections = _route_rows(rows, dev)
    cells = {}
    for engine in C.ROUTES:
        if engine in skip:
            continue
        merged = None
        if dev.get("merged") and dev["merged"][0] == engine:
            merged = (dev["merged"][1], dev["merged"][1] + 1)
        lines, idx = G.route_lines(engine, layout, routes[engine], page_id=page_id, der_sha256=der,
                                   merged=merged, approx_rows=frozenset(approx) if engine == "vision" else frozenset())
        files[f"{name}/{engine_dir}/{engine}/{key}.jsonl"] = C.jlines(lines)
        cells[engine] = idx
    return cells, injections


def build_table_scenario(name: str, design: dict) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    records, fields, pages_meta, layouts, all_injections = [], [], [], {}, []
    extra_truth: dict = {}
    for page in design["pages"]:
        key = page["key"]
        spec = I.PageSpec(key=key, kind=page["kind"], date=page["date"], rows=page["rows"],
                          patient=page.get("patient", C.PATIENT), patient_id=page.get("patient_id", C.PATIENT_ID),
                          stamp_row=page.get("stamp_row"), vertical_text=page.get("vertical_text"),
                          scale=page.get("scale", 1.0))
        upright, layout = I.render(spec)
        upright_png = C.png_bytes(upright)
        if page.get("rotate"):
            stored_png = C.png_bytes(upright.transpose(Image.Transpose.ROTATE_270))
            files[f"{name}/derived/{key}.upright.png"] = upright_png
            extra_truth["upright_transform"] = {"rotate": 90, "pil_transpose": "ROTATE_90",
                                                "stored": "ROTATE_270 of the upright page",
                                                "derived": f"derived/{key}.upright.png"}
        else:
            stored_png = upright_png
        files[f"{name}/images/{key}.png"] = stored_png
        doc = C.doc_id(stored_png)
        page_id = f"{doc}/p001"
        der = C.sha256(upright_png)
        dev = design.get("deviations", {}).get(key, {})
        approx = page.get("approx_rows", [])
        cells, injections = _write_routes(files, name, "engine", key, layout, page["rows"], dev, page_id, der, approx)
        all_injections += [dict(x, key=key) for x in injections]
        for tag, att in design.get("attempts", {}).items():
            skip = tuple(e for e, keys in att["missing"].items() if key in keys)
            _write_routes(files, name, att["dir"], key, layout, page["rows"], att["deviations"].get(key, {}),
                          page_id, der, approx, skip=skip)

        vision_line = {r: cells["vision"][(r, "value")]["line_idx"] for r in range(len(page["rows"]))}
        rid = C.rec_id(doc)
        records.append({
            "key": key, "image": f"images/{key}.png", "doc_id": doc, "page_id": page_id, "rec_id": rid,
            "record_idx": 0, "record_type": page["kind"], "date": page["date"],
            "patient": spec.patient, "patient_id": spec.patient_id, "der_sha256": der,
            "rows": [dict(r, row=i, line_idx=vision_line[i]) for i, r in enumerate(page["rows"])],
        })
        for i, row in enumerate(page["rows"]):
            for col in ("value", "unit"):
                if not row[col]:
                    continue
                routes = {e: (cells[e].get((i, col)) if e in cells else None) for e in C.ROUTES}
                f = {"field_id": f"{key}-l{vision_line[i]:02d}:{col}", "key": key, "row": i, "col": col,
                     "item": row["item"], "truth": row[col], "routes": routes}
                f["designed"] = label(routes, row[col])
                fields.append(f)
        layouts[key] = {"header_box": I.header_box(layout), "table_box": I.table_box(layout),
                        "size": [upright.width, upright.height]}
        if approx:
            extra_truth["approx_row"] = {"key": key, "row": approx[0], "line_idx": vision_line[approx[0]]}
        pages_meta.append({"key": key, "image": f"images/{key}.png", "doc_id": doc, "page_id": page_id,
                           "der_sha256": der, "record_type": page["kind"], "date": page["date"]})

    truth = {"schema": TRUTH_SCHEMA, "scenario": name, "synthetic": True, "engine_dir": "engine",
             "records": records, "fields": fields, "injections": all_injections, "layout": layouts, **extra_truth}
    files[f"{name}/truth.json"] = C.jdump(truth)
    scenario = {"name": name, "description": design["description"], "synthetic": True, "kind": "table",
                "routes": list(C.ROUTES), "pages": pages_meta, "injections": all_injections,
                "attempts": {"t1": {"dir": "engine", "missing": {}},
                             **{t: {"dir": a["dir"], "missing": a["missing"]} for t, a in design.get("attempts", {}).items()}}}
    files[f"{name}/scenario.json"] = C.jdump(scenario)
    return files


def build() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for name, design in SCENARIOS.items():
        files.update(build_table_scenario(name, design))
    return files
