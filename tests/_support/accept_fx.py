"""Helpers of the U1..U8 acceptance suite (WI-31) that need a finished, checked and published library.

Everything runs through the public commands - ``build`` (13 steps), ``kb-check``, ``kb-publish`` - against a data
project in a temporary directory. Nothing here touches a user-global directory: ``HOME`` is a private directory and
``KLODE_REGISTRY`` a private file (``publish_fx.env``), the registrations (``.mcp.json``, the expert agent file,
``settings.local.json``) land in the temporary data project.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from _support import build_fx as B
from _support import desk_fx as DX
from _support import kb_check_fx as KX
from _support import publish_fx as PX
from _support.flows import base_env
from _support.intake_fx import IntakeRig
from klode_kit.audit import confirm_page as CP

ROUTES = ["vision", "mlx", "claude", "codex"]


def front(path: Path) -> dict:
    """The JSON front matter of a record / view file."""
    text = path.read_text(encoding="utf-8")
    return json.loads(text[4:text.index("\n---", 4)])


def klode(build: Path, *args: str, json_mode: bool = False) -> subprocess.CompletedProcess:
    """The real ``klode`` CLI on a build's ``library.toml``."""
    exe = shutil.which("klode")
    assert exe, "klode 0.5.0 is required on PATH for the cli_integration lane"
    pre = ["--json"] if json_mode else []
    return subprocess.run([exe, *pre, "-c", str(build / "library.toml"), *args], capture_output=True, text=True,
                          timeout=300)


def check_and_publish(project: Path, tmp: Path, build: Path) -> SimpleNamespace:
    """``kb-check`` the build, then ``kb-publish`` it; returns the world the MCP helpers (``mcp_fx``) expect."""
    kb = B.kb_dir(project)
    proc, rec = KX.check(kb, "--build", build.name)
    assert proc.returncode == 0 and rec is not None and rec["status"] == "PASS", (proc.stderr.decode(), rec)
    pub = PX.cli(kb, project, tmp, "--build", build.name)
    assert pub.returncode == 0, pub.stdout.decode() + pub.stderr.decode()
    return SimpleNamespace(project=project, kb=kb, ids={"A": build.name}, env=PX.env(tmp), tmp=tmp)


def env_with_private_home(tmp: Path) -> dict[str, str]:
    e = base_env({"HOME": str(B.home(tmp)), "KLODE_REGISTRY": str(PX.registry_file(tmp)),
                  "KLODE_KIT_TEST_SEAMS": "1"})
    e.pop("KLODE_KIT_REAL", None)
    return e


def card_with(build: Path, phrase: str) -> str:
    """The id of the card whose source file holds ``phrase`` (the library is searched like a user would grep it)."""
    hits = sorted(p.stem for p in (build / "library").rglob("*.txt") if phrase in p.read_text(encoding="utf-8"))
    assert hits, f"no source file holds {phrase!r}"
    return hits[0]


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class Flow:
    """med-intake (the user's first command) then the click, the export and the writeback as their own processes."""

    def __init__(self, tmp: Path, fixture: str = "fx-u8"):
        self.tmp = tmp
        self.rig = IntakeRig(tmp, fixture)
        self.project = self.rig.project

    def intake(self) -> subprocess.CompletedProcess:
        res = self.rig.run()
        assert res.returncode == 10, res.stderr.decode()
        return res

    @property
    def page(self) -> Path:
        return self.project / "confirm" / "20261022.html"

    def data(self) -> dict:
        return CP.page_data(self.page.read_bytes())

    def fields(self) -> dict[str, dict]:
        return {f["field_id"]: dict(f, rec_id=r["rec_id"], page_id=r["page_id"])
                for r in self.data()["records"] for f in r["fields"]}

    def clicks(self) -> list[dict]:
        """What the user does: the starred (or, with none starred, the first) route everywhere; on the tie "none is
        right" and nothing typed."""
        out = []
        for fid, f in self.fields().items():
            if f["verdict"] == "tie":
                out.append({"field_id": fid, "kind": "none"})
                continue
            routes = [r for r in ROUTES if f["seen"].get(r) == f["recommended"]] or \
                [r for r in ROUTES if f["seen"].get(r)]
            out.append({"field_id": fid, "kind": "route", "route": routes[0]})
        return out

    def export(self, clicks: list[dict]) -> Path:
        path = self.tmp / "choices.json"
        path.write_text(json.dumps(CP.export_choices(self.page.read_bytes(), clicks), ensure_ascii=False),
                        encoding="utf-8")
        return path

    def klode_kit(self, *args: str, timeout: float = 600) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "klode_kit", *args, "--project", str(self.project)],
                              env=base_env(), capture_output=True, cwd=str(self.project), timeout=timeout)

    def writeback(self, choices: Path) -> subprocess.CompletedProcess:
        return self.klode_kit("kb-audit", "--writeback", str(choices))

    def views(self) -> dict[str, dict]:
        assert self.klode_kit("views").returncode == 0
        return {p.name: front(p) for p in sorted((self.project / "records").glob("*.md"))}

    def revisions(self) -> list[str]:
        return sorted(p.name for p in (self.project / "records" / "_rev").glob("*.md"))




def aurora_inputs(tmp: Path) -> list[str]:
    """``--source/--shelf/--manifest/--desk/--syntheses`` of U7 on the aurora fixture's ``desk.l1.json``."""
    desk_dir = tmp / "src"
    desk_dir.mkdir()
    shutil.copyfile((B.AURORA / "src") / "desk.l1.json", desk_dir / "desk.json")
    shutil.copyfile((B.AURORA / "src") / "holdout.jsonl", desk_dir / "holdout.jsonl")
    spec = json.loads((desk_dir / "desk.json").read_text(encoding="utf-8"))
    syn = tmp / "syntheses"
    syn.mkdir()
    for d in spec["dimensions"]:
        anchors = [a for c in d["cards"] for a in DX.ANCHORS[c]][:4]
        (syn / f"{d['dimension']}.md").write_text(DX.synthesis(d["dimension"], d["cards"], anchors), encoding="utf-8")
    return ["--source", str(B.AURORA / "aurora-kettle.txt"), "--shelf", "book",
            "--manifest", str(B.aurora_manifest(tmp / "chapters-book.json")), "--desk", str(desk_dir / "desk.json"),
            "--syntheses", str(syn)]




# --------------------------------------------------------------------------- real (G2) runs


def real_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """The environment of a real run: the process environment without the replay fakes on PATH, without test seams
    and fake-CLI variables, with ``KLODE_KIT_REAL`` / ``KLODE_KIT_GATE_ACK`` as the tester set them (gate G2)."""
    fakes = str((B.REPO / "tests" / "fakes" / "bin").resolve())
    env = {k: v for k, v in os.environ.items() if not k.startswith("KLODE_KIT_FAKE_")
           and k not in {"KLODE_KIT_TEST_SEAMS", "KLODE_KIT_ENGINE_REPLAY", "PYTHONPATH"}}
    env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                  if p and str(Path(p).resolve()) != fakes)
    env.update(extra or {})
    return env


def stage_isolation_receipts(project: Path) -> None:
    """The stored isolation receipts of the two cloud routes (``docs/spikes/receipts``, WI-00c1b / WI-00c2c) where
    ``kb-ocr`` looks for them by default: ``<project>/work/isolation/<route>/isolation_receipt.json``."""
    src = B.REPO / "docs" / "spikes" / "receipts"
    for route in ("claude", "codex"):
        (receipt,) = sorted(src.glob(f"{route}-isolation_receipt-*.json"))
        dest = project / "work" / "isolation" / route / "isolation_receipt.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(receipt, dest)


def premask(project: Path, inbox: Path, env: dict[str, str]) -> str:
    """The masking step the user entries do not run by themselves (WI-31 finding: ``mask.mask_batch`` is called by no
    command): ``kb-ocr`` with the local Vision route makes the batch and its derived pages, then the real Vision masker
    registers the masked pictures; the cloud routes of the next ``kb-ocr`` / ``med-intake`` / ``pipeline`` run read
    those. Returns the batch id."""
    from klode_kit.audit import mask
    from klode_kit.ocr import engines as E

    done = subprocess.run([sys.executable, "-m", "klode_kit", "kb-ocr", "--batch", str(inbox), "--project",
                           str(project), "--routes", "vision"], env={**env, "PYTHONPATH": str(B.REPO / "src")},
                          capture_output=True, cwd=str(project), timeout=600)
    assert done.returncode == 0, done.stderr.decode()
    (batch,) = [p for p in (project / "work").iterdir() if p.is_dir() and not p.is_symlink()
                and (p / "derived.jsonl").is_file()]
    mask.mask_batch(project, batch.name, engine=E.OcrMacEngine())
    return batch.name
