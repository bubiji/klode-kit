"""WI-00s: privacy_scan (matrix 00s-08, 00s-09, 00s-10, 00s-11).

Observed through the CLI surface ``klode_kit.devtools.privacy_scan.main(argv)`` with
``--json`` output (and once as a real subprocess). Every sensitive-looking sample is
assembled at runtime so this file itself contains no literal ID / phone / name label.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from _support.flows import base_env
from klode_kit.devtools import privacy_scan

pytestmark = pytest.mark.unit

LABEL = "姓" + "名" + "："
ID18 = "1101051990" + "03071234"          # 18 digits
ID18X = "1101051990" + "0307123X"         # 17 digits + X
ID17 = "1101051990" + "0307123"           # 17 digits
ID17X = "1101051990" + "030712X"          # 16 digits + X (17 chars)
ID19 = "1101051990" + "030712345"         # 19 digits
ID19X = "1101051990" + "03071234X"        # 18 digits + X (19 chars)
MOB11 = "138" + "12345678"
MOB10 = "138" + "1234567"
MOB12 = "138" + "123456789"


def scan(root: Path, *paths: Path) -> tuple[int, dict]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = privacy_scan.main(["--root", str(root), "--json", *[str(p) for p in paths]])
    return code, json.loads(buf.getvalue())


def kinds_for_text(text: str) -> list[str]:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        f = root / "sample.txt"
        f.write_text(text, encoding="utf-8")
        _, report = scan(root, f)
    return sorted(x["kind"] for x in report["findings"])


@pytest.mark.parametrize(
    ("sample", "expected"),
    [
        (ID18, ["cn_id"]),
        (ID18X, ["cn_id"]),
        (ID18X.lower(), ["cn_id"]),
        (ID17, []),
        (ID17X, []),
        (ID19, []),
        (ID19X, []),
        (MOB11, ["cn_mobile"]),
        (MOB10, []),
        (MOB12, []),
        (LABEL + "测试患者甲", []),
    ],
    ids=["id18", "id18X", "id18x", "id17", "id17X", "id19", "id19X", "mob11", "mob10", "mob12", "allowlisted"],
)
def test_privacy_scan_boundaries(sample, expected):
    """00s-08: only the 18-char ID and the 11-digit mobile hit; 17/19, 10/12 and allowlisted names do not."""
    assert kinds_for_text(f"编号 {sample} 结束\n") == expected


def test_privacy_scan_findings_do_not_echo_the_secret(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text(f"号码 {MOB11}\n", encoding="utf-8")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        privacy_scan.main(["--root", str(tmp_path), "--json", str(f)])
    assert MOB11 not in buf.getvalue()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        privacy_scan.main(["--root", str(tmp_path), str(f)])
    assert MOB11 not in buf.getvalue()


def test_privacy_scan_name_label_nonwhitelist(tmp_path):
    """00s-09: a non-allowlisted name after the label hits; the allowlisted pseudonym does not; exit != 0."""
    bad = tmp_path / "bad.txt"
    good = tmp_path / "good.txt"
    bad.write_text(LABEL + "张" + "三" + "\n", encoding="utf-8")
    good.write_text(LABEL + "测试患者甲\n", encoding="utf-8")

    code_bad, report_bad = scan(tmp_path, bad)
    code_good, report_good = scan(tmp_path, good)

    assert code_bad != 0
    assert [(x["kind"], x["path"]) for x in report_bad["findings"]] == [("name_label", "bad.txt")]
    assert code_good == 0
    assert report_good["findings"] == []


def test_privacy_scan_unlisted_image(tmp_path):
    """00s-10: an image not in the fixtures MANIFEST is reported; docs/wua/*.svg is not; exit != 0."""
    (tmp_path / "docs" / "wua").mkdir(parents=True)
    (tmp_path / "docs" / "wua" / "x.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    (tmp_path / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    code, report = scan(tmp_path)

    assert code != 0
    listed = [(x["kind"], x["path"]) for x in report["findings"]]
    assert ("unlisted_image", "scan.png") in listed
    assert all(not p.endswith(".svg") for _, p in listed)


def test_privacy_scan_manifest_listed_image_passes_only_with_matching_sha(tmp_path):
    fixtures = tmp_path / "tests" / "fixtures"
    (fixtures / "fx").mkdir(parents=True)
    good = fixtures / "fx" / "ok.png"
    swapped = fixtures / "fx" / "swapped.png"
    good.write_bytes(b"\x89PNG-good")
    swapped.write_bytes(b"\x89PNG-new-bytes")
    manifest = {
        "files": [
            {"path": "fx/ok.png", "sha256": hashlib.sha256(b"\x89PNG-good").hexdigest()},
            {"path": "fx/swapped.png", "sha256": hashlib.sha256(b"\x89PNG-old-bytes").hexdigest()},
        ]
    }
    (fixtures / "MANIFEST.json").write_text(json.dumps(manifest))

    code, report = scan(tmp_path)

    listed = [x["path"] for x in report["findings"] if x["kind"] == "unlisted_image"]
    assert listed == ["tests/fixtures/fx/swapped.png"]
    assert code != 0


def test_privacy_scan_clean_tree_exit_zero(tmp_path):
    (tmp_path / "README.md").write_text("合成数据，测试患者甲，T-0000001\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "leak.txt").write_text(MOB11)  # excluded directory
    code, report = scan(tmp_path)
    assert (code, report["findings"]) == (0, [])


def test_privacy_scan_allowlist_suppresses_exact_match_only(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(f"{LABEL}张三\n{MOB11}\n", encoding="utf-8")
    digest = hashlib.sha256((LABEL + "张三").encode("utf-8")).hexdigest()
    (tmp_path / ".privacy-allowlist").write_text(f"doc.md\tname_label\t{digest}\n")

    code, report = scan(tmp_path)

    assert [x["kind"] for x in report["findings"]] == ["cn_mobile"]
    assert code != 0


def test_privacy_scan_cli_subprocess(tmp_path):
    (tmp_path / "a.txt").write_text(f"x {ID18} y\n")
    res = subprocess.run(
        [sys.executable, "-m", "klode_kit.devtools.privacy_scan", "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        env=base_env(),
        timeout=60,
    )
    assert res.returncode != 0
    assert "a.txt" in res.stdout
    assert ID18 not in res.stdout


def _spans_containing(text: str, index: int) -> int:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        f = root / "s.txt"
        f.write_text(text, encoding="utf-8")
        _, report = scan(root, f)
    return sum(1 for x in report["findings"] if x["start"] < index < x["end"])


text_with_tokens = st.lists(
    st.one_of(
        st.text(max_size=12),
        st.sampled_from([ID18, MOB11, LABEL + "张三", LABEL + "测试患者甲", "12345", "x", " ", "\n"]),
    ),
    max_size=6,
).map("".join)


@settings(max_examples=60, deadline=None)
@given(text=text_with_tokens, data=st.data())
@example(text=f"a {MOB11} b", data=None)
def test_privacy_scan_detection_monotone(text, data):
    """00s-11 (S6, metamorphic): inserting one delimited synthetic ID adds at least one hit.

    Deviation recorded in the WI report: the literal 'any position' form is false for any
    token-boundary detector, because inserting inside an existing hit splits it (e.g. in the
    middle of a phone number). The relation is therefore stated with that correction term:
    after >= before + 1 - (hits whose span strictly contains the insertion index).
    """
    if data is None:
        index = text.index(MOB11) + 4
    else:
        index = data.draw(st.integers(min_value=0, max_value=len(text)))
    inserted = text[:index] + "\n" + ID18 + "\n" + text[index:]

    before = len(kinds_for_text(text))
    after = len(kinds_for_text(inserted))
    split = _spans_containing(text, index)

    assert after >= before + 1 - split
    if split == 0:
        assert after >= before + 1


def test_privacy_scan_allowlist_ignores_comments_and_blank_lines(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text(f"{LABEL}张三\n", encoding="utf-8")
    digest = hashlib.sha256((LABEL + "张三").encode("utf-8")).hexdigest()
    (tmp_path / ".privacy-allowlist").write_text(f"# reviewed\n\ndoc.md\tname_label\t{digest}\n")
    assert scan(tmp_path) == (0, {"count": 0, "findings": []})


def test_privacy_scan_explicit_path_outside_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text(f"{MOB11}\n")
    code, report = scan(root, outside)
    assert code != 0
    assert [(x["kind"], x["path"]) for x in report["findings"]] == [("cn_mobile", str(outside.resolve()))]


def test_privacy_scan_svg_text_is_scanned(tmp_path):
    (tmp_path / "docs" / "wua").mkdir(parents=True)
    (tmp_path / "docs" / "wua" / "flow.svg").write_text(f"<svg><text>{MOB11}</text></svg>")
    code, report = scan(tmp_path)
    assert code != 0
    assert [(x["kind"], x["path"]) for x in report["findings"]] == [("cn_mobile", "docs/wua/flow.svg")]
