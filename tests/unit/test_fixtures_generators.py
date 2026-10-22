"""WI-00a: synthetic fixtures (matrix 00a-03 ... 00a-11, plus added consistency cases).

Observed through public surfaces only:
- the committed tree ``tests/fixtures/`` and its ``MANIFEST.json``;
- ``klode_kit.devtools.fixture_manifest.main(argv)`` (MANIFEST verification CLI);
- the injector functions in ``tests/fixtures/gen_engine_outputs.py``;
- ``klode_kit.ocr.degenerate.repeat_ratio`` / ``is_degenerate``;
- ``klode_kit.devtools.privacy_scan.scan``.

Every sensitive-looking sample (a non-allowlisted name, an ID) is assembled at runtime so
this file itself contains no literal PII pattern (it is scanned by privacy_scan too).
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from _support.flows import REPO
from klode_kit.devtools import fixture_manifest, privacy_scan
from klode_kit.ocr.degenerate import is_degenerate, repeat_ratio

pytestmark = pytest.mark.unit

FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(FIXTURES))
import gen_engine_outputs as geo  # noqa: E402  (generator module lives next to the fixtures)

EXCLUDED_NAMES = {"MANIFEST.json"}
EXCLUDED_PARTS = {"__pycache__"}
EXCLUDED_FILES = {".DS_Store"}

# Plan WI-00a scenario list + matrix section 7 additions (fx-publish-ab is built in tmp, never committed).
PLAN_SCENARIOS = [
    "fx-labs-1x", "fx-labs-3x", "fx-dup10", "fx-colshift", "fx-swap", "fx-missingrow",
    "fx-manualfix", "fx-redo", "fx-rx-stamp", "fx-lowres", "fx-vertical", "fx-rotated90",
    "fx-u1-3labs", "fx-u3-mixed", "fx-u8", "fx-text-pdf", "fx-mixed-pdf", "fx-book-aurora",
    "synth_project", "fx-chain",
]
ADDED_SCENARIOS = ["fx-corrupt-pdf", "fx-approx-only", "replay/probe-synth"]
CHAIN_VARIANTS = (
    ["clean"]
    + [f"broken-{i}" for i in range(1, 13)]
    + ["legal-manual", "legal-dateshift", "legal-alias", "legal-oldbuild", "legal-pdftext_dpi"]
)
ROUTES = ("vision", "mlx", "claude", "codex")
SPIKE_SET = ("fx-u1-3labs", "fx-rx-stamp", "fx-lowres", "fx-vertical")  # matrix 00c3-09

NAME_OK = re.compile(r"测试患者[甲乙丙丁戊己庚辛壬癸]")
ID_OK = re.compile(r"T-\d{7}")
NAME_LABEL = re.compile("(?:" + "姓" + "名" + "|患者)[：:][ \t]*([^\\s，,。；;|<>()（）\"]+)")
ID_LIKE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{1,3}-?\d{4,}(?![0-9])")
DRUG_SUFFIX = re.compile(r"片|胶囊|注射液|颗粒|口服液|滴眼液")
TEXT_SUFFIXES = {".json", ".jsonl", ".md", ".txt", ".toml"}


# --------------------------------------------------------------------------- helpers


def rtext(path: Path) -> str:
    assert path.is_file(), f"fixture missing: {path.relative_to(REPO)}"
    return path.read_text(encoding="utf-8")


def rbytes(path: Path) -> bytes:
    assert path.is_file(), f"fixture missing: {path.relative_to(REPO)}"
    return path.read_bytes()


def copy_fixtures(src: Path, dst: Path) -> None:
    assert src.is_dir(), f"fixture dir missing: {src.relative_to(REPO)}"
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__"))


def tree_files(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in EXCLUDED_PARTS for part in rel.parts) or p.name in EXCLUDED_FILES:
            continue
        if p.is_file() and rel.as_posix() not in EXCLUDED_NAMES:
            out.append(rel.as_posix())
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest(root: Path = FIXTURES) -> dict:
    return json.loads(rtext(root / "MANIFEST.json"))


def truth_files() -> list[Path]:
    return sorted(FIXTURES.glob("fx-*/truth.json"))


def load_truth(scenario: str) -> dict:
    return json.loads(rtext(FIXTURES / scenario / "truth.json"))


def engine_line(scenario: str, engine_dir: str, engine: str, key: str, line_idx: int) -> dict:
    path = FIXTURES / scenario / engine_dir / engine / f"{key}.jsonl"
    lines = [json.loads(x) for x in rtext(path).splitlines() if x.strip()]
    hits = [x for x in lines if x["line_idx"] == line_idx]
    assert len(hits) == 1, f"{path}: line_idx {line_idx} occurs {len(hits)} times"
    return hits[0]


def name_problems(root: Path) -> list[str]:
    """Independent extraction of person names / IDs / drug names from all generated text."""
    problems: list[str] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix not in TEXT_SUFFIXES:
            continue
        rel = p.relative_to(root).as_posix()
        text = p.read_text(encoding="utf-8")
        for m in NAME_LABEL.finditer(text):
            if not NAME_OK.fullmatch(m.group(1)):
                problems.append(f"{rel}: name {m.group(1)!r}")
        for m in ID_LIKE.finditer(text):
            if not ID_OK.fullmatch(m.group(0)):
                problems.append(f"{rel}: id {m.group(0)!r}")
        for m in DRUG_SUFFIX.finditer(text):
            if text[max(0, m.start() - 2):m.end()] != "示例片":
                problems.append(f"{rel}: drug {text[max(0, m.start() - 4):m.end()]!r}")
        if p.name == "truth.json":
            data = json.loads(text)
            for rec in data.get("records", []):
                if not NAME_OK.fullmatch(rec.get("patient", "")):
                    problems.append(f"{rel}: patient {rec.get('patient')!r}")
                if not ID_OK.fullmatch(rec.get("patient_id", "")):
                    problems.append(f"{rel}: patient_id {rec.get('patient_id')!r}")
    return problems


def run_manifest_cli(*argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fixture_manifest.main(list(argv))
    return code, buf.getvalue()


def manifest_problems(root: Path) -> tuple[int, list[dict]]:
    code, out = run_manifest_cli("verify", "--root", str(root), "--json")
    return code, json.loads(out)["problems"]


# --------------------------------------------------------------------------- 00a-03 / 00a-04


def test_manifest_complete():
    """00a-03: every file in the tree is in MANIFEST with the right sha, and nothing extra (both ways)."""
    manifest = load_manifest()
    entries = {e["path"]: e["sha256"] for e in manifest["files"]}
    files = tree_files(FIXTURES)
    assert len(files) >= 100, f"fixtures tree looks empty ({len(files)} files)"
    assert sorted(entries) == sorted(files), {
        "not_in_manifest": sorted(set(files) - set(entries)),
        "missing_on_disk": sorted(set(entries) - set(files)),
    }
    wrong = [p for p in files if sha(FIXTURES / p) != entries[p]]
    assert wrong == []
    assert len(manifest["files"]) == len(entries), "duplicate MANIFEST entries"


def test_manifest_verify_cli_clean_tree_exit0(tmp_path):
    """00a-04 (control): an untouched copy verifies clean."""
    copy_root = tmp_path / "fixtures"
    copy_fixtures(FIXTURES, copy_root)
    code, problems = manifest_problems(copy_root)
    assert code == 0 and problems == []


def test_manifest_detects_tampered_file(tmp_path):
    """00a-04: flip one byte in a tmp copy -> the file is named, exit != 0."""
    copy_root = tmp_path / "fixtures"
    copy_fixtures(FIXTURES, copy_root)
    victim = sorted(e["path"] for e in load_manifest(copy_root)["files"] if e["path"].endswith(".png"))[0]
    data = bytearray(rbytes(copy_root / victim))
    data[len(data) // 2] ^= 0x01
    (copy_root / victim).write_bytes(bytes(data))

    code, out = run_manifest_cli("verify", "--root", str(copy_root))
    assert code != 0
    assert victim in out
    code, problems = manifest_problems(copy_root)
    assert code != 0
    assert problems == [{"path": victim, "problem": "sha_mismatch"}]


def test_manifest_detects_unlisted_and_missing_files(tmp_path):
    """00a-03 both directions, through the verification CLI: extra file and vanished file are both named."""
    copy_root = tmp_path / "fixtures"
    copy_fixtures(FIXTURES, copy_root)
    assert (copy_root / "fx-labs-1x").is_dir(), "fx-labs-1x missing"
    (copy_root / "fx-labs-1x" / "stray.txt").write_text("stray\n", encoding="utf-8")
    gone = "fx-labs-1x/truth.json"
    (copy_root / gone).rename(tmp_path / "moved-away.json")
    code, problems = manifest_problems(copy_root)
    assert code != 0
    assert sorted((p["path"], p["problem"]) for p in problems) == [
        ("fx-labs-1x/stray.txt", "not_in_manifest"),
        (gone, "missing_file"),
    ]


# --------------------------------------------------------------------------- 00a-05


def test_names_allowlist():
    """00a-05: names / IDs / drug names only from the allowlist; privacy_scan has 0 findings."""
    files = [FIXTURES / p for p in tree_files(FIXTURES)]
    assert privacy_scan.scan(REPO, files) == []
    assert name_problems(FIXTURES) == []
    # Non-vacuity: the allowlisted names really are present in the truth.
    patients = {rec["patient"] for t in truth_files() for rec in json.loads(t.read_text(encoding="utf-8"))["records"]}
    ids = {rec["patient_id"] for t in truth_files() for rec in json.loads(t.read_text(encoding="utf-8"))["records"]}
    assert "测试患者甲" in patients
    assert "T-0000001" in ids


def test_names_checker_catches_injected_name(tmp_path):
    """00a-05 (S3): the extraction used above is not vacuous - a planted name, ID and drug are all caught."""
    root = tmp_path / "fx"
    copy_fixtures(FIXTURES / "fx-labs-1x", root / "fx-labs-1x")
    bad_name = "张" + "三"
    bad_id = "M" + "-" + "12345678"
    bad_drug = "阿" + "司匹林" + "片"
    note = root / "fx-labs-1x" / "note.md"
    note.write_text(f"{'姓' + '名'}：{bad_name}\n{bad_id}\n{bad_drug}\n", encoding="utf-8")
    problems = name_problems(root)
    assert any(bad_name in p for p in problems)
    assert any(bad_id in p for p in problems)
    assert any("片" in p and "drug" in p for p in problems)


# --------------------------------------------------------------------------- injectors (00a-06 ... 00a-09)

ITEMS = ["血红蛋白", "白细胞", "血小板", "示例指标", "红细胞"]
UNITS = ["g/L", "10^9/L", "mmol/L", "%", ""]
row_st = st.fixed_dictionaries(
    {
        "item": st.sampled_from(ITEMS),
        "value": st.from_regex(r"\d{1,3}(\.\d{1,2})?", fullmatch=True),
        "unit": st.sampled_from(UNITS),
        "flag": st.sampled_from(["", "H", "L"]),
        "ref": st.sampled_from(["115-150", "3.5-9.5", "125-350", ""]),
    }
)
table_st = st.lists(row_st, min_size=2, max_size=12)


@settings(max_examples=200, deadline=None)
@given(rows=table_st, data=st.data())
def test_swap_injector_property(rows, data):
    """00a-06: multiset of values preserved; exactly 2 positions differ (i, i+1); other rows untouched."""
    i = data.draw(st.integers(0, len(rows) - 2))
    assume(rows[i]["value"] != rows[i + 1]["value"])
    before = copy.deepcopy(rows)
    out = geo.inject_swap(rows, i)
    assert rows == before, "input mutated"
    assert len(out) == len(rows)
    assert Counter(r["value"] for r in out) == Counter(r["value"] for r in rows)
    changed = [k for k in range(len(rows)) if out[k]["value"] != rows[k]["value"]]
    assert changed == [i, i + 1]
    assert out[i]["value"] == rows[i + 1]["value"] and out[i + 1]["value"] == rows[i]["value"]
    for k in range(len(rows)):
        if k not in (i, i + 1):
            assert out[k] == rows[k]
        for col in ("item", "unit", "flag", "ref"):
            assert out[k][col] == rows[k][col]


def test_swap_injector_refuses_noop_and_out_of_range():
    rows = [{"item": "a", "value": "10", "unit": "", "flag": "", "ref": ""},
            {"item": "b", "value": "10", "unit": "", "flag": "", "ref": ""}]
    with pytest.raises(ValueError):
        geo.inject_swap(rows, 0)  # equal values: a swap would change nothing
    with pytest.raises(ValueError):
        geo.inject_swap(rows, 1)  # no row i+1


@settings(max_examples=200, deadline=None)
@given(rows=table_st, data=st.data())
def test_colshift_injector_property(rows, data):
    """00a-07: the row's number lands in the unit column, value column empty; other rows untouched."""
    i = data.draw(st.integers(0, len(rows) - 1))
    before = copy.deepcopy(rows)
    out = geo.inject_colshift(rows, i)
    assert rows == before, "input mutated"
    assert out[i]["value"] == ""
    assert out[i]["unit"].startswith(rows[i]["value"])
    assert out[i]["unit"] == rows[i]["value"] + rows[i]["unit"]
    for col in ("item", "flag", "ref"):
        assert out[i][col] == rows[i][col]
    assert [r for k, r in enumerate(out) if k != i] == [r for k, r in enumerate(rows) if k != i]


@settings(max_examples=200, deadline=None)
@given(rows=table_st, data=st.data())
def test_missingrow_injector_property(rows, data):
    """00a-08: target route has one row fewer (exactly row i removed); the other three routes unchanged."""
    routes = {e: copy.deepcopy(rows) for e in ROUTES}
    if data.draw(st.booleans()):
        routes["codex"] = geo.inject_colshift(rows, 0)  # routes need not be identical
    before = copy.deepcopy(routes)
    target = data.draw(st.sampled_from(ROUTES))
    i = data.draw(st.integers(0, len(routes[target]) - 1))
    out = geo.inject_missingrow(routes, target, i)
    assert routes == before, "input mutated"
    assert len(out[target]) == len(routes[target]) - 1
    assert out[target] == routes[target][:i] + routes[target][i + 1:]
    for e in ROUTES:
        if e != target:
            assert out[e] == routes[e]


def test_missingrow_injector_guards():
    rows = [{"item": "a", "value": "1", "unit": "", "flag": "", "ref": ""}]
    routes = {e: copy.deepcopy(rows) for e in ROUTES}
    with pytest.raises(ValueError):
        geo.inject_missingrow(routes, "tesseract", 0)
    with pytest.raises(ValueError):
        geo.inject_missingrow(routes, "codex", 1)


def test_degenerate_injector_ratio_boundary():
    """00a-09: repeat_ratio 0.30 is not degenerate, 0.31 is (strictly greater than 0.3)."""
    at_030 = geo.inject_degenerate([f"line {k}" for k in range(70)], 30)
    at_031 = geo.inject_degenerate([f"line {k}" for k in range(69)], 31)
    assert len(at_030) == 100 and len(at_031) == 100
    assert repeat_ratio(at_030) == 0.30
    assert repeat_ratio(at_031) == 0.31
    assert is_degenerate(repeat_ratio(at_030)) is False
    assert is_degenerate(repeat_ratio(at_031)) is True
    assert is_degenerate(repeat_ratio(at_031), max_ratio=0.31) is False


def test_repeat_ratio_edges():
    assert repeat_ratio([]) == 0.0
    assert repeat_ratio(["", "  "]) == 0.0  # blank lines are not content
    assert repeat_ratio(["a", "b", "c"]) == 0.0
    assert repeat_ratio(["a", "a"]) == 0.5
    with pytest.raises(ValueError):
        geo.inject_degenerate([], 3)
    with pytest.raises(ValueError):
        geo.inject_degenerate(["a"], -1)


@settings(max_examples=200, deadline=None)
@given(n=st.integers(1, 60), repeats=st.integers(0, 200))
def test_degenerate_injector_ratio_property(n, repeats):
    """Added: for distinct input lines, the injected ratio is exactly repeats / (n + repeats)."""
    out = geo.inject_degenerate([f"row {k}" for k in range(n)], repeats)
    assert repeat_ratio(out) == repeats / (n + repeats)


# --------------------------------------------------------------------------- 00a-10 + route/truth consistency


def _computed_label(field: dict) -> str:
    vals = [None if field["routes"].get(e) is None else field["routes"][e]["value"] for e in ROUTES]
    if any(v is None for v in vals):
        return "missing"
    if len(set(vals)) == 1:
        return "agree" if vals[0] == field["truth"] else "agree_wrong"
    counts = Counter(vals).most_common()
    if counts[0][1] == 3:
        return "majority"
    if counts[0][1] == 2 and counts[1][1] == 2:
        return "tie"
    if counts[0][1] == 2:
        return "plurality"
    return "all_differ"


def test_truth_routes_match_engine_outputs():
    """Added: every per-route value claimed by truth.json is literally at its span in that route's JSONL."""
    checked = 0
    for t in truth_files():
        scenario = t.parent.name
        truth = json.loads(t.read_text(encoding="utf-8"))
        for f in truth["fields"]:
            for engine, ref in f["routes"].items():
                if ref is None:
                    continue
                line = engine_line(scenario, truth.get("engine_dir", "engine"), engine, f["key"], ref["line_idx"])
                s, e = ref["span"]
                assert line["text"][s:e] == ref["value"], (scenario, f["field_id"], engine)
                checked += 1
            assert f["designed"] == _computed_label(f), (scenario, f["field_id"])
    assert checked >= 500


def test_truth_designed_ge20_all_agree_fields():
    """00a-10: >= 20 designed four-route-agree fields on the 00c3 spike pages (computed, not just claimed)."""
    agree = 0
    for scenario in SPIKE_SET:
        for f in load_truth(scenario)["fields"]:
            if _computed_label(f) == "agree":
                agree += 1
    assert agree >= 20


def _disputes(truth: dict) -> list[dict]:
    return [f for f in truth["fields"] if _computed_label(f) != "agree"]


def test_scenario_dispute_designs():
    """Added: the per-scenario designs downstream WIs rely on hold in the generated data."""
    u1 = load_truth("fx-u1-3labs")
    assert len(_disputes(u1)) == 5
    assert len({r["key"] for r in u1["records"]}) == 3
    assert {r["date"] for r in u1["records"]} == {"2026-10-22"}
    assert max(Counter(f["key"] for f in _disputes(u1)).values()) == 3  # 08c-18: one record has 3

    u3 = load_truth("fx-u3-mixed")
    by_key = Counter(f["key"] for f in _disputes(u3))
    keys = sorted({r["key"] for r in u3["records"]})
    assert len(keys) == 2 and by_key[keys[0]] == 0 and by_key[keys[1]] >= 1
    assert {_computed_label(f) for f in _disputes(u3)} == {"majority"}

    u8 = load_truth("fx-u8")
    tie = [f for f in u8["fields"] if f["field_id"] == "p002-l07:value"]
    assert len(tie) == 1 and _computed_label(tie[0]) == "tie"

    for f in load_truth("fx-labs-1x")["fields"] + load_truth("fx-labs-3x")["fields"]:
        assert _computed_label(f) == "agree"
    l3 = load_truth("fx-labs-3x")
    hgb = [f["truth"] for f in l3["fields"] if f["col"] == "value" and f["item"] == "血红蛋白"]
    assert len(hgb) == 3 and len(set(hgb)) == 2
    assert len({r["date"] for r in l3["records"]}) == 3

    dup = [f["truth"] for f in load_truth("fx-dup10")["fields"] if f["col"] == "value"]
    assert dup.count("10") == 2

    manual = [f for f in load_truth("fx-manualfix")["fields"] if f["truth"] == "1.05"]
    assert len(manual) == 1
    assert {manual[0]["routes"][e]["value"] for e in ROUTES} == {"10.5"}

    swap = load_truth("fx-swap")
    sv = {f["row"]: f for f in swap["fields"] if f["col"] == "value"}
    assert sv[2]["routes"]["codex"]["value"] == sv[3]["truth"]
    assert sv[3]["routes"]["codex"]["value"] == sv[2]["truth"]

    col = [f for f in load_truth("fx-colshift")["fields"] if f["designed"] != "agree"]
    assert col, "fx-colshift has no shifted cell"

    miss = load_truth("fx-missingrow")
    assert any(i["kind"] == "missingrow" for i in miss["injections"])
    assert any(i["kind"] == "merged_rows" for i in miss["injections"])

    redo = json.loads(rtext(FIXTURES / "fx-redo" / "scenario.json"))
    assert redo["attempts"]["t2"]["missing"] == {"codex": ["p001"]}
    assert not (FIXTURES / "fx-redo" / "engine_t2" / "codex" / "p001.jsonl").exists()
    for e in ROUTES:
        assert (FIXTURES / "fx-redo" / "engine_t3" / e / "p001.jsonl").is_file()


def test_engine_output_contract_shape():
    """Added: simulated route outputs follow the line contract (bbox only on local routes, ints)."""
    for path in sorted(FIXTURES.glob("fx-*/engine*/*/*.jsonl")):
        engine = path.parent.name
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = json.loads(raw)
            assert line["engine"] == engine
            assert re.fullmatch(r"d-[0-9a-f]{12}/p\d{3}", line["page_id"])
            assert re.fullmatch(r"[0-9a-f]{64}", line["der_sha256"])
            if engine in ("claude", "codex"):
                assert line["bbox_px"] is None and line["tokens"] is None
            else:
                assert len(line["bbox_px"]) == 4 and all(isinstance(v, int) for v in line["bbox_px"])
            if engine == "vision" and line["tokens"] is not None:
                for tok in line["tokens"]:
                    s, e = tok["span"]
                    assert line["text"][s:e] == tok["text"]
                    assert tok["geometry"] in ("exact", "approx")


def test_approx_only_row_declared():
    truth = load_truth("fx-approx-only")
    row = truth["approx_row"]
    key = truth["records"][0]["key"]
    lines = [json.loads(x) for x in rtext(FIXTURES / "fx-approx-only" / "engine" / "vision" / f"{key}.jsonl").splitlines()]
    target = [ln for ln in lines if ln["line_idx"] == row["line_idx"]][0]
    assert {t["geometry"] for t in target["tokens"]} == {"approx"}
    assert all(_computed_label(f) == "agree" for f in truth["fields"])


# --------------------------------------------------------------------------- 00a-11 + extra fixture checks


def _readme_sections(text: str) -> dict[str, str]:
    sections, current = {}, None
    for line in text.splitlines():
        m = re.match(r"^##\s+`?([^`\s]+)`?\s*$", line)
        if m:
            current = m.group(1)
            sections[current] = ""
        elif current is not None and not line.startswith("#"):
            sections[current] += line.strip()
    return sections


def test_fixture_scenarios_exist_and_described():
    """00a-11: every planned scenario directory exists and README describes each; README declares all-synthetic."""
    readme = rtext(FIXTURES / "README.md")
    assert "全部合成" in readme
    sections = _readme_sections(readme)
    for name in PLAN_SCENARIOS + ADDED_SCENARIOS:
        assert (FIXTURES / name).is_dir(), name
        assert sections.get(name, "").strip(), f"README has no description for {name}"
    for variant in CHAIN_VARIANTS:
        assert (FIXTURES / "fx-chain" / variant).is_dir(), variant
        assert variant in sections.get("fx-chain", ""), f"README fx-chain section does not mention {variant}"
    assert "STSong-Light" in readme and "Hiragino Sans GB" in readme


def test_chain_variants_declare_single_expected_violation():
    """Added: each fx-chain variant names its base, the files it mutates and the expected violations."""
    expected_codes = {
        1: "span_mismatch", 2: "der_parent_mismatch", 3: "manual_evidence_missing",
        4: "multiple_effective_decisions", 5: "transform_on_numeric", 6: "rev_binding_mismatch",
        7: "missing_effective_decision", 8: "route_value_mismatch", 9: "record_value_mismatch",
        10: "export_text_mismatch", 11: "pdftext_dpi_mismatch", 12: "span_mismatch",
    }
    for variant in CHAIN_VARIANTS:
        meta = json.loads(rtext(FIXTURES / "fx-chain" / variant / "variant.json"))
        if variant.startswith("broken-"):
            n = int(variant.split("-")[1])
            assert [v["code"] for v in meta["expect_violations"]] == [expected_codes[n]], variant
            base = FIXTURES / "fx-chain" / meta["base"]
            here = FIXTURES / "fx-chain" / variant
            differ = sorted(
                p for p in tree_files(here)
                if p != "variant.json" and (not (base / p).is_file() or sha(base / p) != sha(here / p))
            )
            missing = sorted(p for p in tree_files(base) if p != "variant.json" and not (here / p).is_file())
            assert differ + missing, f"{variant} is identical to {meta['base']}"
            assert sorted(differ + missing) == sorted(meta["mutated_files"]), variant
        else:
            assert meta["expect_violations"] == [], variant
    assert json.loads(rtext(FIXTURES / "fx-chain" / "broken-12" / "variant.json"))[
        "expect_violations"][0]["engine"] == "codex"


def test_probe_synth_expected_verdicts():
    """Added: each synthetic probe event stream has a declared classification; only all-denied PASSes."""
    root = FIXTURES / "replay" / "probe-synth"
    expected = json.loads(rtext(root / "expected.json"))
    for name in ("denied", "enoent", "unattempted", "mixed", "codex-denied"):
        assert (root / f"{name}.jsonl").is_file(), name
        assert name in expected
    for name, exp in expected.items():
        events = [json.loads(x) for x in rtext(root / f"{name}.jsonl").splitlines()]
        assert events, name
        assert exp["verdict"] == ("PASS" if set(exp["canaries"].values()) == {"denied"} else "FAIL"), name
    assert expected["denied"]["verdict"] == "PASS"
    assert expected["codex-denied"]["verdict"] == "PASS"
    for name in ("enoent", "unattempted", "mixed"):
        assert expected[name]["verdict"] == "FAIL"


def test_corrupt_pdf_is_truncated_text_pdf():
    good = rbytes(FIXTURES / "fx-text-pdf" / "guide-2026.pdf")
    bad = rbytes(FIXTURES / "fx-corrupt-pdf" / "corrupt.pdf")
    assert good.startswith(b"%PDF-") and b"%%EOF" in good[-64:]
    assert 0 < len(bad) < len(good) and good.startswith(bad)
    assert b"%%EOF" not in bad


def test_synth_project_override():
    toml = rtext(FIXTURES / "synth_project" / "klode-kit.toml")
    assert re.search(r"(?m)^route_retry\s*=\s*5\s*$", toml)
    assert json.loads(rtext(FIXTURES / "synth_project" / ".mcp.json"))["mcpServers"]
    assert (FIXTURES / "synth_project" / "CLAUDE.md").is_file()


def test_fixtures_total_size_under_15mb():
    total = sum((FIXTURES / p).stat().st_size for p in tree_files(FIXTURES))
    assert 0 < total < 15 * 1024 * 1024


def test_scenario_images_unique():
    """Added: no two scenario images share a sha (cloud replay is keyed by image sha)."""
    shas = Counter(sha(FIXTURES / p) for p in tree_files(FIXTURES)
                   if p.endswith(".png") and not p.startswith("fx-chain/"))
    assert shas and max(shas.values()) == 1


def _span_failures(variant: str) -> list[tuple[str, str]]:
    root = FIXTURES / "fx-chain" / variant
    chain = json.loads(rtext(root / "chain.json"))
    lines = {}
    for path in root.glob("ocr/*/*/p001.jsonl"):
        for raw in rtext(path).splitlines():
            ln = json.loads(raw)
            lines[(ln["run_id"], ln["line_idx"])] = ln["text"]
    bad = []
    for raw in rtext(root / "audit" / chain["batch_id"] / "a1" / "cells.jsonl").splitlines():
        cell = json.loads(raw)
        for engine, prov in cell["prov"].items():
            s, e = prov["span"]
            if lines[(prov["run_id"], prov["line_idx"])][s:e] != cell["candidates"][engine]["value"]:
                bad.append((cell["field_id"], engine))
    return bad


def test_chain_segment1_spans():
    """Added: legal chains satisfy segment 1 (span text == candidate) everywhere; broken-1/12 break exactly one."""
    for variant in ("clean", "legal-manual", "legal-dateshift", "legal-alias", "legal-oldbuild", "legal-pdftext_dpi"):
        assert _span_failures(variant) == [], variant
    assert [e for _, e in _span_failures("broken-1")] == ["vision"]
    assert [e for _, e in _span_failures("broken-12")] == ["codex"]


def test_manifest_write_then_verify_roundtrip(tmp_path):
    """Added (S5): ``write`` then ``verify`` is clean for any tree; caches and .DS_Store are ignored."""
    root = tmp_path / "t"
    (root / "a" / "__pycache__").mkdir(parents=True)
    (root / "a" / "x.json").write_text("{}\n", encoding="utf-8")
    (root / "a" / "__pycache__" / "m.pyc").write_bytes(b"\0")
    (root / ".DS_Store").write_bytes(b"\0")
    (root / "b.png").write_bytes(b"\x89PNG")
    code, out = run_manifest_cli("write", "--root", str(root))
    assert code == 0
    listed = [e["path"] for e in load_manifest(root)["files"]]
    assert listed == ["a/x.json", "b.png"]
    assert load_manifest(root)["files"][1]["bytes"] == 4
    assert manifest_problems(root) == (0, [])
    code, out = run_manifest_cli("verify", "--root", str(root))
    assert code == 0 and "0 problem(s)" in out


def test_manifest_verify_without_manifest(tmp_path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    code, problems = manifest_problems(tmp_path)
    assert code == 1 and problems == [{"path": "MANIFEST.json", "problem": "missing_file"}]
