"""U5 (04 section 6), the part that needs no model: asking the medical library "the dose of 示例片" and checking an
answer's quotation. What the user sees (04): every answer line carries a verbatim quotation ``(grep: 示例片 10mg
2026-09-18)`` and ``klode verify`` says ``VERIFIED`` for it; for something the library does not hold the answer is "not
found", never an invented value.

Matrix U5-01 (lane cli_integration): a medical fixture library (the prescription page ``fx-rx-stamp``, audited, written
back, built with the real ``klode`` 0.5.0, checked by ``kb-check`` and published with ``kb-publish`` into a private
registry) is asked by the two ways a user (or the expert agent) has of quoting:

- the CLI ``klode verify <card> "<phrase>"``; klode 0.5.0 prints ``FOUND`` / ``NOT-FOUND`` (its ``--json`` resolution is
  ``found`` / ``not-found``) - ``VERIFIED`` is the word of the MCP tool ``verify_quote``;
- the klode-kit MCP wrapper (``bin/klode-kit mcp --kb kb/<id>``, what ``.mcp.json`` starts), tool ``verify_quote``.

They must agree on every phrase. The example phrase of 04 names the date 2026-09-18; the prescription fixture is dated
2026-09-19, so the library holds ``示例片 10mg 2026-09-19`` (and ``5mg``, ``0.25g``) - the 04 phrase is the "date the
library does not hold" case. The model half (the expert agent answering) is ``test_u5_expert_qa.py`` (G2).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from _support import accept_fx as A
from _support import build_fx as B
from _support import mcp_fx as M

pytestmark = pytest.mark.cli_integration

PRESENT = ["示例片 10mg 2026-09-19", "示例片 5mg 2026-09-19", "示例片 0.25g 2026-09-19"]
ABSENT = ["示例片 10mg 2026-09-18", "示例片 7mg 2026-09-19", "zzz no such line"]


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("u5")
    project = B.project(tmp, "fx-rx-stamp")
    res = B.build(project, tmp)
    assert res.returncode == 0, res.stderr.decode()
    (build,) = B.builds(project)
    world = A.check_and_publish(project, tmp, build)
    return build, world


def test_u5_verify_cli(library):
    build, world = library
    card = A.card_with(build, PRESENT[0])
    server = M.start(world)
    try:
        assert server.version()["build_id"] == build.name  # the wrapper serves the published build
        for phrase in PRESENT:
            prose = A.klode(build, "verify", card, phrase)
            assert prose.returncode == 0 and "FOUND" in prose.stdout and "NOT-FOUND" not in prose.stdout, prose.stdout
            data = json.loads(A.klode(build, "verify", card, phrase, json_mode=True).stdout)["value"]
            assert (data["resolution"], data["card"], data["phrase"]) == ("found", card, phrase)
            assert data["lines"] and phrase in data["lines"][0][1]
            answered = M.text_of(server.call("verify_quote", {"id": card, "phrase": phrase}))
            assert answered.split("\n")[1].startswith("VERIFIED"), answered  # the same verdict, the MCP word
            assert phrase in answered
        for phrase in ABSENT:
            prose = A.klode(build, "verify", card, phrase)
            assert prose.returncode == 1 and "NOT-FOUND" in prose.stdout, prose.stdout
            assert json.loads(A.klode(build, "verify", card, phrase, json_mode=True).stdout)["value"][
                "resolution"] == "not-found"
            answered = M.text_of(server.call("verify_quote", {"id": card, "phrase": phrase}))
            assert "NOT FOUND" in answered and "VERIFIED" not in answered, answered
    finally:
        server.close()


def test_every_exported_line_is_found_by_both_ways(library):
    """S5 over the whole library: each observation line of the export (the line the expert quotes) is found by
    ``klode verify`` and verified by the wrapper - and the registered library is where ``.mcp.json`` says."""
    build, world = library
    server = M.start(world)
    try:
        lines = [(p.stem, ln.rsplit(" ⟨", 1)[0]) for p in sorted((build / "library" / "items").glob("*.txt"))
                 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) >= 3
        for card, phrase in lines:
            assert A.klode(build, "verify", card, phrase).returncode == 0
            assert M.text_of(server.call("verify_quote", {"id": card, "phrase": phrase})).split("\n")[1].startswith(
                "VERIFIED")
    finally:
        server.close()
    mcp = json.loads((Path(world.project) / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    (entry,) = mcp.values()
    assert entry["args"][:2] == ["mcp", "--kb"] and Path(entry["args"][2]) == world.kb
