"""``klode-kit kb-ocr``: OCR a directory of images on the configured routes (WI-04b; rewrite of 189
``src/ocr/run_batch.py``; design 02 r2.2-A, r2.3, r2.4, r3.2/R3-06, r5).

Usage::

    klode-kit kb-ocr --batch <inbox dir> [--project <data project>] [--routes vision,mlx,claude,codex] [--redo]

Layout (``<batch>`` is ``batch_id``, the set hash of the images)::

    work/.lock                                    project lock (fcntl, pid + batch) - one kb-ocr at a time
    work/<batch>/manifest.jsonl                   one line per inbox image (contract ``manifest``)
    work/<batch>/derived.jsonl                    one line per page (contract ``derived``; ``transform.rotate``)
    work/<batch>/derived/<key>.png                the upright page, only when it had to be turned
    work/<batch>/ocr/<engine>/<cfg_fp>.t<seq>/<key>.jsonl   ``ocr_line`` rows of one route on one page
    work/<batch>/ocr/<engine>/<cfg_fp>.t<seq>/receipt.json  attempt receipt (contract ``receipt``)
    work/<batch>/receipts/ocr.json                step receipt (contract ``receipt``)
    work/<batch>/receipt.json                     batch receipt ``klode-kit.ocr_batch/1`` (below)

Pages are keyed ``p001, p002, ...``: the position of each distinct image in the inbox sorted by file name
(an image is always page 1 of its document, so ``page_id`` is ``<doc_id>/p001``).

Rules

- Inbox (r5): no images -> exit 2 ``no images`` and no directory is created; non-image entries are skipped
  and listed in ``skipped[]`` and on stderr.
- Attempts (R3-06): per route ``cfg_fp`` over the effective configuration, prompt/schema/model/effort,
  adapter version and the set of ``der_sha256``. ``--redo`` always claims a new attempt. Otherwise the latest
  attempt is reused when its receipt is ``done`` and its three hashes match (``inputs_hash``,
  ``config_hash`` and every ``outputs[].sha256`` on disk); an attempt without a receipt is one a killed run
  left behind and is continued in place (finished pages are not called again); any other attempt (failed,
  or changed on disk) leads to a new one. Older attempts are never written to.
- Per page and route: up to ``1 + route_retry`` calls; the terminal state is ``done``, ``route_failed`` or
  ``route_missing_page`` (E21: a route that returns no page is recorded, never dropped). Non-done pairs are
  listed in ``issues`` (``<state>:<engine>:<key>``) and as page-level ``uncertain`` items (r2.3), and printed.
  Rows are accepted only if they pass ``ocr_line`` and belong to the page (``page_id``, ``der_sha256``,
  ``line_idx`` 0..n-1).
- Routes: ``vision`` and ``mlx`` (MLX -> alt prompt -> tesseract) are the WI-04a engines. ``claude`` and
  ``codex`` read ``config/routes_enabled.json``: disabled -> ``route_failed`` (``route_disabled``) with zero
  calls (r3.3); enabled -> the cloud adapters are WI-06a/b, until then ``route_failed`` (``route_unavailable``)
  with zero calls. A local engine that is not installed is likewise ``route_unavailable``. Exit 0 means the
  run finished; the receipt tells which pairs are not done.
- Durability: every file is written to a temporary name in its directory and moved into place; crash
  points (matrix section 0.3) separate every two durable writes. A resumed run first removes temporary
  files a killed run left (``.<name>.<hex>.tmp``).
- Exit codes: 0 finished; 2 usage (bad arguments, inbox missing, no images); 40 configuration error or
  the project lock is held by another run.

Test seam: ``KLODE_KIT_ENGINE_REPLAY=<dir>`` replaces every route call by recorded rows
(``<dir>/<engine>/<key>.jsonl``; ``<key>.fail`` injects failures - empty: always, ``n``: the first n calls of
this process; neither: no page) and appends each call to ``$KLODE_KIT_FAKE_LOG``. Honoured only when the test
seams are active (``KLODE_KIT_TEST_SEAMS=1`` and ``KLODE_KIT_REAL != 1``); otherwise stderr says it is ignored.

Batch receipt ``klode-kit.ocr_batch/1`` (no wall-clock fields, so equal runs give equal bytes)::

    {schema, batch_id, routes, route_retry, status: done|incomplete, skipped: [{path, reason}],
     pages: [{key, doc_id, page_id, img_sha256, der_sha256, rotate, source,
              routes: {engine: {status, run_id?, file?, tries?, error?}}, uncertain: [...]}],
     attempts: {engine: {run_id, dir, status}}, issues: ["<state>:<engine>:<key>", ...]}
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from klode_kit import __version__, receipts
from klode_kit import config as config_mod
from klode_kit.contract import ids, schema
from klode_kit.ocr import engines as E
from klode_kit.ocr.manifest import Batch, InboxError, Page, dumps_jsonl, plan, scan
from klode_kit.seams import crashpoint, seams_active

RECEIPT_SCHEMA = "klode-kit.ocr_batch/1"
ROUTE_NAMES = ("vision", "mlx", "claude", "codex")
CLOUD_ROUTES = ("claude", "codex")
REPLAY_VAR = "KLODE_KIT_ENGINE_REPLAY"
ADAPTER_VERSION = "run_batch/1"
LOCK_NAME = ".lock"
USAGE_EXIT = 2
ENV_EXIT = 40
TERMINAL = ("done", "route_failed", "route_missing_page")
CRASHPOINTS = (
    "run_batch.mkdir", "run_batch.lock", "run_batch.cleanup", "run_batch.meta_tmp", "run_batch.meta_replaced",
    "run_batch.before_replace", "run_batch.page_done", "run_batch.receipt_written",
)
_TMP = re.compile(r"\..+\.tmp")


class RouteError(Exception):
    """One call of a route failed (retried up to ``route_retry`` times)."""


class PageMissing(Exception):
    """The route answered without the page (``route_missing_page``)."""


class LockBusy(Exception):
    """Another run holds ``work/.lock``."""


# --------------------------------------------------------------------------- small helpers


def _sha(data: bytes | str) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def _canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _packaged(package: str, name: str) -> bytes:
    return (resources.files(package) / name).read_bytes()


def _fsync_dir(directory: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _ensure_dir(directory: Path) -> None:
    missing = []
    cur = directory
    while not cur.is_dir():
        missing.append(cur)
        cur = cur.parent
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            if not d.is_dir():
                raise
        crashpoint("run_batch.mkdir")


def _atomic(path: Path, data: bytes, before: str, after: str) -> None:
    _ensure_dir(path.parent)
    tmp = path.parent / f".{path.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
        finally:
            os.close(fd)
        crashpoint(before)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.remove(tmp)
        raise
    _fsync_dir(path.parent)
    crashpoint(after)


def _write_meta(path: Path, data: bytes) -> None:
    if path.is_file() and path.read_bytes() == data:
        return
    _atomic(path, data, "run_batch.meta_tmp", "run_batch.meta_replaced")


def _write_receipt(path: Path, data: dict) -> None:
    receipts.write(path, data, "receipt")
    crashpoint("run_batch.receipt_written")


def _cleanup(batch_dir: Path) -> None:
    """Remove the temporary files a killed run left inside the batch directory."""
    if not batch_dir.is_dir():
        return
    for dirpath, _dirs, files in os.walk(batch_dir):
        for name in sorted(files):
            if _TMP.fullmatch(name):
                os.remove(Path(dirpath) / name)
                crashpoint("run_batch.cleanup")


def _lock(work: Path, batch: str) -> int:
    path = work / LOCK_NAME
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.pread(fd, 128, 0).decode("utf-8", "replace").split()
        os.close(fd)
        who = f"pid {holder[0]}, batch {holder[1]}" if len(holder) >= 2 else "unknown holder"
        raise LockBusy(f"another kb-ocr holds {path} ({who})") from None
    stamp = f"{os.getpid()} {batch}".ljust(63)[:63] + "\n"
    os.pwrite(fd, stamp.encode("utf-8"), 0)  # fixed width: no truncate needed
    crashpoint("run_batch.lock")
    return fd


# --------------------------------------------------------------------------- routes


@dataclass
class Route:
    name: str
    model: str
    effort: str
    prompt_sha: str
    schema_sha: str
    problem: str | None = None
    call: Callable[[Page], list[dict]] | None = None
    engines: tuple[str, ...] = ()


@dataclass
class _Replay:
    root: Path
    counts: dict[tuple[str, str], int] = field(default_factory=dict)

    def caller(self, engine: str) -> Callable[[Page], list[dict]]:
        def call(page: Page) -> list[dict]:
            log = os.environ.get("KLODE_KIT_FAKE_LOG")
            if log:
                entry = {"tool": engine, "page": page.key, "der_sha256": page.der_sha256, "pid": os.getpid()}
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry) + "\n")
            n = self.counts[(engine, page.key)] = self.counts.get((engine, page.key), 0) + 1
            base = self.root / engine / page.key
            fail = base.with_suffix(".fail")
            if fail.is_file():
                limit = fail.read_text(encoding="utf-8").strip()
                if not limit or n <= int(limit):
                    raise RouteError(f"replay: injected failure on call {n}")
            rows = base.with_suffix(".jsonl")
            if not rows.is_file():
                raise PageMissing(f"replay: no {engine} output for {page.key}")
            return [json.loads(line) for line in rows.read_text(encoding="utf-8").splitlines() if line.strip()]

        return call


def _vision_call(page: Page) -> list[dict]:
    lines = E.OcrMacEngine().ocr_page(page.derived_path, page_id=page.page_id, der_sha256=page.der_sha256)
    if not lines:
        raise PageMissing("vision returned no lines")
    return lines


def _mlx_caller(max_ratio: float) -> Callable[[Page], list[dict]]:
    mlx, fallback = E.MlxEngine(), E.TesseractEngine()

    def call(page: Page) -> list[dict]:
        res = E.ocr_page(page.derived_path, page_id=page.page_id, der_sha256=page.der_sha256, mlx=mlx,
                         fallback=fallback, max_ratio=max_ratio)
        if not res.ok or res.lines is None:
            raise RouteError("; ".join(u.get("fix_action", u["reason"]) for u in res.uncertain) or res.status)
        if not res.lines:
            raise PageMissing("route B returned no lines")
        return res.lines

    return call


def _local_problem(name: str) -> str | None:
    if name == "vision":
        if importlib.util.find_spec("ocrmac") is None:
            return "route_unavailable: ocrmac is not installed (dependency group ocr-local, macOS)"
        return None
    if importlib.util.find_spec("mlx_vlm") is None:
        return "route_unavailable: mlx-vlm is not installed (dependency group ocr-local, Apple silicon)"
    status = E.mlx_weights_status()
    return None if status.ok else f"route_unavailable: {status.problem}"


def routes_enabled_default() -> dict[str, bool]:
    data = json.loads(_packaged("klode_kit.config", "routes_enabled.json"))
    return {k: bool(v) for k, v in data.get("routes_enabled", {}).items()}


def build_routes(names: list[str], cfg: config_mod.Config, enabled: Mapping[str, bool],
                 replay: _Replay | None) -> dict[str, Route]:
    line_schema = _sha(_packaged("klode_kit.schemas", "ocr_line.json"))
    cloud_schema = _sha(_packaged("klode_kit.schemas", "ocr_cloud_output.json"))
    prompt = _sha(_packaged("klode_kit.prompts", "ocr_read.md"))
    routes: dict[str, Route] = {}
    for name in names:
        if name == "vision":
            route = Route(name, "apple-vision/ocrmac", "none", _sha(_canonical(E.OcrMacEngine().params)),
                          line_schema, engines=("vision",))
        elif name == "mlx":
            params = {"mlx": E.MlxEngine().params, "fallback": E.TesseractEngine().params,
                      "repeat_ratio_max": cfg["repeat_ratio_max"]}
            route = Route(name, f"{E.MLX_MODEL}@{E.MLX_SNAPSHOT}", "none", _sha(_canonical(params)), line_schema,
                          engines=("mlx", "tesseract"))
        else:
            route = Route(name, cfg[f"{name}.model"], cfg[f"{name}.effort"], prompt, cloud_schema, engines=(name,))
        if name in CLOUD_ROUTES and not enabled.get(name, False):
            route.problem = f"route_disabled: {name} is disabled in config/routes_enabled.json"
        elif replay is not None:
            route.call = replay.caller(name)
        elif name in CLOUD_ROUTES:
            adapter = "WI-06a" if name == "claude" else "WI-06b"
            route.problem = f"route_unavailable: the {name} adapter is not implemented yet ({adapter})"
        else:
            route.problem = _local_problem(name)
            if route.problem is None:
                route.call = _vision_call if name == "vision" else _mlx_caller(cfg["repeat_ratio_max"])
        routes[name] = route
    return routes


# --------------------------------------------------------------------------- one route


def _check_rows(rows: Any, page: Page, route: Route) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise PageMissing(f"{route.name} returned no lines for {page.key}")
    for idx, row in enumerate(rows):
        problems = schema.issues("ocr_line", row)
        if problems:
            raise RouteError(f"line {idx} violates ocr_line: {problems[0].path or '<root>'}: {problems[0].message}")
        if row["engine"] not in route.engines:
            raise RouteError(f"line {idx}: engine {row['engine']!r} is not route {route.name}")
        if row["page_id"] != page.page_id or row["der_sha256"] != page.der_sha256:
            raise RouteError(f"line {idx} belongs to {row['page_id']} / {row['der_sha256'][:12]}, not {page.key}")
        if row["line_idx"] != idx:
            raise RouteError(f"line {idx} has line_idx {row['line_idx']}")
    return rows


def _call(route: Route, page: Page, retry: int) -> tuple[str, int, str | None, list[dict] | None]:
    assert route.call is not None
    last: tuple[str, str] = ("route_failed", "no call made")
    for tries in range(1, retry + 2):
        try:
            return "done", tries, None, _check_rows(route.call(page), page, route)
        except PageMissing as exc:
            last = ("route_missing_page", str(exc))
        except Exception as exc:  # an engine failure is a failed call, not a crash of the batch
            last = ("route_failed", f"{type(exc).__name__}: {exc}")
    return last[0], retry + 1, last[1], None


def _existing_rows(path: Path, page: Page) -> bool:
    """A page file a killed run already put in place (complete by construction) for this very page."""
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        return False
    return bool(rows) and all(r.get("page_id") == page.page_id and r.get("der_sha256") == page.der_sha256
                              for r in rows)


def _attempt_dirs(engine_dir: Path, fp: str) -> list[tuple[int, Path]]:
    if not engine_dir.is_dir():
        return []
    pat = re.compile(re.escape(fp) + r"\.t([1-9][0-9]*)")
    found = [(int(m.group(1)), p) for p in engine_dir.iterdir() if p.is_dir() and (m := pat.fullmatch(p.name))]
    return sorted(found)


def _reusable(directory: Path, inputs_hash: str, config_hash: str, keys: list[str]) -> bool:
    try:
        rec = receipts.read(directory / "receipt.json", "receipt")
    except (OSError, receipts.ReceiptInvalid):
        return False
    if rec["status"] != "done" or rec["inputs_hash"] != inputs_hash or rec["config_hash"] != config_hash:
        return False
    outputs = {o["path"]: o["sha256"] for o in rec["outputs"]}
    if set(outputs) != {f"{k}.jsonl" for k in keys}:
        return False
    for rel, digest in outputs.items():
        p = directory / rel
        if not p.is_file() or _sha(p.read_bytes()) != digest:
            return False
    return True


@dataclass
class RouteRun:
    run_id: str | None
    directory: Path | None
    status: str
    pages: dict[str, dict]


def _run_route(route: Route, batch: Batch, batch_dir: Path, cfg: config_mod.Config, redo: bool,
               out: TextIO) -> RouteRun:
    if route.problem is not None:
        return RouteRun(None, None, "failed", {p.key: {"status": "route_failed", "error": route.problem}
                                                for p in batch.pages})
    ders = [p.der_sha256 for p in batch.pages]
    fp = ids.cfg_fp(cfg, prompt_sha=route.prompt_sha, schema_sha=route.schema_sha, model=route.model,
                    effort=route.effort, adapter_code_version=f"{__version__}/{ADAPTER_VERSION}", der_sha256s=ders)
    inputs_hash = _sha(_canonical([[p.key, p.page_id, p.der_sha256] for p in batch.pages]))
    config_hash = _sha(_canonical({"config": json.loads(cfg.canonical()), "route": route.name, "model": route.model,
                                   "effort": route.effort, "prompt_sha": route.prompt_sha,
                                   "schema_sha": route.schema_sha, "adapter": f"{__version__}/{ADAPTER_VERSION}"}))
    keys = [p.key for p in batch.pages]
    existing = _attempt_dirs(batch_dir / "ocr" / route.name, fp)
    seq, directory, mode = (existing[-1][0], existing[-1][1], "") if existing else (0, None, "")
    if directory is not None and not redo:
        if not (directory / "receipt.json").exists():
            mode = "continue"
        elif _reusable(directory, inputs_hash, config_hash, keys):
            mode = "reuse"
    if mode == "":
        attempt = ids.allocate_attempt(batch_dir, route.name, fp)
        seq, directory = attempt.seq, attempt.path
    assert directory is not None
    run_id = ids.run_id(batch_dir.name, route.name, fp, ids.attempt_id(seq))
    rel = f"ocr/{route.name}/{directory.name}"
    if mode == "reuse":
        print(f"kb-ocr: {route.name}: reusing {rel} (done, hashes match)", file=out)
        return RouteRun(run_id, directory, "done", {k: {"status": "done", "run_id": run_id,
                                                         "file": f"{rel}/{k}.jsonl"} for k in keys})
    if mode == "continue":
        print(f"kb-ocr: {route.name}: continuing interrupted attempt {rel}", file=out)
    retry = cfg["route_retry"]
    started = _now()
    results: dict[str, dict] = {}
    outputs = []
    for page in batch.pages:
        target = directory / f"{page.key}.jsonl"
        if not (mode == "continue" and target.is_file() and _existing_rows(target, page)):
            state, tries, error, rows = _call(route, page, retry)
            if state != "done":
                results[page.key] = {"status": state, "run_id": run_id, "tries": tries, "error": error}
                continue
            assert rows is not None
            _atomic(target, dumps_jsonl(rows), "run_batch.before_replace", "run_batch.page_done")
        results[page.key] = {"status": "done", "run_id": run_id, "file": f"{rel}/{page.key}.jsonl"}
        outputs.append({"path": target.name, "sha256": _sha(target.read_bytes())})
    status = "done" if all(r["status"] == "done" for r in results.values()) else "failed"
    _write_receipt(directory / "receipt.json", {
        "schema": "klode-kit.receipt/1", "step": f"ocr_{route.name}", "status": status, "started": started,
        "ended": _now(), "inputs_hash": inputs_hash, "config_hash": config_hash,
        "config_version": cfg.config_version, "outputs": sorted(outputs, key=lambda o: o["path"]),
        "model": route.model, "effort": route.effort, "exit_code": 0 if status == "done" else 1,
    })
    return RouteRun(run_id, directory, status, results)


# --------------------------------------------------------------------------- the batch


FIX = {
    "route_failed": "rerun kb-ocr (a new attempt is made for this route), or choose 'continue with the other "
                    "routes' on the confirm page",
    "route_missing_page": "rerun kb-ocr for this route, or choose 'continue with the other routes' on the "
                          "confirm page",
}


def _uncertain(page: Page, engine: str, result: dict) -> dict:
    state = result["status"]
    fix = FIX[state]
    if result.get("error", "").startswith(("route_disabled", "route_unavailable")):
        fix = f"{result['error']}; enable or install the route, then rerun kb-ocr"
    return {"id": f"{page.page_id}:{engine}:{state}", "reason": state, "scope": "page", "engine": engine,
            "fix_action": fix}


def _batch_receipt(batch: Batch, project: Path, routes: list[str], retry: int, skipped: list[dict],
                   runs: dict[str, RouteRun], batch_dir: Path) -> dict:
    pages, issues = [], []
    for page, der in zip(batch.pages, batch.derived):
        per = {r: runs[r].pages[page.key] for r in routes}
        unc = [_uncertain(page, r, per[r]) for r in routes if per[r]["status"] != "done"]
        issues += [f"{per[r]['status']}:{r}:{page.key}" for r in routes if per[r]["status"] != "done"]
        pages.append({"key": page.key, "doc_id": page.doc_id, "page_id": page.page_id,
                      "img_sha256": page.img_sha256, "der_sha256": page.der_sha256, "rotate": page.rotate,
                      "source": next(m["path"] for m in batch.manifest if m["img_sha256"] == page.img_sha256),
                      "derived": der["path"], "routes": per, "uncertain": unc})
    attempts = {r: {"run_id": run.run_id, "dir": run.directory.relative_to(batch_dir).as_posix(), "status": run.status}
                for r, run in runs.items() if run.directory is not None and run.run_id is not None}
    return {"schema": RECEIPT_SCHEMA, "batch_id": batch.batch_id, "routes": routes, "route_retry": retry,
            "status": "done" if not issues else "incomplete", "skipped": skipped, "pages": pages,
            "attempts": attempts, "issues": issues}


def run(project: Path, inbox: Path, routes: list[str] | None = None, *, redo: bool = False,
        routes_enabled: Mapping[str, bool] | None = None, out: TextIO | None = None) -> int:
    out = sys.stderr if out is None else out
    cfg = config_mod.load(project)
    names = list(cfg["routes"]) if routes is None else routes
    unknown = [n for n in names if n not in ROUTE_NAMES]
    if unknown or not names:
        print(f"kb-ocr: unknown route(s): {', '.join(unknown) or '(none given)'}; "
              f"choose from {', '.join(ROUTE_NAMES)}", file=out)
        return USAGE_EXIT
    try:
        images, skipped = scan(inbox)
    except InboxError as exc:
        print(f"kb-ocr: {exc}", file=out)
        return USAGE_EXIT
    for s in skipped:
        print(f"kb-ocr: skipped {s['path']} ({s['reason']})", file=out)
    if not images:
        print(f"kb-ocr: no images in {inbox}", file=out)
        return USAGE_EXIT
    replay = None
    if os.environ.get(REPLAY_VAR):
        if seams_active():
            replay = _Replay(Path(os.environ[REPLAY_VAR]))
        else:
            print(f"klode-kit: 测试缝变量已忽略（未处于测试模式：需要 KLODE_KIT_TEST_SEAMS=1 且 KLODE_KIT_REAL!=1）："
                  f"{REPLAY_VAR}", file=out)
    enabled = routes_enabled_default() if routes_enabled is None else dict(routes_enabled)
    work = project / "work"
    batch = plan(images, project, work)
    specs = build_routes(names, cfg, enabled, replay)
    _ensure_dir(work)
    try:
        lock = _lock(work, batch.batch_id)
    except LockBusy as exc:
        print(f"kb-ocr: {exc}", file=out)
        return ENV_EXIT
    try:
        batch_dir = work / batch.batch_id
        _ensure_dir(batch_dir)
        _cleanup(batch_dir)
        for page in batch.pages:
            if page.derived_bytes is not None:
                _write_meta(page.derived_path, page.derived_bytes)
        _write_meta(batch_dir / "manifest.jsonl", dumps_jsonl(batch.manifest))
        _write_meta(batch_dir / "derived.jsonl", dumps_jsonl(batch.derived))
        started = _now()
        runs = {name: _run_route(specs[name], batch, batch_dir, cfg, redo, out) for name in names}
        rec = _batch_receipt(batch, project, names, cfg["route_retry"], skipped, runs, batch_dir)
        outputs = [{"path": n, "sha256": _sha((batch_dir / n).read_bytes())} for n in ("manifest.jsonl", "derived.jsonl")]
        outputs += [{"path": r["file"], "sha256": _sha((batch_dir / r["file"]).read_bytes())}
                    for p in rec["pages"] for r in p["routes"].values() if r["status"] == "done"]
        _write_receipt(batch_dir / "receipts" / "ocr.json", {
            "schema": "klode-kit.receipt/1", "step": "ocr", "status": "done", "started": started, "ended": _now(),
            "inputs_hash": _sha(dumps_jsonl(batch.manifest)), "config_hash": cfg.sha256(),
            "config_version": cfg.config_version, "outputs": outputs, "exit_code": 0,
        })
        _write_meta(batch_dir / "receipt.json",
                    (json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(lock)
    for page in rec["pages"]:
        for r in names:
            res = page["routes"][r]
            if res["status"] != "done":
                print(f"kb-ocr: {res['status']}:{r}:{page['key']} ({res.get('error')})", file=out)
    print(f"kb-ocr: batch {batch.batch_id}: {len(batch.pages)} page(s), routes {','.join(names)}, "
          f"{len(rec['issues'])} issue(s); receipt {(batch_dir / 'receipt.json')}", file=out)
    return 0


def main(argv: list[str] | None = None, *, routes_enabled: Mapping[str, bool] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit kb-ocr", description="OCR a directory of images on four routes.")
    parser.add_argument("--batch", required=True, type=Path, help="inbox directory with the images")
    parser.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    parser.add_argument("--routes", default=None, help="comma-separated subset of vision,mlx,claude,codex")
    parser.add_argument("--redo", action="store_true", help="always start new attempts (never reuse done ones)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    project = Path.cwd() if args.project is None else args.project
    routes = None
    if args.routes is not None:
        routes = list(dict.fromkeys(r.strip() for r in args.routes.split(",") if r.strip()))
    try:
        return run(project, args.batch, routes, redo=args.redo, routes_enabled=routes_enabled)
    except config_mod.ConfigError as exc:
        print(f"kb-ocr: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit kb-ocr``
    sys.exit(main())
