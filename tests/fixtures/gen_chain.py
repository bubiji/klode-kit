"""fx-chain (WI-00a): complete provenance-chain products plus legal and broken variants.

Layout of every variant directory (02 r2.2-A, r3.1, r3.2, r4.1; field names provisional until
WI-00b1/00b3 fix the schemas - this generator is the place to update them):

    chain.json                       batch_id, rec_ids, routes, base_route, kb_id
    variant.json                     base variant, expected violations, mutated files
    images/p001.png | source/p001.pdf   original (manifest img_sha256)
    derived_img/p001.m.png | derived_img/p001.r150.png   what the routes read (der_sha256)
    manifest.jsonl  derived.jsonl  [page_map.jsonl]
    ocr/<engine>/<cfg_fp>.t1/p001.jsonl          route lines (contract + run_id)
    audit/<batch>/a1/{runs.json, cells.jsonl, fixes.jsonl}
    records/_by_id/<rec_id>.md       effective + candidates (JSON front matter)
    records/_rev/<rec_id>.r<N>.md    immutable revision bodies (fields by field_id)
    build/build_receipt.json         audit_bindings {rec_id: {rev, audit_rev}}
    build/src/export_map.jsonl       one row per exported line: rev, text, positions, transforms
    build/library/items/<slug>.txt   the exported lines

Effective decision of a field = among its decisions with ``rev_new <= bound rev``, the one with
the largest ``rev_new``; a verified field has exactly one.
"""

from __future__ import annotations

import copy
import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import gen_common as C
import gen_engine_outputs as G
import gen_images as I
import gen_pdf

DATE = "2026-09-18"
SHIFTED = "2026-10-02"
KB = "fx-chain-klode"
DPI = 150
ROWS = [
    {"item": "血红蛋白", "slug": "hemoglobin", "value": "128", "unit": "g/L"},
    {"item": "白细胞", "slug": "wbc", "value": "6.2", "unit": "10^9/L"},
    {"item": "血小板", "slug": "platelet", "value": "210", "unit": "10^9/L"},
    {"item": "示例指标", "slug": "sample-index", "value": "1.05", "unit": "mmol/L"},
]
LEGAL = {
    "clean": {},
    "legal-manual": {"manual": True},
    "legal-dateshift": {"dateshift": True},
    "legal-alias": {"alias": True},
    "legal-oldbuild": {"oldbuild": True},
    "legal-pdftext_dpi": {"pdftext": True},
}
DESCRIPTIONS = {
    "clean": "完整来源链：四路读同一张打码 derived 图；row 1 数值分歧（vision 6.2，其余 6.8），人工按 route=vision 确认；其余字段 auto。",
    "legal-manual": "四路都把 示例指标 读成 10.5，人工 manual 改为 1.05，evidence 带 region 与 seen；段 ① 对 10.5 仍成立。",
    "legal-dateshift": "deident.mode=dateshift 导出：每行日期经 dateshift transform 偏移到 2026-10-02。",
    "legal-alias": "row 0 项目名四路都读作 HGB，导出时经 alias transform 归一为 血红蛋白（只作用于 item）。",
    "legal-oldbuild": "记录已推进到 rev 2（row 1 改选 mlx 的 6.8），旧 build 仍绑定 rev 1 且自洽。",
    "legal-pdftext_dpi": "文字层 PDF 单路 pdftext：bbox_pt 经 page_map.dpi=150 换算为 bbox_px。",
}
BROKEN = {
    1: ("clean", "span_mismatch", "vision 路 labs[0].value 的 prov.span 右移 1 位，行文本在该跨度处 ≠ 候选值。", {"engine": "vision"}),
    2: ("clean", "der_parent_mismatch", "derived.jsonl 的 parent 不等于 manifest 的 img_sha256。", {}),
    3: ("legal-manual", "manual_evidence_missing", "manual 决定的 evidence 去掉 region。", {}),
    4: ("clean", "multiple_effective_decisions", "labs[2].value 多出一条同 rev 的生效决定。", {}),
    5: ("clean", "transform_on_numeric", "导出行给 labs[0].value（数值）加了 alias transform（from==to，文本不变）。", {}),
    6: ("legal-oldbuild", "rev_binding_mismatch", "export_map 中 row 0 那一行 rev=2，而 audit_bindings 绑定 rev 1（该行字段在 r1/r2 相同）。", {}),
    7: ("clean", "missing_effective_decision", "verified 记录的 date 字段没有任何决定。", {}),
    8: ("clean", "route_value_mismatch", "row 1 人工决定 route=vision，写入的却是★推荐值 6.8；记录与导出同步为 6.8。", {}),
    9: ("clean", "record_value_mismatch", "rev 1 记录里 labs[0].value 为 129，决定值是 128；导出与记录一致（129）。", {}),
    10: ("clean", "export_text_mismatch", "导出行在 labs[0].value 声明位置上的文本为 182 ≠ apply(transforms, 128)。", {}),
    11: ("legal-pdftext_dpi", "pdftext_dpi_mismatch", "pdftext 路 row 0 那一行的 bbox_px 直接用 72dpi 的 bbox_pt，没有按 page_map.dpi 换算。", {}),
    12: ("clean", "span_mismatch", "codex 路（bbox_px:null）labs[3].value 的 prov.span 右移 1 位。", {"engine": "codex"}),
}


def _pdf_page(rows) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4, invariant=1, pageCompression=1)
    font = gen_pdf._font()
    c.setFont(font, 12)
    y = 770
    for line in [f"{C.HOSPITAL} 检验报告单", f"日期：{DATE}"] + [f"{r['item']} {r['value']} {r['unit']}" for r in rows]:
        c.drawString(72, y, line)
        y -= 20
    c.showPage()
    c.save()
    return buf.getvalue()


def _token_bbox(line, span):
    for t in line.get("tokens") or []:
        if t["span"] == span:
            return t["bbox_px"]
    return None


def build_model(opts: dict) -> dict:
    rows = copy.deepcopy(ROWS)
    drawn = [{"item": r["item"], "value": r["value"], "unit": r["unit"], "flag": "", "ref": ""} for r in rows]
    if opts.get("alias"):
        drawn[0]["item"] = "HGB"
    spec = I.PageSpec(key="p001", kind="lab", date=DATE, rows=drawn)
    page_img, layout = I.render(spec)

    pdftext = bool(opts.get("pdftext"))
    images: dict[str, bytes] = {}
    if pdftext:
        original = _pdf_page(rows)
        images["source/p001.pdf"] = original
        der_png = C.png_bytes(page_img)
        der_path = "derived_img/p001.r150.png"
        transform = {"rotate": 0, "crop": None, "mask_boxes": [], "render_dpi": DPI}
        kind = "pdf"
        orig_path = "source/p001.pdf"
    else:
        original = C.png_bytes(page_img)
        images["images/p001.png"] = original
        box = I.header_box(layout)
        der_png = C.png_bytes(I.masked(page_img, box))
        der_path = "derived_img/p001.m.png"
        transform = {"rotate": 0, "crop": None, "mask_boxes": [box]}
        kind = "image"
        orig_path = "images/p001.png"
    images[der_path] = der_png

    img_sha, der = C.sha256(original), C.sha256(der_png)
    doc = C.doc_id(original)
    page_id = f"{doc}/p001"
    rec = C.rec_id(doc)
    batch = C.sha256("\n".join(sorted([img_sha])).encode())[:12]
    audit_rev = f"{batch}/a1"
    engines = ["pdftext"] if pdftext else list(C.ROUTES)
    base_route = engines[0]

    readable = [x for x in layout if x["kind"] not in ("patient", "patient_id")]
    ocr, runs, idx = {}, {}, {}
    for engine in engines:
        route_rows = [dict(r, _src=i) for i, r in enumerate(drawn)]
        if not pdftext and engine != "vision":
            route_rows[1]["value"] = "6.8"
        if opts.get("manual"):
            route_rows[3]["value"] = "10.5"
        render_as = "vision" if engine == "pdftext" else engine
        lines, cells = G.route_lines(render_as, readable, route_rows, page_id=page_id, der_sha256=der)
        cfg_fp = C.sha256(f"synthetic-cfg|{engine}".encode())[:12]
        run_id = f"{batch}/{engine}/{cfg_fp}/t1"
        for ln in lines:
            ln["engine"] = engine
            ln["run_id"] = run_id
            if engine == "pdftext":
                ln["bbox_pt"] = [round(v * 72 / DPI, 2) for v in ln["bbox_px"]]
                ln["bbox_px"] = [round(v * DPI / 72) for v in ln["bbox_pt"]]
                for t in ln["tokens"]:
                    t["bbox_pt"] = [round(v * 72 / DPI, 2) for v in t["bbox_px"]]
                    t["bbox_px"] = [round(v * DPI / 72) for v in t["bbox_pt"]]
        date_line = next(ln for ln in lines if ln["text"].startswith("日期："))
        cells[(None, "date")] = {"line_idx": date_line["line_idx"], "span": [3, 3 + len(DATE)], "value": DATE}
        ocr[engine] = {"path": f"ocr/{engine}/{cfg_fp}.t1/p001.jsonl", "lines": lines}
        runs[engine] = run_id
        idx[engine] = cells

    field_keys = [(None, "date")] + [(i, c) for i in range(len(rows)) for c in ("item", "value", "unit")]

    def fid(key):
        return f"{rec}/date" if key[0] is None else f"{rec}/labs[{key[0]}].{key[1]}"

    cells_rows = []
    for key in field_keys:
        cands, prov = {}, {}
        for engine in engines:
            ref = idx[engine][key]
            line = ocr[engine]["lines"][ref["line_idx"]]
            if engine in ("claude", "codex"):
                bbox = None
            elif engine == "mlx":
                bbox = line["bbox_px"]
            else:
                bbox = _token_bbox(line, ref["span"]) or line["bbox_px"]
            cands[engine] = {"value": ref["value"]}
            prov[engine] = {"run_id": runs[engine], "line_idx": ref["line_idx"], "span": ref["span"],
                            "bbox_px": bbox, "der_sha256": der}
        cells_rows.append({"field_id": fid(key), "rec_id": rec, "slot": fid(key).split("/", 1)[1],
                           "cell": {"row": key[0], "col": key[1]}, "candidates": cands, "prov": prov})

    commit = f"{audit_rev}/c0001"
    fixes = []
    for n, cell in enumerate(cells_rows, start=1):
        cands = cell["candidates"]
        seen = {e: v["value"] for e, v in cands.items()}
        d = {"decision_id": f"{audit_rev}/d{n:04d}", "commit_id": commit, "audit_rev": audit_rev,
             "field_id": cell["field_id"], "rev_base": 0, "rev_new": 1, "at": C.FIXED_CREATED}
        if cell["field_id"] == f"{rec}/labs[1].value" and not pdftext:
            d.update(by="human", choice="route", route="vision", value=cands["vision"]["value"],
                     evidence={"seen": seen, "confirm_sha256": C.sha256(b"synthetic confirm page")})
        elif cell["field_id"] == f"{rec}/labs[3].value" and opts.get("manual"):
            vision_line = ocr["vision"]["lines"][cell["prov"]["vision"]["line_idx"]]
            d.update(by="human", choice="manual", route=None, value=ROWS[3]["value"],
                     evidence={"region": {"der_sha256": der, "bbox_px": vision_line["bbox_px"]}, "seen": seen})
        else:
            d.update(by="auto", choice="route", route=base_route, value=cands[base_route]["value"],
                     evidence={"seen": seen})
        fixes.append(d)

    values_r1 = {d["field_id"]: d["value"] for d in fixes}
    verified_by = "human" if any(d["by"] == "human" for d in fixes) else "auto"
    revs = {1: {"rec_id": rec, "rev": 1, "audit_rev": audit_rev, "commit_id": commit, "type": "lab",
                "date": DATE, "fields": values_r1}}
    candidates = [{"rev": 1, "audit_rev": audit_rev, "status": "verified", "uncertain": [], "created": C.FIXED_CREATED}]
    effective = {"rev": 1, "audit_rev": audit_rev, "status": "verified", "verified_by": verified_by}
    if opts.get("oldbuild"):
        target = f"{rec}/labs[1].value"
        cell = next(c for c in cells_rows if c["field_id"] == target)
        fixes.append({"decision_id": f"{audit_rev}/d{len(fixes) + 1:04d}", "commit_id": f"{audit_rev}/c0002",
                      "audit_rev": audit_rev, "field_id": target, "rev_base": 1, "rev_new": 2, "at": C.FIXED_CREATED,
                      "by": "human", "choice": "route", "route": "mlx", "value": cell["candidates"]["mlx"]["value"],
                      "evidence": {"seen": {e: v["value"] for e, v in cell["candidates"].items()},
                                   "confirm_sha256": C.sha256(b"synthetic confirm page r2")}})
        revs[2] = dict(copy.deepcopy(revs[1]), rev=2, commit_id=f"{audit_rev}/c0002",
                       fields=dict(values_r1, **{target: cell["candidates"]["mlx"]["value"]}))
        candidates[0]["superseded_by"] = 2
        candidates.append({"rev": 2, "audit_rev": audit_rev, "status": "verified", "uncertain": [], "created": C.FIXED_CREATED})
        effective = {"rev": 2, "audit_rev": audit_rev, "status": "verified", "verified_by": "human"}
    by_id = {"rec_id": rec, "doc_id": doc, "page_id": page_id, "record_idx": 0, "type": "lab", "date": DATE,
             "status": "verified", "effective": effective, "candidates": candidates}

    export = []
    for i, r in enumerate(rows):
        item_fid, value_fid, unit_fid, date_fid = (f"{rec}/labs[{i}].item", f"{rec}/labs[{i}].value",
                                                   f"{rec}/labs[{i}].unit", f"{rec}/date")
        transforms = []
        item = values_r1[item_fid]
        if opts.get("alias") and i == 0:
            transforms.append({"kind": "alias", "field_id": item_fid, "from": item, "to": r["item"]})
            item = r["item"]
        date = values_r1[date_fid]
        if opts.get("dateshift"):
            transforms.append({"kind": "dateshift", "field_id": date_fid, "from": date, "to": SHIFTED})
            date = SHIFTED
        parts = [(item_fid, item), (value_fid, values_r1[value_fid]), (unit_fid, values_r1[unit_fid]), (date_fid, date)]
        text, positions = "", {}
        for f, s in parts:
            if text:
                text += " "
            positions[f] = [len(text), len(text) + len(s)]
            text += s
        text += f" ⟨{rec}⟩"
        export.append({"kb_id": KB, "source_file": f"library/items/{r['slug']}.txt", "line_no": 1, "rec_id": rec,
                       "field_ids": [f for f, _ in parts], "rev": 1, "text": text, "positions": positions,
                       "transforms": transforms})

    build_id = "b-20261022T000000Z-" + C.sha256(f"{rec}:1|{KB}".encode())[:8]
    return {
        "images": images,
        "manifest": [{"doc_id": doc, "img_sha256": img_sha, "kind": kind, "path": orig_path}],
        "derived": [{"der_sha256": der, "parent": img_sha, "page_id": page_id, "path": der_path, "transform": transform}],
        "page_map": [{"page_id": page_id, "dpi": DPI, "size_px": [page_img.width, page_img.height]}] if pdftext else None,
        "ocr": ocr, "runs": runs, "cells": cells_rows, "fixes": fixes, "by_id": by_id, "revs": revs,
        "export": export, "batch": batch, "rec": rec,
        "build_receipt": {"schema": "klode-kit.fixture-build-receipt/1", "build_id": build_id, "kb_id": KB,
                          "audit_bindings": {rec: {"rev": 1, "audit_rev": audit_rev}},
                          "deident_mode": "dateshift" if opts.get("dateshift") else "pseudonym"},
        "chain": {"schema": "klode-kit.fixture-chain/1", "synthetic": True, "kb_id": KB, "batch_id": batch,
                  "rec_ids": [rec], "routes": engines, "base_route": base_route, "audit_rev": audit_rev},
    }


def serialize(m: dict) -> dict[str, bytes]:
    files = dict(m["images"])
    files["chain.json"] = C.jdump(m["chain"])
    files["manifest.jsonl"] = C.jlines(m["manifest"])
    files["derived.jsonl"] = C.jlines(m["derived"])
    if m["page_map"]:
        files["page_map.jsonl"] = C.jlines(m["page_map"])
    for o in m["ocr"].values():
        files[o["path"]] = C.jlines(o["lines"])
    a = f"audit/{m['batch']}/a1"
    files[f"{a}/runs.json"] = C.jdump(m["runs"])
    files[f"{a}/cells.jsonl"] = C.jlines(m["cells"])
    files[f"{a}/fixes.jsonl"] = C.jlines(m["fixes"])
    files[f"records/_by_id/{m['rec']}.md"] = C.front_matter(m["by_id"])
    for rev, body in m["revs"].items():
        files[f"records/_rev/{m['rec']}.r{rev}.md"] = C.front_matter(body)
    files["build/build_receipt.json"] = C.jdump(m["build_receipt"])
    files["build/src/export_map.jsonl"] = C.jlines(m["export"])
    for e in m["export"]:
        files[f"build/{e['source_file']}"] = C.text(e["text"] + "\n")
    return files


# --------------------------------------------------------------------------- mutations


def _cell(m, field_suffix):
    return next(c for c in m["cells"] if c["field_id"] == f"{m['rec']}/{field_suffix}")


def _export_for(m, field_suffix):
    fid = f"{m['rec']}/{field_suffix}"
    return next(e for e in m["export"] if fid in e["positions"]), fid


def _set_export_text(m, field_suffix, new):
    e, fid = _export_for(m, field_suffix)
    s, t = e["positions"][fid]
    assert len(new) == t - s
    e["text"] = e["text"][:s] + new + e["text"][t:]


def mutate(n: int, m: dict) -> None:
    rec = m["rec"]
    if n in (1, 12):
        engine = "vision" if n == 1 else "codex"
        cell = _cell(m, "labs[0].value" if n == 1 else "labs[3].value")
        cell["prov"][engine]["span"] = [v + 1 for v in cell["prov"][engine]["span"]]
    elif n == 2:
        m["derived"][0]["parent"] = C.sha256(b"not the original image")
    elif n == 3:
        d = next(d for d in m["fixes"] if d["choice"] == "manual")
        del d["evidence"]["region"]
    elif n == 4:
        d = copy.deepcopy(next(d for d in m["fixes"] if d["field_id"] == f"{rec}/labs[2].value"))
        d["decision_id"] = d["decision_id"][:-4] + f"{len(m['fixes']) + 1:04d}"
        m["fixes"].append(d)
    elif n == 5:
        e, fid = _export_for(m, "labs[0].value")
        e["transforms"].append({"kind": "alias", "field_id": fid, "from": "128", "to": "128"})
    elif n == 6:
        e, _ = _export_for(m, "labs[0].value")
        e["rev"] = 2
    elif n == 7:
        m["fixes"] = [d for d in m["fixes"] if d["field_id"] != f"{rec}/date"]
    elif n == 8:
        fid = f"{rec}/labs[1].value"
        d = next(d for d in m["fixes"] if d["field_id"] == fid and d["by"] == "human")
        d["value"] = "6.8"
        m["revs"][1]["fields"][fid] = "6.8"
        _set_export_text(m, "labs[1].value", "6.8")
    elif n == 9:
        m["revs"][1]["fields"][f"{rec}/labs[0].value"] = "129"
        _set_export_text(m, "labs[0].value", "129")
    elif n == 10:
        _set_export_text(m, "labs[0].value", "182")
    elif n == 11:
        o = m["ocr"]["pdftext"]
        line_idx = _cell(m, "labs[0].value")["prov"]["pdftext"]["line_idx"]
        line = o["lines"][line_idx]
        line["bbox_px"] = [round(v) for v in line["bbox_pt"]]
        for t in line["tokens"]:
            t["bbox_px"] = [round(v) for v in t["bbox_pt"]]
        for c in m["cells"]:
            p = c["prov"]["pdftext"]
            if p["line_idx"] == line_idx:
                p["bbox_px"] = _token_bbox(line, p["span"]) or line["bbox_px"]
    else:
        raise ValueError(n)


def build() -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    models, serialized = {}, {}
    for name, opts in LEGAL.items():
        models[name] = build_model(opts)
        serialized[name] = serialize(models[name])
        meta = {"variant": name, "base": None if name == "clean" else "clean", "options": opts,
                "description": DESCRIPTIONS[name], "expect_violations": [], "mutated_files": []}
        for rel, data in serialized[name].items():
            files[f"fx-chain/{name}/{rel}"] = data
        files[f"fx-chain/{name}/variant.json"] = C.jdump(meta)
    for n, (base, code, desc, extra) in BROKEN.items():
        m = copy.deepcopy(models[base])
        mutate(n, m)
        out = serialize(m)
        base_files = serialized[base]
        mutated = sorted({r for r in out if base_files.get(r) != out[r]} | {r for r in base_files if r not in out})
        name = f"broken-{n}"
        for rel, data in out.items():
            files[f"fx-chain/{name}/{rel}"] = data
        files[f"fx-chain/{name}/variant.json"] = C.jdump({
            "variant": name, "base": base, "description": desc,
            "expect_violations": [dict({"code": code}, **extra)], "mutated_files": mutated})
    return files
