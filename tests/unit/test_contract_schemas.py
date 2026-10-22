"""WI-00b1: contract schemas (test matrix 00b1-01 .. 00b1-09, 00b1-14).

Specification sources: 02 r2.2-A, r2.3, r2.6, r3.1, r3.2, r3.3, r3.4 (R2-08/R2-12), r4.1, r4.2,
r4.3, r4.4 (R3-05/R3-07/R3-08); venv addendum V6.

Public surface used here (nothing else):
- ``klode_kit.contract.schema.NAMES``: the 16 contract names;
- ``klode_kit.contract.schema.issues(name, instance)``: list of issues, each with a ``path`` in
  dotted form (``items[0].choice.kind``; a missing or unexpected property is reported at the
  property itself, not at its parent object);
- ``klode_kit.contract.schema.validate(name, instance)``: raises ``ContractError`` with ``issues``.

Examples live in ``tests/contract/schema_examples/<name>/{valid,invalid}/*.json``. A valid file is
the instance itself; an invalid file is ``{"error_path": <path>, "why": <text>, "instance": {...}}``
and must be rejected with exactly that one path.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from klode_kit.contract import schema as S

REPO = Path(__file__).resolve().parents[2]
EXAMPLES = REPO / "tests" / "contract" / "schema_examples"
FIXTURES = REPO / "tests" / "fixtures"

EXPECTED_NAMES = {
    "ocr_line", "manifest", "derived", "cells", "fixes", "record", "choices", "export_map",
    "receipt", "build_receipt", "check_receipt", "publish_log", "kb_version", "isolation_receipt",
    "writer_receipt", "lane_receipt",
}
BUILD_STEPS = [
    "export", "init_kb", "ingest", "split_manifest", "klode_build", "dispatch_writers", "gate1",
    "write_card", "check_strict", "desk_apply", "desk_verify", "gen_agent", "self_check",
]


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _example_dirs() -> list[Path]:
    return sorted(p for p in EXAMPLES.iterdir() if p.is_dir()) if EXAMPLES.is_dir() else []


def _examples(kind: str) -> list[tuple[str, Path]]:
    return [(d.name, f) for d in _example_dirs() for f in sorted((d / kind).glob("*.json"))]


def _paths(name: str, instance) -> list[str]:
    return sorted(i.path for i in S.issues(name, instance))


def _valid(name: str) -> dict:
    files = sorted((EXAMPLES / name / "valid").glob("*.json"))
    assert files, f"no valid example for {name}"
    return copy.deepcopy(_load(files[0]))


# --------------------------------------------------------------------------- inventory


def test_contract_names_are_the_sixteen_of_the_plan():
    assert set(S.NAMES) == EXPECTED_NAMES
    assert len(S.NAMES) == 16


def test_every_schema_has_valid_and_invalid_examples():
    """Plan WI-00b1: at least one legal and one illegal example per schema."""
    dirs = {d.name for d in _example_dirs()}
    assert dirs == EXPECTED_NAMES
    for name in EXPECTED_NAMES:
        assert list((EXAMPLES / name / "valid").glob("*.json")), f"{name}: no valid example"
        assert list((EXAMPLES / name / "invalid").glob("*.json")), f"{name}: no invalid example"


def test_schema_files_are_draft_2020_12_and_packaged():
    """The schemas ship inside the package (src/klode_kit/schemas) so an installed plugin has them."""
    import jsonschema

    pkg = REPO / "src" / "klode_kit" / "schemas"
    assert {p.stem for p in pkg.glob("*.json")} >= EXPECTED_NAMES
    for name in EXPECTED_NAMES:
        doc = S.load(name)
        assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        jsonschema.Draft202012Validator.check_schema(doc)


def test_unknown_schema_name_is_refused():
    with pytest.raises(S.UnknownSchema):
        S.issues("no_such_contract", {})


# --------------------------------------------------------------------------- 00b1-01 / 00b1-02


@pytest.mark.parametrize(("name", "path"), _examples("valid"), ids=lambda v: v.name if isinstance(v, Path) else v)
def test_schema_examples_valid(name, path):
    """00b1-01: every legal example passes."""
    instance = _load(path)
    assert S.issues(name, instance) == []
    S.validate(name, instance)  # does not raise


@pytest.mark.parametrize(("name", "path"), _examples("invalid"), ids=lambda v: v.name if isinstance(v, Path) else v)
def test_schema_examples_invalid(name, path):
    """00b1-02: every illegal example is rejected, and the error path is exactly the declared field."""
    case = _load(path)
    assert set(case) == {"error_path", "why", "instance"}, "invalid example must declare error_path/why/instance"
    assert _paths(name, case["instance"]) == [case["error_path"]]
    with pytest.raises(S.ContractError) as exc:
        S.validate(name, case["instance"])
    assert [i.path for i in exc.value.issues] == [case["error_path"]]


# --------------------------------------------------------------------------- 00b1-03


def test_choices_partial_rejected():
    """00b1-03 (r3.3): choice.kind has no `partial` any more."""
    doc = _valid("choices")
    assert _paths("choices", doc) == []
    doc["items"][0]["choice"] = {"kind": "partial"}
    assert _paths("choices", doc) == ["items[0].choice.kind"]


@pytest.mark.parametrize("kind", ["route", "manual", "none"])
def test_choices_kinds_still_accepted(kind):
    doc = _valid("choices")
    doc["items"][0]["choice"] = {"route": {"kind": "route", "route": "vision"},
                                 "manual": {"kind": "manual", "value": "1.05"},
                                 "none": {"kind": "none"}}[kind]
    assert _paths("choices", doc) == []


def test_choices_manual_empty_value_rejected():
    """r2.3: `manual` value must not be the empty string (empty = none = stays disputed)."""
    doc = _valid("choices")
    doc["items"][0]["choice"] = {"kind": "manual", "value": ""}
    assert _paths("choices", doc) == ["items[0].choice.value"]


# --------------------------------------------------------------------------- 00b1-04


def _build_receipt(steps: list[str]) -> dict:
    doc = _valid("build_receipt")
    template = doc["steps"][0]
    doc["steps"] = [dict(template, step=s) for s in steps]
    return doc


def test_build_steps_order_exactly_13():
    """00b1-04 (K8): only the 13 steps in their fixed order pass."""
    assert _paths("build_receipt", _build_receipt(BUILD_STEPS)) == []

    twelve = _build_receipt(BUILD_STEPS[:12])
    assert _paths("build_receipt", twelve) == ["steps"]

    fourteen = _build_receipt(BUILD_STEPS + ["self_check"])
    assert _paths("build_receipt", fourteen) == ["steps"]

    swapped = list(BUILD_STEPS)
    swapped[4], swapped[5] = swapped[5], swapped[4]
    assert _paths("build_receipt", _build_receipt(swapped)) == ["steps[4].step", "steps[5].step"]


@settings(max_examples=60, deadline=None)
@given(st.permutations(BUILD_STEPS))
def test_build_steps_any_permutation_but_identity_rejected(order):
    """Strengthens 00b1-04 over the whole permutation domain: accepted <=> identity order."""
    accepted = _paths("build_receipt", _build_receipt(list(order))) == []
    assert accepted == (list(order) == BUILD_STEPS)


# --------------------------------------------------------------------------- 00b1-05


@pytest.mark.parametrize(("geometry", "ok"), [("exact", True), ("approx", True), ("", False), (None, False), ("EXACT", False)])
def test_geometry_enum(geometry, ok):
    """00b1-05 (R3-05): tokens[].geometry is exact | approx, nothing else."""
    line = _valid("ocr_line")
    assert line["tokens"], "the valid ocr_line example must carry tokens"
    line["tokens"][0]["geometry"] = geometry
    assert _paths("ocr_line", line) == ([] if ok else ["tokens[0].geometry"])


def test_geometry_required_on_every_token():
    line = _valid("ocr_line")
    del line["tokens"][0]["geometry"]
    assert _paths("ocr_line", line) == ["tokens[0].geometry"]


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_cloud_routes_have_no_geometry(engine):
    """r3.1 / R2-08: cloud routes carry bbox_px null and tokens null."""
    line = _valid("ocr_line")
    line["engine"] = engine
    assert sorted(_paths("ocr_line", line)) == ["bbox_px", "tokens"]
    line["bbox_px"], line["tokens"] = None, None
    assert _paths("ocr_line", line) == []


# --------------------------------------------------------------------------- 00b1-06

RECORD_STATUSES = ["extracted", "pending_confirm", "verified", "disputed", "none"]
CANDIDATE_STATUSES = ["extracted", "pending_confirm", "verified", "disputed"]
EFFECTIVE_STATUSES = ["verified", "disputed", "none"]


@st.composite
def _records(draw):
    doc = _valid("record")
    audit = doc["candidates"][0]["audit_rev"]
    n = draw(st.integers(min_value=1, max_value=5))
    cands = []
    for rev in range(1, n + 1):
        status = draw(st.sampled_from(CANDIDATE_STATUSES))
        cands.append({"rev": rev, "audit_rev": audit, "status": status, "uncertain": [], "created": "2026-10-22T00:00:00Z"})
    doc["candidates"] = cands
    if draw(st.booleans()):
        status = draw(st.sampled_from(EFFECTIVE_STATUSES))
        doc["effective"] = {"rev": draw(st.integers(1, n)), "audit_rev": audit, "status": status,
                            "verified_by": "human" if status == "verified" else None}
        expected = status
    else:
        doc.pop("effective", None)
        expected = cands[-1]["status"]
    doc["status"] = draw(st.one_of(st.just(expected), st.sampled_from(RECORD_STATUSES)))
    return doc, expected


@settings(max_examples=200, deadline=None)
@given(_records())
def test_record_status_equals_effective_or_latest_candidate(case):
    """00b1-06 (R4-01): accepted <=> status == effective.status, or candidates[-1].status without effective."""
    doc, expected = case
    paths = _paths("record", doc)
    if doc["status"] == expected:
        assert paths == []
    else:
        assert paths == ["status"]


def test_record_with_no_candidates_rejected():
    doc = _valid("record")
    doc["candidates"] = []
    assert "candidates" in _paths("record", doc)


# --------------------------------------------------------------------------- 00b1-07


def test_export_map_value_audited_and_transforms_required():
    """00b1-07 (R3-07, r3.1 segment 3): value_raw is not the field name; transforms[] is required."""
    row = _valid("export_map")
    assert _paths("export_map", row) == []

    renamed = copy.deepcopy(row)
    renamed["value_raw"] = renamed.pop("value_audited")
    assert _paths("export_map", renamed) == ["value_audited", "value_raw"]

    no_transforms = copy.deepcopy(row)
    del no_transforms["transforms"]
    assert _paths("export_map", no_transforms) == ["transforms"]


def test_export_map_transform_kinds():
    row = _valid("export_map")
    row["transforms"] = [{"kind": "rename", "field_id": row["field_ids"][0], "from": "a", "to": "b"}]
    assert _paths("export_map", row) == ["transforms[0].kind"]


# --------------------------------------------------------------------------- 00b1-08


@pytest.mark.parametrize("field", ["cli_version", "effective_config_sha", "argv_sha"])
def test_isolation_receipt_required_bindings(field):
    """00b1-08 (r4.3): each binding is required on its own."""
    doc = _valid("isolation_receipt")
    assert _paths("isolation_receipt", doc) == []
    del doc[field]
    assert _paths("isolation_receipt", doc) == [field]


# --------------------------------------------------------------------------- 00b1-09


def test_lane_receipt_release_sha_required():
    """00b1-09 (R3-08)."""
    doc = _valid("lane_receipt")
    assert _paths("lane_receipt", doc) == []
    del doc["env_fingerprint"]["release_sha"]
    assert _paths("lane_receipt", doc) == ["env_fingerprint.release_sha"]


@pytest.mark.parametrize("field", ["repo_root", "python"])
def test_lane_receipt_records_repo_root_and_venv_python(field):
    """Addendum V6: the fingerprint records the repo-root realpath and the venv python realpath."""
    doc = _valid("lane_receipt")
    del doc["env_fingerprint"][field]
    assert _paths("lane_receipt", doc) == [f"env_fingerprint.{field}"]
    doc = _valid("lane_receipt")
    doc["env_fingerprint"][field] = "relative/path"
    assert _paths("lane_receipt", doc) == [f"env_fingerprint.{field}"]


def test_lane_receipt_written_by_run_lane_conforms(tmp_path):
    """The lane runner's own fingerprint (WI-00s) satisfies the full contract."""
    from klode_kit.devtools import run_lane

    doc = _valid("lane_receipt")
    doc["env_fingerprint"] = run_lane.env_fingerprint(REPO)
    assert _paths("lane_receipt", doc) == []


# --------------------------------------------------------------------------- 00b1-14


def test_choices_duplicate_field_id_schema_rejected():
    """00b1-14 (F17): a repeated field_id is rejected at the second occurrence."""
    doc = _valid("choices")
    first = doc["items"][0]
    other = copy.deepcopy(first)
    other["field_id"] = first["rec_id"] + "/labs[9].value"
    dup = copy.deepcopy(first)
    dup["choice"] = {"kind": "manual", "value": "7.7"}
    doc["items"] = [first, other, dup]
    assert _paths("choices", doc) == ["items[2].field_id"]
    doc["items"] = [first, other]
    assert _paths("choices", doc) == []


# --------------------------------------------------------------------------- fixtures conform (added)

CHAIN_FILES = {
    "manifest.jsonl": "manifest",
    "derived.jsonl": "derived",
}


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return json.loads(text[4:].split("\n---\n", 1)[0])


LEGAL_CHAIN = ["clean", "legal-manual", "legal-dateshift", "legal-alias", "legal-oldbuild", "legal-pdftext_dpi"]


@pytest.mark.parametrize("variant", LEGAL_CHAIN)
def test_fixture_chain_artifacts_conform(variant):
    """Added: the WI-00a fx-chain products (provisional formats) satisfy the formal contracts."""
    root = FIXTURES / "fx-chain" / variant
    checked: dict[str, int] = {}

    def check(name, rows, where):
        for n, row in enumerate(rows):
            assert _paths(name, row) == [], f"{where}#{n}"
        checked[name] = checked.get(name, 0) + len(rows)

    for rel, name in CHAIN_FILES.items():
        check(name, _jsonl(root / rel), rel)
    for p in sorted((root / "ocr").rglob("*.jsonl")):
        check("ocr_line", _jsonl(p), str(p.relative_to(root)))
    for p in sorted((root / "audit").rglob("cells.jsonl")):
        check("cells", _jsonl(p), str(p.relative_to(root)))
    for p in sorted((root / "audit").rglob("fixes.jsonl")):
        check("fixes", _jsonl(p), str(p.relative_to(root)))
    for p in sorted((root / "records" / "_by_id").glob("*.md")):
        check("record", [_front_matter(p)], str(p.relative_to(root)))
    check("export_map", _jsonl(root / "build" / "src" / "export_map.jsonl"), "export_map.jsonl")
    assert set(checked) == {"manifest", "derived", "ocr_line", "cells", "fixes", "record", "export_map"}
    assert all(checked.values())


def test_fixture_engine_outputs_conform():
    """Added: every simulated route line under tests/fixtures/*/engine satisfies ocr_line."""
    files = sorted(FIXTURES.glob("*/engine/*/*.jsonl"))
    assert files
    for p in files:
        for n, row in enumerate(_jsonl(p)):
            assert _paths("ocr_line", row) == [], f"{p.relative_to(FIXTURES)}#{n}"


# --------------------------------------------------------------------------- custom keywords: wrong shapes (added)


def test_custom_keywords_tolerate_wrong_shapes():
    """x-uniqueBy / x-mirror never crash on malformed input; the type/required issue is what is reported."""
    doc = _valid("choices")
    doc["items"] = "not a list"
    assert _paths("choices", doc) == ["items"]

    doc = _valid("choices")
    del doc["items"][0]["field_id"]
    assert _paths("choices", doc) == ["items[0].field_id"]

    rec = _valid("record")
    del rec["status"]
    assert _paths("record", rec) == ["status"]

    rec = _valid("record")
    del rec["effective"]
    rec["candidates"] = "not a list"
    assert _paths("record", rec) == ["candidates"]

    assert _paths("record", ["not", "an", "object"]) == [""]


def test_contract_error_message_names_every_path():
    doc = _valid("isolation_receipt")
    del doc["argv_sha"]
    doc["route"] = "gemini"
    with pytest.raises(S.ContractError) as exc:
        S.validate("isolation_receipt", doc)
    assert [i.path for i in exc.value.issues] == ["argv_sha", "route"]
    assert "argv_sha" in str(exc.value) and "route" in str(exc.value)
