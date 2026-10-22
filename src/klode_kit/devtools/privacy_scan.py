"""Privacy scan for this public repository: ``python -m klode_kit.devtools.privacy_scan``.

Text rules (findings never echo the matched text):
- ``cn_id``: 18-char resident ID (17 digits + digit/X), not adjacent to other alphanumerics;
- ``cn_mobile``: 11-digit mainland mobile (1[3-9] + 9 digits), same boundary rule;
- ``name_label``: ``姓名：<name>`` whose name is not an allowlisted pseudonym (测试患者甲 ...).

Image rule: ``unlisted_image`` for any image/PDF that is neither listed (with matching
sha256) in ``tests/fixtures/MANIFEST.json`` (``{"files": [{"path", "sha256"}]}``, paths
relative to tests/fixtures) nor a ``docs/wua/*.svg``.

Allowlist ``<root>/.privacy-allowlist``: ``<relpath>\\t<kind>\\t<sha256 of the matched text>``
per line, for reviewed false positives (e.g. placeholder names quoted in specs).

Exit 0 when clean, 1 when anything is found.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ID_RE = re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])")
MOBILE_RE = re.compile(r"(?<![0-9A-Za-z])1[3-9]\d{9}(?![0-9A-Za-z])")
NAME_RE = re.compile(r"姓名[：:][ \t]*([^\s，,。；;：:、|<>()（）\[\]{}\"'`*]+)")
NAME_ALLOW = re.compile(r"测试患者[甲乙丙丁戊己庚辛壬癸]")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic", ".heif", ".svg", ".pdf"}
SKIP_DIRS = {
    ".git", ".venv", "venv", "work", "node_modules", "__pycache__", ".pytest_cache",
    ".hypothesis", ".mutmut-cache", "mutants", "htmlcov", "_lab", ".ruff_cache", ".mypy_cache", "_trash",
}
MAX_TEXT_BYTES = 5 * 1024 * 1024


@dataclass
class Finding:
    kind: str
    path: str
    line: int | None = None
    start: int | None = None
    end: int | None = None
    digest: str | None = None


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in rel.parts[:-1]):
            continue
        if path.is_file() and not path.is_symlink():
            yield path


def scan_text(text: str, rel: str) -> list[Finding]:
    found: list[Finding] = []

    def add(kind: str, start: int, end: int) -> None:
        digest = hashlib.sha256(text[start:end].encode("utf-8")).hexdigest()
        found.append(Finding(kind, rel, text.count("\n", 0, start) + 1, start, end, digest))

    for m in ID_RE.finditer(text):
        add("cn_id", m.start(), m.end())
    for m in MOBILE_RE.finditer(text):
        add("cn_mobile", m.start(), m.end())
    for m in NAME_RE.finditer(text):
        if not NAME_ALLOW.fullmatch(m.group(1)):
            add("name_label", m.start(), m.end())
    return sorted(found, key=lambda f: (f.start, f.kind))


def _manifest(root: Path) -> dict[str, str | None]:
    path = root / "tests" / "fixtures" / "MANIFEST.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {f"tests/fixtures/{e['path']}": e.get("sha256") for e in data.get("files", [])}


def _image_ok(path: Path, rel: str, manifest: dict[str, str | None]) -> bool:
    parts = Path(rel).parts
    if path.suffix.lower() == ".svg" and len(parts) == 3 and parts[:2] == ("docs", "wua"):
        return True
    if rel in manifest:
        expected = manifest[rel]
        return expected is not None and hashlib.sha256(path.read_bytes()).hexdigest() == expected
    return False


def _allowlist(root: Path) -> set[tuple[str, str, str]]:
    path = root / ".privacy-allowlist"
    if not path.is_file():
        return set()
    entries = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            rel, kind, digest = line.split("\t")
            entries.add((rel, kind, digest.strip()))
    return entries


def scan(root: Path, paths: list[Path] | None = None) -> list[Finding]:
    root = root.resolve()
    files = [p.resolve() for p in paths] if paths else list(_iter_files(root))
    manifest = _manifest(root)
    allow = _allowlist(root)
    findings: list[Finding] = []
    for path in files:
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)
        if path.suffix.lower() in IMAGE_EXT:
            if not _image_ok(path, rel, manifest):
                findings.append(Finding("unlisted_image", rel))
            if path.suffix.lower() != ".svg":
                continue
        if path.stat().st_size > MAX_TEXT_BYTES:
            continue
        # Binary-looking files (NUL bytes) are scanned too: an ID inside a blob is still a leak.
        raw = path.read_bytes()
        for f in scan_text(raw.decode("utf-8", errors="replace"), rel):
            if (f.path, f.kind, f.digest) not in allow:
                findings.append(f)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit dev privacy-scan")
    parser.add_argument("paths", nargs="*", type=Path, help="files to scan (default: the whole tree)")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    findings = scan(args.root, args.paths or None)
    if args.json:
        print(json.dumps({"count": len(findings), "findings": [asdict(f) for f in findings]}, ensure_ascii=False))
    else:
        for f in findings:
            where = f"{f.path}:{f.line}" if f.line else f.path
            extra = f" sha256={f.digest}" if f.digest else ""
            print(f"{where}: {f.kind}{extra}")
        print(f"privacy_scan: {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
