"""kb-verifier advice: ``a<N>/advice.json`` and the one command that produces it (WI-12; design 02 r2.3 row 3).

    klode-kit kb-advice --batch B [--project P] [--rev N] --from FILE
    klode-kit kb-advice --batch B [--project P] [--rev N] --run [--claude C] [--max-budget-usd X] [--timeout-s S]

``kb-verifier`` (``agents/kb-verifier.md``) is a read-only agent: it looks at the dispute crops of one audit revision and
says, per field, what it reads there. Its whole output is ``advice.json``: a JSON array of
``{field_id, suggestion, evidence}`` (``schemas/advice.json``). The confirm page shows each entry as a hint on its field
(``confirm_page._item_html``); it is never selected for the human and never reaches ``choices.json``. **The verifier
changes no status**: only ``audit/writeback.py`` writes ``status`` / ``rev`` / ``uncertain`` (K3); this module opens nothing
under ``records/`` for writing and writes exactly one file, ``a<N>/advice.json``.

``--from FILE`` validates and stores advice produced elsewhere (a host session running the agent). ``--run`` starts the
agent itself::

    claude -p --agents <agents/*.md as JSON> --agent kb-verifier --permission-mode dontAsk
           --allowedTools "Read,Grep,Glob" --disallowedTools "Edit,Write,Bash,NotebookEdit"
           --output-format json --json-schema {advice: [...]} --max-budget-usd X --no-session-persistence "<prompt>"

with cwd = ``work/<batch>/audit/a<N>`` - the crops and nothing else it needs are under that directory. The prompt names the
fields (id, item label, what each route read, the crop's path relative to the cwd); no absolute path of the data project
goes into it. After the run every file under ``a<N>`` is compared with the state before: a verifier that wrote anything
fails the run (exit 20) and its advice is dropped.

**Privacy**: the crops are cut from the masked derivative of a page when one exists (WI-07d) but they are not registered
images - ``ocr send`` never sees them. Reading a crop through ``claude`` shows that image to the model. This command is
therefore behind the shared G2 gate (``gate.vet``: a real ``claude`` needs ``KLODE_KIT_REAL=1`` and
``KLODE_KIT_GATE_ACK=G2-<yyyymmdd>``; exit 61 otherwise, nothing started) and a run against a real data project is the
user's explicit decision, not something a pipeline stage does on its own.

Exit codes: 0 written (the path is printed); 2 usage (bad option, unknown batch / revision, unreadable ``--from``); 20 the
verifier failed (exit code, ``is_error``, no structured answer, advice that breaks the contract, a file it changed,
timeout); 30 the advice is refused (contract violation of ``--from`` input, ``advice.json`` or the sealed
``confirm.html`` already there); 40 no ``claude`` to start; 61 the real-call gate is closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from klode_kit import bridge
from klode_kit import gate as G
from klode_kit.audit import confirm_page as CP

EXIT_OK, EXIT_USAGE, EXIT_FAILED, EXIT_CONTRACT, EXIT_CONFIG, EXIT_GATE = 0, 2, 20, 30, 40, G.GATE_EXIT
VERIFIER_AGENT = "kb-verifier"
ALLOWED_TOOLS = "Read,Grep,Glob"
DISALLOWED_TOOLS = "Edit,Write,Bash,NotebookEdit"
DEFAULT_MAX_BUDGET_USD = 1.0
DEFAULT_TIMEOUT_S = 600.0
KILL_GRACE_S = 2.0
SCHEMA_DROP = ("$schema", "$id", "title", "description")


class AdviceError(Exception):
    """A refusal with its exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_CONTRACT):
        super().__init__(message)
        self.exit_code = exit_code


class AdviceGateClosed(G.GateClosed):
    """A real ``claude`` would be started with the G2 gate closed (exit 61, zero calls)."""


# --------------------------------------------------------------------------- the contract


@lru_cache(maxsize=1)
def _schema_text() -> str:
    return resources.files("klode_kit").joinpath("schemas", "advice.json").read_text(encoding="utf-8")


def schema() -> dict:
    """A fresh copy of ``schemas/advice.json`` (the file's contract: an array of advice entries)."""
    return json.loads(_schema_text())


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    doc = json.loads(_schema_text())
    Draft202012Validator.check_schema(doc)
    return Draft202012Validator(doc)


def issues(doc: Any) -> list[str]:
    """Contract violations of ``doc`` as ``<path>: <message>`` lines (empty when it is valid advice)."""
    out = []
    for err in sorted(_validator().iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path]):
        path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path) or "<root>"
        out.append(f"{path}: {err.message}")
    return out


def _wrapper_schema() -> dict:
    """The ``--json-schema`` of a run: a structured result must be an object, so the array travels as ``advice``."""
    inner = {k: v for k, v in schema().items() if k not in SCHEMA_DROP}
    return {"type": "object", "additionalProperties": False, "required": ["advice"], "properties": {"advice": inner}}


# --------------------------------------------------------------------------- the audit revision


def _revision(project: Path, batch_id: str, n: int | None) -> tuple[int, Path]:
    try:
        return CP._rev_dir(project, batch_id, n)
    except CP.ConfirmError as exc:
        raise AdviceError(str(exc), exc.exit_code) from exc


def shown_fields(adir: str | os.PathLike) -> list[dict]:
    """The fields the confirm page shows (verdict not ``agree``, or uncertain, or missing): the only fields advice may
    speak about. Each: ``field_id``, ``item_label``, ``verdict``, ``seen`` (route -> value) and ``crop`` (path relative to
    ``a<N>``, or None)."""
    adir = Path(adir)
    batch_id, n = adir.parent.parent.name, int(adir.name[1:])
    try:
        cells = {c.get("field_id"): c for c in CP._jsonl(adir / "cells.jsonl")}
        out = []
        for cand in CP._jsonl(adir / "candidates.jsonl"):
            for f in cand.get("fields", []):
                if not CP._shown(f):
                    continue
                votes = (cells.get(f["field_id"]) or {}).get("candidates") or {}
                out.append({"field_id": f["field_id"], "item_label": f.get("item_label"), "verdict": f.get("verdict"),
                            "seen": {e: (v or {}).get("value") for e, v in votes.items()},
                            "crop": CP._crop_src(f.get("crop"), batch_id, n, adir)})
    except CP.ConfirmError as exc:
        raise AdviceError(str(exc), exc.exit_code) from exc
    return out


def _problems(doc: Any, adir: Path) -> list[str]:
    problems = issues(doc)
    if problems:
        return problems
    shown = {f["field_id"] for f in shown_fields(adir)}
    seen: set[str] = set()
    for entry in doc:
        fid = entry["field_id"]
        if fid not in shown:
            problems.append(f"{fid}: not a field the confirm page shows")
        if fid in seen:
            problems.append(f"{fid}: more than one entry for the field")
        seen.add(fid)
    return problems


def _bytes(doc: list) -> bytes:
    return (json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _write_once(adir: Path, data: bytes) -> Path:
    """``a<N>/advice.json``: temporary file + hard link (fails if the name exists), read-only. The same bytes again are a
    no-op; other bytes are refused."""
    target = adir / "advice.json"
    if target.exists():
        if target.read_bytes() == data:
            return target
        raise AdviceError(f"{target} already holds other advice (an audit revision is written once)")
    if (adir / "confirm.html").exists():
        raise AdviceError(f"{adir / 'confirm.html'} is already written: advice must exist before the page is sealed")
    tmp = adir / f".advice.json.{secrets.token_hex(4)}.tmp"
    with open(tmp, "xb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    try:
        os.chmod(tmp, 0o444)
        os.link(tmp, target)
    except FileExistsError:
        raise AdviceError(f"{target} already exists (an audit revision is written once)") from None
    finally:
        os.remove(tmp)
    fd = os.open(adir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return target


def ingest(project: str | os.PathLike, batch_id: str, doc: Any, n: int | None = None) -> Path:
    """Validate ``doc`` and store it as ``a<N>/advice.json`` (exit 30 on any violation, nothing written)."""
    project = Path(project)
    n, adir = _revision(project, batch_id, n)
    problems = _problems(doc, adir)
    if problems:
        raise AdviceError("advice refused: " + "; ".join(problems))
    return _write_once(adir, _bytes(doc))


# --------------------------------------------------------------------------- the verifier run


def _prompt(fields: list[dict]) -> str:
    lines = [
        "任务：核对确认页上有分歧的字段，给出建议。建议只作提示，不会自动选中，也不会改任何记录。",
        "当前目录是一个审计修订目录；只读它。每个字段的分歧小图在 crops/ 下，用 Read 直接看图。",
        "对每个你在小图上能清楚读出来的字段，给出 field_id、suggestion（小图上读到的值，原样）、evidence（一句话：你在图上看到了什么）。",
        "读不清、没有小图的字段不要写。不写已经一致的字段，也不写这里没列出的字段。",
        '完成后以结构化结果返回：{"advice": [ ... ]}（没有把握的话就返回空数组）。',
        "",
        "字段：",
    ]
    for f in fields:
        reads = "，".join(f"{route}={value}" for route, value in f["seen"].items()) or "（没有读数）"
        lines += [f"- field_id: {f['field_id']}",
                  f"  项目: {f['item_label'] if f['item_label'] is not None else '（无）'}",
                  f"  各路读数: {reads}",
                  f"  小图: {f['crop'] if f['crop'] else '无小图'}"]
    return "\n".join(lines)


def verifier_argv(claude: str, fields: list[dict], *, agents_dir: Path | None = None,
                  max_budget_usd: float = DEFAULT_MAX_BUDGET_USD) -> list[str]:
    """The ``claude -p`` argv of a verifier run over ``fields`` (cwd: the ``a<N>`` directory)."""
    agents = bridge.agents_json(agents_dir if agents_dir is not None else bridge.PLUGIN_ROOT / "agents")
    if VERIFIER_AGENT not in agents:
        raise AdviceError(f"no {VERIFIER_AGENT} agent definition", EXIT_CONFIG)
    return [
        claude, "-p",
        "--agents", json.dumps(agents, ensure_ascii=False, separators=(",", ":")),
        "--agent", VERIFIER_AGENT,
        "--permission-mode", "dontAsk",
        "--allowedTools", ALLOWED_TOOLS,
        "--disallowedTools", DISALLOWED_TOOLS,
        "--output-format", "json",
        "--json-schema", json.dumps(_wrapper_schema(), ensure_ascii=False, separators=(",", ":")),
        "--max-budget-usd", f"{max_budget_usd:.2f}",
        "--no-session-persistence",
        _prompt(fields),
    ]


def _tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _spawn(argv: list[str], cwd: Path, timeout_s: float, env: dict[str, str] | None) -> tuple[int, bytes, bytes]:
    proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        for sig, wait in ((signal.SIGTERM, KILL_GRACE_S), (signal.SIGKILL, None)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.communicate(timeout=wait)
                break
            except subprocess.TimeoutExpired:
                continue
        raise AdviceError(f"the verifier did not finish within {timeout_s:g}s", EXIT_FAILED) from None
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return proc.returncode, out, err


def _answer(returncode: int, out: bytes, err: bytes) -> list:
    if returncode != 0:
        tail = err.decode("utf-8", "replace").strip()[-300:]
        raise AdviceError(f"the verifier process exited {returncode}" + (f": {tail}" if tail else ""), EXIT_FAILED)
    try:
        obj = json.loads(out.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdviceError(f"the verifier's stdout is not JSON: {exc}", EXIT_FAILED) from exc
    if not isinstance(obj, dict):
        raise AdviceError("the verifier's stdout JSON is not an object", EXIT_FAILED)
    if obj.get("is_error") is True:
        raise AdviceError(f"the verifier run reported an error ({obj.get('subtype')})", EXIT_FAILED)
    structured = obj.get("structured_output")
    if not isinstance(structured, dict) or "advice" not in structured:
        raise AdviceError("the verifier returned no structured advice", EXIT_FAILED)
    return structured["advice"]


def run(project: str | os.PathLike, batch_id: str, n: int | None = None, *, claude: str = "claude",
        max_budget_usd: float = DEFAULT_MAX_BUDGET_USD, timeout_s: float = DEFAULT_TIMEOUT_S,
        agents_dir: Path | None = None, env: dict[str, str] | None = None) -> Path:
    """Run the verifier over the fields in dispute and store its advice. Nothing in the project changes except
    ``a<N>/advice.json`` (a failed run changes nothing)."""
    project = Path(project)
    n, adir = _revision(project, batch_id, n)
    target = adir / "advice.json"
    if target.exists():
        raise AdviceError(f"{target} already exists: an audit revision gets its advice once", EXIT_CONTRACT)
    if (adir / "confirm.html").exists():
        raise AdviceError(f"{adir / 'confirm.html'} is already written: advice must exist before the page is sealed")
    fields = shown_fields(adir)
    if not fields:                                     # nothing in dispute: nothing to advise on, nothing to pay for
        return _write_once(adir, _bytes([]))
    argv = verifier_argv(claude, fields, agents_dir=agents_dir, max_budget_usd=max_budget_usd)
    exe = G.vet(claude, error=AdviceGateClosed)
    if exe is None:
        raise AdviceError(f"{claude!r} is not an executable on PATH", EXIT_CONFIG)
    argv[0] = str(exe)
    before = _tree(adir)
    returncode, out, err = _spawn(argv, adir, timeout_s, env)
    after = _tree(adir)
    if after != before:
        changed = sorted(set(before) ^ set(after) | {k for k in before.keys() & after.keys() if before[k] != after[k]})
        raise AdviceError("the verifier changed files in the audit directory (" + ", ".join(changed[:5])
                          + "); its advice is dropped", EXIT_FAILED)
    doc = _answer(returncode, out, err)
    problems = _problems(doc, adir)
    if problems:
        raise AdviceError("the verifier's advice breaks the contract: " + "; ".join(problems), EXIT_FAILED)
    return _write_once(adir, _bytes(doc))


# --------------------------------------------------------------------------- CLI


def _positive(value: str) -> float:
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("must be a positive number")
    return number


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit kb-advice",
                                     description="kb-verifier advice for the confirm page (hints only).")
    parser.add_argument("--batch", required=True, help="batch id (12 hex characters)")
    parser.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    parser.add_argument("--rev", type=int, default=None, help="audit revision N of a<N> (default: the newest)")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--from", dest="source", type=Path, help="store advice.json content produced elsewhere")
    what.add_argument("--run", action="store_true", help="start the kb-verifier agent (G2 gate)")
    parser.add_argument("--claude", default="claude", help="the claude executable (default: claude on PATH)")
    parser.add_argument("--max-budget-usd", type=_positive, default=DEFAULT_MAX_BUDGET_USD)
    parser.add_argument("--timeout-s", type=_positive, default=DEFAULT_TIMEOUT_S)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else EXIT_OK
    project = Path.cwd() if args.project is None else args.project
    try:
        if args.run:
            path = run(project, args.batch, args.rev, claude=args.claude, max_budget_usd=args.max_budget_usd,
                       timeout_s=args.timeout_s)
        else:
            _revision(project, args.batch, args.rev)           # unknown batch / revision is a usage error first
            try:
                raw = args.source.read_bytes()
            except OSError as exc:
                raise AdviceError(f"cannot read {args.source}: {exc}", EXIT_USAGE) from exc
            try:
                doc = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AdviceError(f"{args.source} is not JSON: {exc}") from exc
            path = ingest(project, args.batch, doc, args.rev)
    except G.GateClosed as exc:
        print(f"kb-advice: {exc}", file=sys.stderr)
        return EXIT_GATE
    except AdviceError as exc:
        print(f"kb-advice: {exc}", file=sys.stderr)
        return exc.exit_code
    print(path)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit kb-advice``
    sys.exit(main())
