"""The desk (frameworks): one ``src/desk.json`` defines the dimensions; this command runs the rest (WI-02c; vendored).

Vendored from the user's klode-build skill ``scripts/desk_build.py`` (read-only source, 2026-10-24); the flow is
the klode-kb SKILL.md desk table, steps ②-⑤.

    klode-kit desk-build KB --apply      # ② library.toml [frameworks] on; _diagnostics.md generated from the spec
    klode-kit desk-build KB --prompts    # ③ one worker prompt per dimension -> KB/_work/desk_prompts/<dim>.txt
    klode-kit desk-build KB --status     # which dimension files exist (presence only - not a completion signal)
    klode-kit desk-build KB --verify [--holdout F] [--refreeze --reason R] [--receipt F] [--project DIR]
                                         # ⑤ check_synthesis + klode check --strict + diagnose, tuning / holdout apart
    common: [--spec F] (default KB/src/desk.json)
    exit 0 pass; 1 verify gate FAIL or --status with missing dimensions; 2 usage; 30 spec / holdout contract
    (incl. a holdout change without --refreeze --reason); 40 configuration, no klode, klode not answering JSON

Spec (kept from the original; the single source of truth, 03 A7)::

    {"threshold": 0.8,
     "dimensions": [{"dimension": "forces-of-progress", "title": "...", "question": "...",
                     "cards": ["<card id>", ...], "cues": ["四力", "four forces", ...], "tests": ["<question>", ...]}]}

Every listed card must exist in ``library/cards/``. Dispatching the workers is the caller's job (one worker per
dimension; the prompt comes from ``--prompts``). The desk is bound to its cards: after a card is rewritten,
``--verify`` fails S5 and the dimension is redone.

Kept: the ``--apply`` toml toggle and ``_diagnostics.md`` header/rows; the worker prompt; ``--status``; the
``--verify`` items check_synthesis all PASS and ``klode check --strict`` last line "0 errors".

Changed:

- **B3** (03 §2 step 24): routing was scraped from the prose output with ``\\b([a-z]+(?:-[a-z]+)+)\\b`` - only
  hyphenated words, so a dimension like ``safety`` was always ``∅``, and a hyphenated word in a core question was
  taken for a dimension. It is now read from ``klode --json -c <toml> diagnose <q>``: ``value[0][0]``, or none when
  ``value`` is empty. Shared with ``eval_compare`` (``diagnose_top``).
- **Tuning and holdout scored apart** (02 r2.1 row 1.3; the original merged ``tests`` and ``--gold`` into one
  ratio, so enough easy tuning hits could hide a failing holdout). The tuning set is the spec's ``tests``; its
  score is reported against ``threshold`` (``tune.ok``, the L1 loop's signal) but does not gate. The holdout set
  is ``src/holdout.jsonl`` (``--holdout`` / the original ``--gold`` override), one ``{"q", "gold": <dimension>}``
  per line; the gate is ``desk.holdout_min`` (5) questions or more and ``hit/total >= desk.holdout_pass`` (0.8).
  Holdout misses are reported, never fixed (iron law 2). A holdout line naming an unknown dimension, a duplicate
  question or a question that is also a tuning question is a contract error (30).
- **Frozen holdout**: its sha256 is frozen in ``src/holdout.freeze.json`` (``klode-kit.holdout_freeze/1``,
  ``{"schema", "sha256", "history": [{"sha256", "reason"}]}``) on the first ``--verify``. A later change is refused
  (exit 30, nothing written) unless ``--refreeze --reason R`` is given; the reason goes into the freeze history and
  the verify receipt.
- **Iron law 1** (klode-kb SKILL.md): no command here writes a dimension's synthesis file - only its worker does,
  and nobody touches it before the worker hands back. ``--status`` says so: presence is not completion.
- **Iron law 2**: ``_diagnostics.md`` is generated from the spec and never hand-edited; ``--verify`` fails when it
  differs from what the spec renders (rerun ``--apply`` after changing the spec, never edit the file).
- Verify receipt ``KB/desk_receipt.json`` (``--receipt`` overrides), ``klode-kit.desk_verify/1`` (``JSON_SCHEMA``;
  no wall-clock fields). No hard-coded paths: ``klode`` is found on PATH; prompts name the packaged
  ``klode_kit/prompts/synthesis.md`` and ``python3 -m klode_kit.build.check_synthesis``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from klode_kit import config as config_mod
from klode_kit.build import check_synthesis

EXIT_FAIL, EXIT_USAGE, EXIT_CONTRACT, EXIT_ENV = 1, 2, 30, 40
SPEC_FIELDS = ("dimension", "title", "question", "cards", "cues", "tests")
DIM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CUE_FORBIDDEN = (",", "|", "\n", "\r")
RECEIPT_NAME = "desk_receipt.json"
FREEZE_NAME = "holdout.freeze.json"
RECEIPT_SCHEMA_ID = "klode-kit.desk_verify/1"
FREEZE_SCHEMA_ID = "klode-kit.holdout_freeze/1"
SYNTHESIS_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "synthesis.md"
DIAG_HEADER = """# 诊断路由表 — 口语症状 → 维度

`klode diagnose "<一句话>"` / MCP `diagnose` 读这张表：左列是逗号分隔的**线索词**（小写比对，子串命中），右列是维度 stem（对应 `_syntheses/<stem>.md`）。
中文・英文的说法都登记在同一行；维度名不要放在 synthesis 的 aliases 里（SPEC：维度刻意不进模糊索引）。
本文件由 `klode-kit desk-build --apply` 从 `src/desk.json` 生成——改线索词请改 spec 再重跑，不要手改这里。

| symptom cues | dimensions |
|---|---|
"""

_RESULT = {"type": "object", "additionalProperties": False, "required": ["q", "expect", "got", "ok"],
           "properties": {"q": {"type": "string"}, "expect": {"type": "string"}, "got": {"type": ["string", "null"]},
                          "ok": {"type": "boolean"}}}
_SHA = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema", "status", "synthesis", "strict", "diagnostics", "tune", "holdout", "freeze"],
    "properties": {
        "schema": {"const": RECEIPT_SCHEMA_ID},
        "status": {"enum": ["pass", "fail"]},
        "synthesis": {"type": "object", "additionalProperties": False, "required": ["pass"],
                      "properties": {"pass": {"type": "boolean"}}},
        "strict": {"type": "object", "additionalProperties": False, "required": ["pass", "last_line"],
                   "properties": {"pass": {"type": "boolean"}, "last_line": {"type": "string"}}},
        "diagnostics": {"type": "object", "additionalProperties": False, "required": ["current"],
                        "properties": {"current": {"type": "boolean"}}},
        "tune": {"type": "object", "additionalProperties": False,
                 "required": ["hit", "total", "threshold", "ok", "results"],
                 "properties": {"hit": {"type": "integer", "minimum": 0}, "total": {"type": "integer", "minimum": 0},
                                "threshold": {"type": "number"}, "ok": {"type": "boolean"},
                                "results": {"type": "array", "items": _RESULT}}},
        "holdout": {"type": "object", "additionalProperties": False,
                    "required": ["path", "sha256", "hit", "total", "min", "pass_ratio", "pass", "results"],
                    "properties": {"path": {"type": "string"}, "sha256": {"oneOf": [_SHA, {"type": "null"}]},
                                   "hit": {"type": "integer", "minimum": 0},
                                   "total": {"type": "integer", "minimum": 0},
                                   "min": {"type": "integer"}, "pass_ratio": {"type": "number"},
                                   "pass": {"type": "boolean"}, "results": {"type": "array", "items": _RESULT}}},
        "freeze": {"type": "object", "additionalProperties": False,
                   "required": ["sha256", "initial", "refrozen", "reason"],
                   "properties": {"sha256": {"oneOf": [_SHA, {"type": "null"}]}, "initial": {"type": "boolean"},
                                  "refrozen": {"type": "boolean"}, "reason": {"type": ["string", "null"]}}},
    },
}
FREEZE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False, "required": ["schema", "sha256", "history"],
    "properties": {"schema": {"const": FREEZE_SCHEMA_ID}, "sha256": _SHA,
                   "history": {"type": "array", "minItems": 1, "items": {
                       "type": "object", "additionalProperties": False, "required": ["sha256", "reason"],
                       "properties": {"sha256": _SHA, "reason": {"type": "string", "minLength": 1}}}}},
}
_VALIDATOR = Draft202012Validator(JSON_SCHEMA)
_FREEZE_VALIDATOR = Draft202012Validator(FREEZE_SCHEMA)


class DeskError(Exception):
    """A refusal with its exit code (30 contract, 40 environment)."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- spec


def _str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def load(kb: Path, spec: Path | None = None) -> dict:
    """The validated desk spec (``spec`` or ``KB/src/desk.json``); DeskError(30) on any contract problem."""
    p = Path(spec) if spec is not None else Path(kb) / "src" / "desk.json"
    if not p.is_file():
        raise DeskError(EXIT_CONTRACT, f"找不到 spec：{p}")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DeskError(EXIT_CONTRACT, f"spec 不是合法 JSON：{p}：{exc}") from None
    dims = d.get("dimensions") if isinstance(d, dict) else None
    if not isinstance(dims, list) or not dims:
        raise DeskError(EXIT_CONTRACT, f"spec 没有 dimensions：{p}")
    seen: set[str] = set()
    for dim in dims:
        if not isinstance(dim, dict):
            raise DeskError(EXIT_CONTRACT, f"spec 维度不是对象：{dim!r}")
        for k in SPEC_FIELDS:
            if k not in dim:
                raise DeskError(EXIT_CONTRACT, f"spec 维度 {dim.get('dimension')} 缺字段 {k}")
        name = dim["dimension"]
        if not isinstance(name, str) or not DIM_RE.match(name):
            raise DeskError(EXIT_CONTRACT, f"spec 维度名不合法（只许字母数字 . _ -）：{name!r}")
        if name in seen:
            raise DeskError(EXIT_CONTRACT, f"spec 维度重复：{name}")
        seen.add(name)
        if not isinstance(dim["title"], str) or not isinstance(dim["question"], str):
            raise DeskError(EXIT_CONTRACT, f"spec 维度 {name} 的 title / question 须是字符串")
        for k in ("cards", "cues", "tests"):
            if not _str_list(dim[k]):
                raise DeskError(EXIT_CONTRACT, f"spec 维度 {name} 的 {k} 须是字符串列表")
        bad = [c for c in dim["cues"] if not c.strip() or any(ch in c for ch in CUE_FORBIDDEN)]
        if bad:
            raise DeskError(EXIT_CONTRACT, f"维度 {name} 的线索词不能为空、不能含 , | 或换行：{bad}")
        missing = [c for c in dim["cards"] if not (Path(kb) / "library/cards" / f"{c}.md").exists()]
        if missing:
            raise DeskError(EXIT_CONTRACT, f"维度 {name} 的 cards 不存在：{missing}")
    thr = d.get("threshold", 0.8)
    if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not 0 <= thr <= 1:
        raise DeskError(EXIT_CONTRACT, f"spec threshold 须是 0–1 的数：{thr!r}")
    return d


def render_diagnostics(d: dict) -> str:
    """``_diagnostics.md`` exactly as ``--apply`` writes it for spec ``d``."""
    return DIAG_HEADER + "".join(f"| {', '.join(x['cues'])} | {x['dimension']} |\n" for x in d["dimensions"])


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)


def _write_if_changed(path: Path, text: str) -> bool:
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    _atomic_write(path, data)
    return True


# --------------------------------------------------------------------------- apply / prompts / status


def apply(kb: Path, d: dict) -> None:
    toml = kb / "library.toml"
    t = toml.read_text(encoding="utf-8")
    if not re.search(r"^\[frameworks\]", t, flags=re.M):
        raise DeskError(EXIT_ENV, f"{toml} 没有 [frameworks] 段（不是 klode init 生成的 library.toml？）")
    t2 = re.sub(r"(\[frameworks\][^\n]*\n(?:[^\[]*?))enabled = false", r"\1enabled = true", t, count=1, flags=re.S)
    if t2 != t:
        _atomic_write(toml, t2.encode("utf-8"))
        print("library.toml：[frameworks] enabled = true")
    else:
        print("library.toml：[frameworks] 已开")
    syn = check_synthesis.syntheses_dir(kb)
    _write_if_changed(syn / "_diagnostics.md", render_diagnostics(d))
    print(f"_diagnostics.md：{len(d['dimensions'])} 行（由 spec 生成；改线索词改 spec 再 --apply，不要手改）")


def prompts(kb: Path, d: dict) -> None:
    out = kb / "_work/desk_prompts"
    out.mkdir(parents=True, exist_ok=True)
    for x in d["dimensions"]:
        txt = (
            f"任务类型变更：这次写的是**书桌维度 synthesis**，不是卡片。kb={kb}  dimension={x['dimension']}\n"
            f"规范：Read {SYNTHESIS_PROMPT}（格式与规则，照做）。\n"
            f"本维度标题：{x['title']}；回答：「{x['question']}」。\n"
            f"cards:（只读这些卡的 library/cards/<id>.md，锚点只能从里面逐字复制）\n{', '.join(x['cards'])}\n"
            f"写 {kb}/library/frameworks/_syntheses/{x['dimension']}.md（Write 会自动建目录），然后 "
            f"python3 -m klode_kit.build.check_synthesis {kb} {x['dimension']} 改到 PASS，回报一行。"
            f"跨书时如实并列各书说法，不调和。不改卡片、不碰 git。\n"
        )
        _write_if_changed(out / f"{x['dimension']}.txt", txt)
        print(f"===== {x['dimension']} =====\n{txt}")
    print(f"提示词已写到 {out}/（一维度一个 worker，并行派）")
    print("铁律 1：worker 交回（handback）之前不要碰它的 synthesis 文件——文件存在 ≠ 写完。")


def status(kb: Path, d: dict) -> int:
    syn = check_synthesis.syntheses_dir(kb)
    missing = [x["dimension"] for x in d["dimensions"] if not (syn / f"{x['dimension']}.md").exists()]
    for x in d["dimensions"]:
        print(f"{'✅' if x['dimension'] not in missing else '☐ '} {x['dimension']}")
    print("（只看文件在不在，不是完成判据：完成以 worker 回报 + --verify 为准）")
    return 1 if missing else 0


# --------------------------------------------------------------------------- verify


def holdout_gate(hit: int, total: int, *, holdout_min: int, pass_ratio: float) -> bool:
    """The desk gate: at least ``holdout_min`` holdout questions (and at least one) and ``hit/total >= pass_ratio``."""
    return total > 0 and total >= holdout_min and hit >= pass_ratio * total - 1e-9


def diagnose_top(klode: str, toml: Path, q: str) -> str | None:
    """The most relevant dimension ``klode --json diagnose`` routes ``q`` to, or None (B3: JSON, never prose)."""
    r = subprocess.run([klode, "--json", "-c", str(toml), "diagnose", q], capture_output=True, text=True,
                       timeout=120)
    try:
        value = json.loads(r.stdout)["value"]
        if not isinstance(value, list):
            raise TypeError("value is not a list")
        if not value:
            return None
        top = value[0][0]
        if not isinstance(top, str):
            raise TypeError("value[0][0] is not a string")
        return top
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        detail = (r.stderr or r.stdout).strip()[:200]
        raise DeskError(EXIT_ENV, f"klode --json diagnose 没有给出可解析的 JSON（exit {r.returncode}，{exc}）：{detail}")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_holdout(path: Path, d: dict) -> tuple[list[dict], bytes | None]:
    """``([{"q", "gold"}], raw bytes)``; ``([], None)`` when the file does not exist. DeskError(30) on bad lines."""
    if not path.is_file():
        return [], None
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise DeskError(EXIT_CONTRACT, f"留出集不是 UTF-8：{path}") from None
    dims = {x["dimension"] for x in d["dimensions"]}
    tune = {q for x in d["dimensions"] for q in x["tests"]}
    items: list[dict] = []
    seen: set[str] = set()
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            raise DeskError(EXIT_CONTRACT, f"{path.name}:{n} 不是 JSON") from None
        q = obj.get("q") if isinstance(obj, dict) else None
        gold = obj.get("gold") if isinstance(obj, dict) else None
        if not isinstance(q, str) or not q.strip() or not isinstance(gold, str):
            raise DeskError(EXIT_CONTRACT, f"{path.name}:{n} 须是 {{\"q\": 问题, \"gold\": 维度}}")
        if gold not in dims:
            raise DeskError(EXIT_CONTRACT, f"{path.name}:{n} 的维度 {gold!r} 不在 spec 里")
        if q in seen:
            raise DeskError(EXIT_CONTRACT, f"{path.name}:{n} 题目重复：{q}")
        if q in tune:
            raise DeskError(EXIT_CONTRACT, f"{path.name}:{n} 与调优集同题（留出集必须与调优集分开）：{q}")
        seen.add(q)
        items.append({"q": q, "gold": gold})
    return items, raw


def check_freeze(freeze_path: Path, raw: bytes | None, *, refreeze: bool,
                 reason: str | None) -> tuple[dict, dict | None]:
    """``(receipt freeze block, freeze document to write or None)``; DeskError(30) for an unfrozen change.

    No freeze yet: the current sha is frozen (``initial``). A damaged freeze file is refused unless ``--refreeze``.
    """
    if raw is None:
        return {"sha256": None, "initial": False, "refrozen": False, "reason": None}, None
    sha = _sha(raw)
    existing: Any = None
    if freeze_path.exists():
        try:
            existing = json.loads(freeze_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            existing = None
        if not _FREEZE_VALIDATOR.is_valid(existing):
            if not refreeze:
                raise DeskError(EXIT_CONTRACT, f"冻结记录损坏：{freeze_path}（不要手改；恢复它，或带 --refreeze --reason）")
            existing = None
    if existing is None:
        why = reason if refreeze and reason else "initial freeze"
        doc = {"schema": FREEZE_SCHEMA_ID, "sha256": sha, "history": [{"sha256": sha, "reason": why}]}
        return {"sha256": sha, "initial": True, "refrozen": refreeze, "reason": reason if refreeze else None}, doc
    if refreeze:
        doc = {"schema": FREEZE_SCHEMA_ID, "sha256": sha,
               "history": existing["history"] + [{"sha256": sha, "reason": reason}]}
        return {"sha256": sha, "initial": False, "refrozen": True, "reason": reason}, doc
    if existing["sha256"] != sha:
        raise DeskError(EXIT_CONTRACT,
                        f"留出集已改动（冻结 sha {existing['sha256'][:12]}… ≠ 当前 {sha[:12]}…）：留出集不拿来调线索词；"
                        "确属必要（如修错字）请带 --refreeze --reason \"<原因>\" 重新冻结")
    return {"sha256": sha, "initial": False, "refrozen": False, "reason": None}, None


def _rel(path: Path, kb: Path) -> str:
    try:
        return path.resolve().relative_to(kb).as_posix()
    except ValueError:
        return str(path.resolve())


def verify(kb: Path, d: dict, holdout: Path | None = None, *, refreeze: bool = False, reason: str | None = None,
           receipt: Path | None = None, holdout_min: int = 5, pass_ratio: float = 0.8) -> int:
    klode = shutil.which("klode")
    if not klode:
        raise DeskError(EXIT_ENV, "找不到 klode：需要 klode 0.5.x 在 PATH 上")
    toml = kb / "library.toml"
    holdout_path = Path(holdout) if holdout is not None else kb / "src" / "holdout.jsonl"
    items, raw = load_holdout(holdout_path, d)
    freeze_path = kb / "src" / FREEZE_NAME
    freeze_info, freeze_doc = check_freeze(freeze_path, raw, refreeze=refreeze, reason=reason)

    fails: list[str] = []
    rc_syn, lines = check_synthesis.run(kb)
    print("\n".join(lines))
    if rc_syn:
        fails.append("synthesis")
    r = subprocess.run([klode, "-c", str(toml), "check", "--strict"], capture_output=True, text=True, timeout=300)
    last = (r.stdout.strip().splitlines() or ["?"])[-1]
    print("klode check --strict:", last)
    strict_ok = "0 errors" in last
    if not strict_ok:
        fails.append("strict")
    diag = check_synthesis.syntheses_dir(kb) / "_diagnostics.md"
    current = diag.is_file() and diag.read_text(encoding="utf-8") == render_diagnostics(d)
    if not current:
        print("FAIL  _diagnostics.md 与 src/desk.json 不一致（手改过，或改 spec 后没重跑 --apply）：改 spec 再 --apply，不要手改")
        fails.append("diagnostics")

    def score(pairs: list[tuple[str, str]], tag: str) -> list[dict]:
        out = []
        for dim, q in pairs:
            got = diagnose_top(klode, toml, q)
            ok = got == dim
            print(f"{'PASS' if ok else 'FAIL'}  [{tag}] diagnose「{q}」→ {got or '∅'}（期望 {dim}）")
            out.append({"q": q, "expect": dim, "got": got, "ok": ok})
        return out

    tune = score([(x["dimension"], q) for x in d["dimensions"] for q in x["tests"]], "调优")
    hold = score([(h["gold"], h["q"]) for h in items], "留出")
    t_hit, t_total = sum(x["ok"] for x in tune), len(tune)
    h_hit, h_total = sum(x["ok"] for x in hold), len(hold)
    thr = d.get("threshold", 0.8)
    tune_ok = t_total > 0 and t_hit / t_total >= thr - 1e-9
    hold_ok = holdout_gate(h_hit, h_total, holdout_min=holdout_min, pass_ratio=pass_ratio)
    print(f"diagnose 调优集：{t_hit}/{t_total} = {t_hit / t_total if t_total else 0:.2f}（阈值 {thr}；只报告，不计入门禁）")
    short = f"；题数不足 {holdout_min}" if h_total < holdout_min else ""
    missing = "；留出集文件不存在" if raw is None else ""
    print(f"diagnose 留出集：{h_hit}/{h_total} = {h_hit / h_total if h_total else 0:.2f}"
          f"（门禁：≥{holdout_min} 题且 ≥ {pass_ratio:.2f}{short}{missing}）")
    if h_hit < h_total:
        print("留出集未命中只报告：不要拿它们调线索词（铁律 2）")
    if not hold_ok:
        fails.append("holdout")
    print("\n书桌验收：" + ("通过" if not fails else f"未通过（{len(fails)} 项：{', '.join(fails)}）"))

    doc = {"schema": RECEIPT_SCHEMA_ID, "status": "fail" if fails else "pass",
           "synthesis": {"pass": not rc_syn}, "strict": {"pass": strict_ok, "last_line": last},
           "diagnostics": {"current": current},
           "tune": {"hit": t_hit, "total": t_total, "threshold": thr, "ok": tune_ok, "results": tune},
           "holdout": {"path": _rel(holdout_path, kb), "sha256": None if raw is None else _sha(raw), "hit": h_hit,
                       "total": h_total, "min": holdout_min, "pass_ratio": pass_ratio, "pass": hold_ok,
                       "results": hold},
           "freeze": freeze_info}
    errors = list(_VALIDATOR.iter_errors(doc))
    if errors:  # pragma: no cover - a bug in this module, not an input problem
        raise ValueError(f"desk receipt violates {RECEIPT_SCHEMA_ID}: {errors[0].message}")
    if freeze_doc is not None:
        _atomic_write(freeze_path, (json.dumps(freeze_doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    out = Path(receipt) if receipt is not None else kb / RECEIPT_NAME
    _atomic_write(out, (json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return EXIT_FAIL if fails else 0


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="klode-kit desk-build", description=__doc__.splitlines()[0])
    ap.add_argument("kb", type=Path)
    ap.add_argument("--spec", type=Path)
    ap.add_argument("--holdout", "--gold", dest="holdout", type=Path,
                    help="frozen holdout set (default KB/src/holdout.jsonl; --gold is the original name)")
    ap.add_argument("--refreeze", action="store_true", help="with --verify: accept a changed holdout (needs --reason)")
    ap.add_argument("--reason", help="why the holdout was changed (recorded)")
    ap.add_argument("--receipt", type=Path, help=f"verify receipt (default KB/{RECEIPT_NAME})")
    ap.add_argument("--project", type=Path, help="data project with klode-kit.toml overrides")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true")
    g.add_argument("--prompts", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--status", action="store_true")
    try:
        a = ap.parse_args(argv)
        if a.refreeze and not a.verify:
            ap.error("--refreeze 只能与 --verify 一起用")
        if a.refreeze and not (a.reason and a.reason.strip()):
            ap.error("--refreeze 必须带 --reason \"<原因>\"")
        if a.reason is not None and not a.refreeze:
            ap.error("--reason 只与 --refreeze 一起用")
    except SystemExit as exc:
        return EXIT_USAGE if exc.code else 0
    kb = a.kb.resolve()
    try:
        d = load(kb, a.spec)
        if a.apply:
            apply(kb, d)
            return 0
        if a.prompts:
            prompts(kb, d)
            return 0
        if a.status:
            return status(kb, d)
        cfg = config_mod.load(a.project)
        return verify(kb, d, a.holdout, refreeze=a.refreeze, reason=a.reason, receipt=a.receipt,
                      holdout_min=cfg["desk.holdout_min"], pass_ratio=cfg["desk.holdout_pass"])
    except DeskError as exc:
        print(f"desk-build: {exc}", file=sys.stderr)
        return exc.code
    except config_mod.ConfigError as exc:
        print(f"desk-build: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit desk-build``
    raise SystemExit(main())
