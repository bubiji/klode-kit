"""Shared fixtures for WI-02c (the desk): a synthetic aurora library and a scripted ``klode`` stand-in.

The ``klode`` CLI is a boundary (plan §0.4). In the unit lane it is replaced by a script written into the test's
own tmp directory and put first on PATH (never into ``tests/fakes/bin``, which is on PATH for every lane and would
shadow the real klode of the cli_integration lane). Its answers come from a scenario file the test writes:

    {"diagnose": {question: [dimension, ...]},     # most relevant first; [] or missing = no symptom matched
     "core": {dimension: core question},           # second element of each JSON pair / prose line
     "strict_stdout": str, "strict_rc": int,        # `klode check --strict`
     "search": {question: [card id, ...]},         # `klode --json search --full`
     "raw_diagnose_stdout": str | absent}          # replaces the diagnose stdout verbatim (broken klode)

Output shapes are copied from the real klode 0.5.0 (recorded 2026-10-24 in a scratch library):
``--json diagnose`` prints ``{"op_id": "diagnose", "provenance": {...}, "value": [[dim, core_q], ...],
"capability": "stable"}`` and exits 1 when nothing matched; prose diagnose prints ``  • <dim>`` followed by the
dimension's core question and ``→ klode consult <dim>``. The default core question carries hyphenated English
words, which is exactly what the original prose regex picked up instead of a dimension without a hyphen (03 B3).
Paired real-path tests: ``tests/cli/test_desk_cli.py`` (02c-01).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from _support.flows import REPO

SOURCE = (REPO / "tests" / "fixtures" / "fx-book-aurora" / "aurora-kettle.txt").read_text(encoding="utf-8")
AURORA_SRC = REPO / "tests" / "fixtures" / "fx-book-aurora" / "src"
CH = {
    "ch01": SOURCE[SOURCE.index("Chapter 1:"):SOURCE.index("Chapter 2:")],
    "ch02": SOURCE[SOURCE.index("Chapter 2:"):SOURCE.index("Chapter 3:")],
    "ch03": SOURCE[SOURCE.index("Chapter 3:"):],
}
ANCHORS = {
    "ch01": ["copper shell that is hammered thin and then annealed twice",
             "Annealing relaxes the metal so that the shell can expand",
             "A brass rim is folded over the upper edge of the shell",
             "A domed base flexes outward when heated",
             "Makers tin the inside of the shell with a thin food-safe layer"],
    "ch02": ["opens only when the steam pressure passes a small threshold",
             "a reed of spring steel vibrates against two slots",
             "the reed is usually coated with scale and needs a vinegar soak",
             "A lever on the handle lifts the valve cap",
             "Spare reeds are cut from the same strip as the original"],
    "ch03": ["kept on a low ember bed and topped up from a covered jug",
             "Vendors wipe the shell with a dry wool cloth after each refill",
             "restored with a paste of salt and lemon juice",
             "The handle is wrapped in waxed cord",
             "dried upside down, and packed with crumpled paper"],
}
ALIASES = {
    "ch01": ["copper shell", "kettle shell", "annealing", "brass rim", "domed base", "tinning", "retinning",
             "food-safe layer", "hammered copper", "kettle body"],
    "ch02": ["whistle valve", "reed", "spring steel", "valve cap", "two-tone note", "vinegar soak", "spare reed",
             "pitch", "steam pressure", "spout"],
    "ch03": ["night market", "ember bed", "wool cloth", "salt and lemon", "waxed cord", "winter storage",
             "crumpled paper", "covered jug", "darkened shell", "spring boil"],
}
TOML = """# library.toml — a klode knowledge library (synthetic test copy of `klode init` output).

[library]
dir = "library"
cards = "cards"
shelves = ["book"]

[bibliography]
enabled = true
path = "BIBLIOGRAPHY.md"

[frameworks]              # optional per-dimension distillation layer
enabled = false
dir = "frameworks"
syntheses = "_syntheses"

[copyright]
guard = true
extra_guard_dirs = []
"""
CARD = """---
id: {cid}
shelf: book
file: library/book/{cid}.txt
framework: none
zoom: full
aliases: [{aliases}]
grep_ready: true
source_sha256: 0000000000000000000000000000000000000000000000000000000000000000
---

# {cid}

## Content
`library/book/{cid}.txt` — full text (git-ignored, grep-ready). Never duplicated here; grep it to verify.

<!-- scaffold: managed by klode — edit Thin/Full below the marker by hand -->

## Thin
Summary (grep: `{a0}`).

## Full
### Points
{full}"""
DEFAULT_CORE = "what about the long-standing multi-word issue in this well-known area?"


def card_text(cid: str, anchors: list[str] | None = None, aliases: list[str] | None = None) -> str:
    anchors = ANCHORS[cid] if anchors is None else anchors
    aliases = ALIASES[cid] if aliases is None else aliases
    full = "".join(f"- point {k} (grep: `{a}`)\n" for k, a in enumerate(anchors))
    return CARD.format(cid=cid, aliases=", ".join(aliases), a0=anchors[0], full=full)


def synthesis(dim: str, cards: list[str], anchors: list[str], *, actions: list[str] | None = None,
              title: str | None = None, craft_extra: str = "") -> str:
    """A synthesis that check_synthesis passes: front-matter, Craft with the three sections, Provenance."""
    actions = actions if actions is not None else [f"- **A{k + 1} step-{k + 1} 中文一句话。** (grep: `{a}`)"
                                                   for k, a in enumerate(anchors[:2])]
    mech = "".join(f"- 机制说明 {k} (grep: `{a}`)\n" for k, a in enumerate(anchors))
    return (f"---\ndimension: {dim}\ntitle: {title or dim}\ncards: [{', '.join(cards)}]\nstatus: draft\ntier: 1\n---\n\n"
            f"**Core question:** {dim} 怎么做。\n\n## Craft\n\n### 机制（为什么这样做有效）\n{mech}{craft_extra}\n"
            f"### 动作（按顺序做什么）\n" + "\n".join(actions) + "\n\n"
            f"### 可选项（几条路怎么选）\n- 情境 → 选法 (grep: `{anchors[0]}`)\n\n"
            f"## Operational spec\n（留空）\n\n## Provenance\n- 来源卡 {', '.join(cards)}。\n")


def aurora_spec() -> dict:
    return json.loads((AURORA_SRC / "desk.json").read_text(encoding="utf-8"))


def aurora_holdout() -> list[dict]:
    return [json.loads(line) for line in (AURORA_SRC / "holdout.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]


def make_kb(root: Path, spec: dict | None = None, holdout: list[dict] | None = None, *,
            syntheses: bool = True, applied: bool = False) -> Path:
    """A library at ``root``: library.toml, three aurora cards, ``src/desk.json``, ``src/holdout.jsonl`` and (by
    default) one passing synthesis per dimension. ``applied`` also runs nothing - it only enables frameworks."""
    spec = aurora_spec() if spec is None else spec
    holdout = aurora_holdout() if holdout is None else holdout
    root.mkdir(parents=True, exist_ok=True)
    (root / "library.toml").write_text(TOML.replace("enabled = false", "enabled = true") if applied else TOML,
                                       encoding="utf-8")
    cards = root / "library" / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    (root / "library" / "book").mkdir(parents=True, exist_ok=True)
    for cid in CH:
        (cards / f"{cid}.md").write_text(card_text(cid), encoding="utf-8")
        (root / "library" / "book" / f"{cid}.txt").write_text(CH[cid], encoding="utf-8")
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "desk.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
    write_holdout(root, holdout)
    if syntheses:
        syn = root / "library" / "frameworks" / "_syntheses"
        syn.mkdir(parents=True, exist_ok=True)
        for d in spec["dimensions"]:
            anchors = [a for c in d["cards"] for a in ANCHORS.get(c, [])][:4]
            (syn / f"{d['dimension']}.md").write_text(synthesis(d["dimension"], d["cards"], anchors),
                                                      encoding="utf-8")
    return root


def write_holdout(root: Path, holdout: list[dict]) -> Path:
    p = root / "src" / "holdout.jsonl"
    p.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in holdout), encoding="utf-8")
    return p


def spec_with(n_dims: int = 3, tests_per_dim: int = 1, dim_names: list[str] | None = None) -> dict:
    """A desk spec over the aurora cards with ``n_dims`` dimensions (cards cycle ch01..ch03) and generated tests."""
    names = dim_names or ["construction", "whistle", "night-market-care", "safety", "storage-plan"][:n_dims]
    cids = list(CH)
    dims = []
    for k, name in enumerate(names):
        cid = cids[k % 3]
        dims.append({"dimension": name, "title": f"T {name}", "question": f"Q {name}", "cards": [cid],
                     "cues": [f"{name} cue", ALIASES[cid][k % 10]],
                     "tests": [f"tune question {t} for {name}?" for t in range(tests_per_dim)]})
    return {"threshold": 0.8, "dimensions": dims}


class FakeKlode:
    """A scripted ``klode`` executable in ``bin_dir``; ``install(monkeypatch)`` puts it first on PATH."""

    def __init__(self, bin_dir: Path):
        bin_dir.mkdir(parents=True, exist_ok=True)
        self.bin_dir = bin_dir
        self.scenario_path = bin_dir / "scenario.json"
        self.calls_path = bin_dir / "calls.jsonl"
        self.scenario: dict = {"diagnose": {}, "core": {}, "strict_stdout": None, "strict_rc": 0, "search": {}}
        self.save()
        exe = bin_dir / "klode"
        exe.write_text(FAKE_KLODE.format(python=sys.executable, scenario=str(self.scenario_path),
                                         calls=str(self.calls_path)), encoding="utf-8")
        exe.chmod(0o755)

    def save(self) -> None:
        self.scenario_path.write_text(json.dumps(self.scenario, ensure_ascii=False), encoding="utf-8")

    def route(self, answers: dict[str, list[str] | str | None]) -> None:
        for q, dims in answers.items():
            self.scenario["diagnose"][q] = [] if dims is None else ([dims] if isinstance(dims, str) else list(dims))
        self.save()

    def set(self, **kw) -> None:
        self.scenario.update(kw)
        self.save()

    def install(self, monkeypatch) -> None:
        import os
        monkeypatch.setenv("PATH", str(self.bin_dir) + os.pathsep + os.environ.get("PATH", ""))

    def calls(self) -> list[list[str]]:
        if not self.calls_path.exists():
            return []
        return [json.loads(x) for x in self.calls_path.read_text(encoding="utf-8").splitlines() if x.strip()]


FAKE_KLODE = '''#!{python}
"""Scripted klode stand-in for WI-02c unit tests (see tests/_support/desk_fx.py)."""
import json
import sys

with open({calls!r}, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\\n")
sc = json.load(open({scenario!r}, encoding="utf-8"))
args = sys.argv[1:]
json_mode = False
toml = None
while args and args[0].startswith("-"):
    if args[0] == "--json":
        json_mode = True
        args = args[1:]
    elif args[0] in ("-c", "--config"):
        toml = args[1]
        args = args[2:]
    else:
        args = args[1:]
cmd, rest = (args[0], args[1:]) if args else ("", [])
core = sc.get("core", {{}})
default_core = {default_core!r}
if cmd == "check":
    if json_mode:
        print("--json is not implemented for `check` (supported: cards, consult, diagnose, kbs, lenses, review, "
              "search, verify, zoom) — it would have printed prose")
        sys.exit(0)
    out = sc.get("strict_stdout")
    if out is None:
        out = "library check — 3 cards, 3 shelf sources\\n  NOTE  [E] not a git work tree\\n\\nOK: 0 errors, 0 warning(s)"
    print(out)
    sys.exit(sc.get("strict_rc", 0))
if cmd == "diagnose":
    q = " ".join(rest)
    if "raw_diagnose_stdout" in sc:
        sys.stdout.write(sc["raw_diagnose_stdout"])
        sys.exit(sc.get("raw_diagnose_rc", 0))
    dims = sc.get("diagnose", {{}}).get(q, [])
    if json_mode:
        print(json.dumps({{"op_id": "diagnose", "provenance": {{"op_id": "diagnose", "kb": "fake", "op_version": "1",
                          "source_sha": None, "source_version": None, "policy": None}},
                          "value": [[d, core.get(d, default_core)] for d in dims], "capability": "stable"}},
                         ensure_ascii=False))
    elif dims:
        print("\\u201c" + q + "\\u201d \\u2192 consult these panels (most-relevant first):\\n")
        for d in dims:
            print("  \\u2022 " + d + "\\n      " + core.get(d, default_core) + "\\n      \\u2192 klode consult " + d)
    else:
        print("no symptom matched. Browse with `klode consult`, or `klode search <terms>`.")
    sys.exit(0 if dims else 1)
if cmd == "search":
    terms = [a for a in rest if a != "--full"]
    q = " ".join(terms)
    ids = sc.get("search", {{}}).get(q, [])
    print(json.dumps({{"op_id": "search", "value": {{"hits": [{{"id": i}} for i in ids]}}}}))
    sys.exit(0)
print("fake klode: unsupported " + repr(sys.argv[1:]), file=sys.stderr)
sys.exit(2)
'''.replace("{default_core!r}", repr(DEFAULT_CORE))
