"""WI-02d: packaged prompts and agent templates carry no private or machine paths (matrix 02d-08).

Specification (03 G10 "prompts/SKILL 引用 003/189/16_d"; 02 §3.1; WI-02c -> 02d deviation: the desk prompt names the
packaged ``klode_kit/prompts/synthesis.md``):

- No file under ``src/klode_kit/prompts/`` or ``src/klode_kit/templates/agent_*.md`` names a private project
  (``003/``, ``189/``, ``16_d``), the user's skill directory (``~/.claude``, ``skills/klode-build``) or an absolute
  home path (``/Users/``, ``/home/``, ``~/Documents``).
- The prompts point at the plugin's own commands: ``write_card.md`` checks with ``klode-kit check-cards`` (WI-02a)
  and reads the skeleton with ``klode-kit skeleton``; ``synthesis.md`` checks with ``klode-kit check-synthesis``
  (WI-02c). The file the desk worker prompt names (``desk_build.SYNTHESIS_PROMPT``) exists.

No law: a static scan.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import klode_kit
from klode_kit.build import desk_build

pytestmark = pytest.mark.unit

PKG = Path(klode_kit.__file__).resolve().parent
PROMPTS = PKG / "prompts"
PRIVATE = [
    re.compile(r"(?<![\w.])(?:003|189)/"),
    re.compile(r"16_d"),
    re.compile(r"~/\.claude"),
    re.compile(r"skills/klode-build"),
    re.compile(r"/Users/"),
    re.compile(r"/home/"),
    re.compile(r"~/Documents"),
]


def scanned() -> list[Path]:
    files = sorted(PROMPTS.glob("*.md")) + sorted((PKG / "templates").glob("agent_*.md"))
    return files


def test_prompts_no_private_paths():
    """02d-08 (S3): no private project, user-skill or absolute home path in any packaged prompt or agent
    template; the scan covers the two vendored prompts and the medical agent template (never vacuous)."""
    names = {p.name for p in scanned()}
    assert {"write_card.md", "synthesis.md", "agent_records_zh.md"} <= names
    hits = [(p.name, rx.pattern, m.group(0)) for p in scanned() for rx in PRIVATE
            for m in rx.finditer(p.read_text(encoding="utf-8"))]
    assert hits == []


@pytest.mark.parametrize("text", ["see 189/kb/x", "范本：`003/kb/a`", "~/.claude/skills/klode-build/scripts",
                                  "/Users/someone/x", "/home/u/x", "~/Documents/Claude/Projects"])
def test_private_patterns_catch(text):
    """02d-08 guard on the scan itself: every pattern class above is caught (the scan cannot pass by being
    blind); version-like numbers such as ``0.5.0`` or ``2.1.189`` are not paths."""
    assert any(rx.search(text) for rx in PRIVATE)
    assert not any(rx.search(s) for rx in PRIVATE for s in ("klode 0.5.0", "v2.1.189", "ISO 189"))


def test_prompts_name_plugin_commands():
    """The write-card prompt checks with ``klode-kit check-cards`` and reads the skeleton with ``klode-kit
    skeleton``; the synthesis prompt checks with ``klode-kit check-synthesis``; neither runs ``python3 <path>``."""
    assert (PROMPTS / "write_card.md").is_file() and (PROMPTS / "synthesis.md").is_file()
    wc = (PROMPTS / "write_card.md").read_text(encoding="utf-8")
    syn = (PROMPTS / "synthesis.md").read_text(encoding="utf-8")
    assert "klode-kit check-cards" in wc and "klode-kit skeleton" in wc
    assert "klode-kit check-synthesis" in syn
    assert not re.search(r"python3 \S+\.py", wc + syn)


def test_desk_prompt_target_exists():
    """WI-02c -> 02d: the packaged synthesis prompt the desk worker prompt names exists and is the vendored one
    (its format block names ``## Craft``)."""
    assert desk_build.SYNTHESIS_PROMPT == PROMPTS / "synthesis.md"
    assert desk_build.SYNTHESIS_PROMPT.is_file()
    assert "## Craft" in desk_build.SYNTHESIS_PROMPT.read_text(encoding="utf-8")
