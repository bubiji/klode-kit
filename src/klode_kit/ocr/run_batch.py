"""``klode-kit kb-ocr``: OCR a directory of images on the configured routes (WI-04b; rewrite of 189
``src/ocr/run_batch.py``; design 02 r2.2-A, r2.3, r2.4, r3.2/R3-06, r5).

Usage::

    klode-kit kb-ocr --batch <inbox dir or PDF> [--project <data project>] [--routes vision,mlx,claude,codex] [--redo]
                       [--type lab|rx]

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
  calls (r3.3). A
  local engine that is not installed is likewise ``route_unavailable``. Exit 0 means the run finished; the
  receipt tells which pairs are not done.
- Cloud routes (claude: WI-06a ``adapters_claude.read_page``; codex: WI-06b ``adapters_codex.read_page``; both
  through ``send``): only WI-05a masked images are sent -
  for each page the last ``_masked/`` line of ``derived.jsonl`` whose sha is in ``_masked/MANIFEST.jsonl``; a page
  without one is ``route_failed`` (``route_unavailable``) with zero calls, and so is the whole route when the batch
  has none. Its rows carry the masked image's ``der_sha256``. ``parse_error`` (malformed output twice) is a page
  state of its own and is not retried again. A sender refusal stops the route: 61 (G2 gate, isolation) ->
  the page and every later page ``route_failed`` (``isolation_refused: <reason>``); 60 (privacy) -> also every
  later cloud route, and kb-ocr exits 60. A project that lowers ``codex.effort`` below the lock (``max``) makes the
  codex route ``route_failed`` (``effort_locked: ...``) with zero calls.
- Cloud budget (r2.6): one ``cloud_budget.Ledger`` per batch with ``cloud.max_cost_usd_per_batch``, persisted in
  ``receipts/cloud_ledger.json`` so reruns of the batch keep counting. Every call is reserved before dispatch;
  once a reservation is refused, no cloud call follows in this batch: unsent pages are ``route_failed``
  (``budget_exceeded``) and kb-ocr exits 50. The batch receipt then has
  ``cloud: {limit_usd, spent_usd, calls, stop: null|"budget"|"privacy"}``. Page receipts of the adapter and the
  sender go to ``receipts/cloud/``. Stored isolation receipts: ``--isolation-receipt ROUTE=PATH``, default
  ``<project>/work/isolation/<route>/isolation_receipt.json``.
- Durability: every file is written to a temporary name in its directory and moved into place; crash
  points (matrix section 0.3) separate every two durable writes. A resumed run first removes temporary
  files a killed run left (``.<name>.<hex>.tmp``).
- Exit codes: 0 finished; 2 usage (bad arguments, inbox missing, no images); 40 configuration error or
  the project lock is held by another run; 50 the cloud budget stopped the batch; 60 the sender refused an
  image for privacy (receipts are written in both cases).

Test seam: ``KLODE_KIT_ENGINE_REPLAY=<dir>`` replaces every route call by recorded rows
(``<dir>/<engine>/<key>.jsonl``; ``<key>.fail`` injects failures - empty: always, ``n``: the first n calls of
this process; neither: no page) and appends each call to ``$KLODE_KIT_FAKE_LOG``. Honoured only when the test
seams are active (``KLODE_KIT_TEST_SEAMS=1`` and ``KLODE_KIT_REAL != 1``); otherwise stderr says it is ignored.

PDF input (WI-04c; design 02 r2.4 "文字层 PDF"): ``--batch`` may be a PDF file, and ``.pdf`` files in an inbox
are documents. Needs poppler (``pdfinfo``, ``pdftotext``, ``pdftoppm``): a missing tool is exit 40 naming it,
before anything is written. Every page is read with ``pdftotext -bbox-layout``, classified ``text`` / ``empty``
/ ``garbled`` (``text_layer.min_chars`` / ``garbage_max``, see ``textlayer``) and rendered at
``textlayer.DEFAULT_DPI`` as its derived image. ``text`` pages never go to the routes (their route state is
``skipped``); ``empty`` and ``garbled`` pages do, like images. Outputs::

    work/<batch>/text/<key>.jsonl             pdftext ``ocr_line`` rows of a ``text`` page
    work/<batch>/text/<doc_id>_whole.txt      the PDF's text pages as one text (``text_to_whole``)
    work/<batch>/page_map.jsonl               per PDF page: page_id, key, class, dpi, size_px, whole,
                                              first_line, n_lines

A PDF that cannot be read stops the batch before any extraction is written: exit 2, stderr and the batch
receipt (``status: failed``, ``errors: [{path, reason: pdf_unreadable, detail}]``) name it.

Batch receipt ``klode-kit.ocr_batch/1`` (no wall-clock fields, so equal runs give equal bytes)::

    {schema, batch_id, routes, route_retry, status: done|incomplete|failed, skipped: [{path, reason}],
     text_layer: true|false|"partial", routes_skipped: [engine, ...],
     pages: [{key, doc_id, page_id, img_sha256, der_sha256, rotate, source, text_class?, text_file?,
              routes: {engine: {status, run_id?, file?, tries?, error?, reason?}}, uncertain: [...]}],
     attempts: {engine: {run_id, dir, status}}, issues: ["<state>:<engine>:<key>", ...], errors?: [...],
     cloud?: {limit_usd, spent_usd, calls, stop}}

``text_layer`` is true when every page is a PDF ``text`` page, ``"partial"`` when some are, false otherwise;
``routes_skipped`` lists the requested routes when no page needs them (U4), else is empty.

WI-13c, optional increments of the batch receipt: ``route_stats: {engine: {elapsed_ms, failed_pages}}`` for every
requested route (``elapsed_ms``: non-negative integer, measured around the route and stored in the attempt receipt, so a
reused attempt reports the same number and equal runs give equal bytes; a replayed run measures 0 with a logical
clock; ``failed_pages``: pages whose state on that route is not done/skipped); ``record_type`` (``lab`` | ``rx``) at
the top level and on every page when ``--type`` is given. ``record_type`` is sticky: a rerun without ``--type`` keeps
the one in the receipt, a rerun with ``--type`` replaces it. The writeback prefers the page's ``record_type`` over
what the page text says (``writeback._page_meta``), and the views follow the record's type.
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
import time
from dataclasses import dataclass, field
from collections import Counter
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Callable, Mapping, TextIO

from klode_kit import __version__, receipts
from klode_kit import config as config_mod
from klode_kit.audit import mask
from klode_kit.contract import ids, schema
from klode_kit.ocr import adapters_claude, adapters_codex
from klode_kit.ocr import engines as E
from klode_kit.ocr import textlayer
from klode_kit.ocr.cloud_budget import CALL_RESERVE_USD, BudgetExceeded, Ledger
from klode_kit.ocr.send import PRIVACY_EXIT, SendRefused
from klode_kit.ocr.manifest import Batch, InboxError, Page, Source, dumps_jsonl, plan, scan
from klode_kit.ocr.text_to_whole import text_to_whole
from klode_kit.seams import crashpoint, seams_active

RECEIPT_SCHEMA = "klode-kit.ocr_batch/1"
ROUTE_NAMES = ("vision", "mlx", "claude", "codex")
RECORD_TYPES = ("lab", "rx")
CLOUD_ROUTES = ("claude", "codex")
REPLAY_VAR = "KLODE_KIT_ENGINE_REPLAY"
ADAPTER_VERSION = "run_batch/1"
LOCK_NAME = ".lock"
USAGE_EXIT = 2
ENV_EXIT = 40
TERMINAL = ("done", "route_failed", "route_missing_page", "parse_error")
BUDGET_EXIT = 50
LEDGER_FILE = "receipts/cloud_ledger.json"
CLOUD_RECEIPTS = "receipts/cloud"
NOT_MASKED = "cloud routes send only _masked/ images - run the masker (WI-05a) on this batch, then rerun kb-ocr"
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


class NotMasked(Exception):
    """No masked image for this page: a cloud route makes no call (``route_failed``, ``route_unavailable``)."""


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


def _masked_lines(path: Path, planned: list[dict]) -> list[dict]:
    """Lines ``mask.mask_batch`` registered for masked images (``path`` under ``_masked/``, WI-05b), in file order,
    so that a rerun keeps them after the planned lines. An unreadable file keeps nothing (it is rewritten)."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    have = {r["der_sha256"] for r in planned}
    kept = []
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        rel = row.get("path") if isinstance(row, dict) else None
        if (isinstance(rel, str) and not os.path.isabs(rel) and Path(rel).parts[:1] == ("_masked",)
                and row.get("der_sha256") not in have):
            have.add(row.get("der_sha256"))
            kept.append(row)
    return kept


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
    cloud: bool = False  # a cloud adapter is attached once derived.jsonl is written (WI-06a)
    masked: dict[str, str] = field(default_factory=dict)  # page_id -> der_sha256 of the masked image sent


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
            route.cloud = True
        else:
            route.problem = _local_problem(name)
            if route.problem is None:
                route.call = _vision_call if name == "vision" else _mlx_caller(cfg["repeat_ratio_max"])
        routes[name] = route
    return routes


# --------------------------------------------------------------------------- one route


def _ders(route: Route, page: Page) -> set[str]:
    """Images a route's rows of ``page`` may name: the upright page, or the masked image a cloud route sent."""
    return {page.der_sha256} | ({route.masked[page.page_id]} if page.page_id in route.masked else set())


def _check_rows(rows: Any, page: Page, route: Route) -> list[dict]:
    if not isinstance(rows, list) or not rows:
        raise PageMissing(f"{route.name} returned no lines for {page.key}")
    for idx, row in enumerate(rows):
        problems = schema.issues("ocr_line", row)
        if problems:
            raise RouteError(f"line {idx} violates ocr_line: {problems[0].path or '<root>'}: {problems[0].message}")
        if row["engine"] not in route.engines:
            raise RouteError(f"line {idx}: engine {row['engine']!r} is not route {route.name}")
        if row["page_id"] != page.page_id or row["der_sha256"] not in _ders(route, page):
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
        except (BudgetExceeded, SendRefused):
            raise  # stops the route (and for 50/60 the batch's cloud sending): handled by _run_route
        except NotMasked as exc:
            return "route_failed", 0, str(exc), None
        except adapters_claude.ParseFailed as exc:
            return "parse_error", tries, str(exc), None  # the adapter already retried once
        except PageMissing as exc:
            last = ("route_missing_page", str(exc))
        except Exception as exc:  # an engine failure is a failed call, not a crash of the batch
            last = ("route_failed", f"{type(exc).__name__}: {exc}")
    return last[0], retry + 1, last[1], None


def _existing_rows(path: Path, page: Page, ders: set[str]) -> bool:
    """A page file a killed run already put in place (complete by construction) for this very page."""
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    except (OSError, ValueError):
        return False
    return bool(rows) and all(r.get("page_id") == page.page_id and r.get("der_sha256") in ders for r in rows)


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
    stop: str | None = None  # "budget" (exit 50) / "privacy" (exit 60) / "isolation": no further call made
    elapsed_ms: int = 0  # WI-13c: time the route took (stored in the attempt receipt; a reused attempt gives it back)


def _stop_of(exc: BaseException) -> tuple[str, str]:
    if isinstance(exc, BudgetExceeded):
        return "budget", f"budget_exceeded: {exc}"
    assert isinstance(exc, SendRefused)
    if exc.exit_code == PRIVACY_EXIT:
        return "privacy", f"privacy_refused: {exc.reason}"
    return "isolation", f"isolation_refused: {exc.reason}"


def _stored_elapsed(directory: Path) -> int:
    """``elapsed_ms`` an earlier run stored in the attempt receipt (0 for an attempt made before WI-13c)."""
    try:
        value = receipts.read(directory / "receipt.json", "receipt").get("elapsed_ms", 0)
    except (OSError, receipts.ReceiptInvalid):  # pragma: no cover - ``_reusable`` just read this very file
        return 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _run_route(route: Route, pages: list[Page], batch_dir: Path, cfg: config_mod.Config, redo: bool,
               out: TextIO, clock: Callable[[], float] = time.monotonic) -> RouteRun:
    if route.problem is not None:
        return RouteRun(None, None, "failed", {p.key: {"status": "route_failed", "error": route.problem}
                                                for p in pages})
    ders = [p.der_sha256 for p in pages]
    fp = ids.cfg_fp(cfg, prompt_sha=route.prompt_sha, schema_sha=route.schema_sha, model=route.model,
                    effort=route.effort, adapter_code_version=f"{__version__}/{ADAPTER_VERSION}", der_sha256s=ders)
    inputs_hash = _sha(_canonical([[p.key, p.page_id, p.der_sha256] for p in pages]))
    config_hash = _sha(_canonical({"config": json.loads(cfg.canonical()), "route": route.name, "model": route.model,
                                   "effort": route.effort, "prompt_sha": route.prompt_sha,
                                   "schema_sha": route.schema_sha, "adapter": f"{__version__}/{ADAPTER_VERSION}"}))
    keys = [p.key for p in pages]
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
                                                         "file": f"{rel}/{k}.jsonl"} for k in keys},
                        elapsed_ms=_stored_elapsed(directory))
    if mode == "continue":
        print(f"kb-ocr: {route.name}: continuing interrupted attempt {rel}", file=out)
    retry = cfg["route_retry"]
    started = _now()
    t0 = clock()
    results: dict[str, dict] = {}
    outputs = []
    stop: tuple[str, str] | None = None
    for page in pages:
        target = directory / f"{page.key}.jsonl"
        if stop is not None:
            results[page.key] = {"status": "route_failed", "run_id": run_id, "tries": 0, "error": stop[1]}
            continue
        if not (mode == "continue" and target.is_file() and _existing_rows(target, page, _ders(route, page))):
            try:
                state, tries, error, rows = _call(route, page, retry)
            except (BudgetExceeded, SendRefused) as exc:
                stop = _stop_of(exc)
                results[page.key] = {"status": "route_failed", "run_id": run_id, "tries": 1, "error": stop[1]}
                continue
            if state != "done":
                results[page.key] = {"status": state, "run_id": run_id, "tries": tries, "error": error}
                continue
            assert rows is not None
            _atomic(target, dumps_jsonl(rows), "run_batch.before_replace", "run_batch.page_done")
        results[page.key] = {"status": "done", "run_id": run_id, "file": f"{rel}/{page.key}.jsonl"}
        outputs.append({"path": target.name, "sha256": _sha(target.read_bytes())})
    status = "done" if all(r["status"] == "done" for r in results.values()) else "failed"
    elapsed_ms = max(0, round((clock() - t0) * 1000))
    _write_receipt(directory / "receipt.json", {
        "schema": "klode-kit.receipt/1", "step": f"ocr_{route.name}", "status": status, "started": started,
        "ended": _now(), "inputs_hash": inputs_hash, "config_hash": config_hash,
        "config_version": cfg.config_version, "outputs": sorted(outputs, key=lambda o: o["path"]),
        "model": route.model, "effort": route.effort, "elapsed_ms": elapsed_ms,
        "exit_code": 0 if status == "done" else 1,
    })
    return RouteRun(run_id, directory, status, results, stop[0] if stop else None, elapsed_ms)


# --------------------------------------------------------------------------- the batch


# r3.3: a page with a failed / missing route stays disputed until the route is repaired; there is no
# "continue with the remaining routes" choice on the confirm page.
FIX = {
    "route_failed": "rerun kb-ocr (a new attempt is made for this route), then kb-audit",
    "route_missing_page": "rerun kb-ocr for this route (--routes <route> --redo), then kb-audit",
    "parse_error": "rerun kb-ocr for this route (the answer broke the output schema twice), then kb-audit",
}


def _uncertain(page: Page, engine: str, result: dict) -> dict:
    state = result["status"]
    fix = FIX[state]
    error = result.get("error") or ""
    if NOT_MASKED in error:
        fix = "run the masker (WI-05a) on this batch, then rerun kb-ocr; cloud routes send only _masked/ images"
    elif error.startswith("budget_exceeded"):
        fix = "raise cloud.max_cost_usd_per_batch in klode-kit.toml and rerun kb-ocr, then kb-audit"
    elif error.startswith("effort_locked"):
        fix = (f"remove the codex.effort override from klode-kit.toml (it is locked to "
               f"{adapters_codex.EFFORT_LOCK!r}), then rerun kb-ocr")
    elif error.startswith(("route_disabled", "route_unavailable")):
        fix = f"{error}; enable or install the route, then rerun kb-ocr"
    return {"id": f"{page.page_id}:{engine}:{state}", "reason": state, "scope": "page", "engine": engine,
            "fix_action": fix}


SKIPPED = {"status": "skipped", "reason": "text_layer"}


def _routed(page: Page) -> bool:
    return page.text_class != "text"


def _text_layer(pages: list[Page]) -> bool | str:
    text = sum(1 for p in pages if p.text_class == "text")
    return True if pages and text == len(pages) else ("partial" if text else False)


def _batch_receipt(batch: Batch, project: Path, routes: list[str], retry: int, skipped: list[dict],
                   runs: dict[str, RouteRun], batch_dir: Path, cloud: dict | None = None,
                   record_type: str | None = None) -> dict:
    pages, issues = [], []
    failed_pages: Counter = Counter()
    for page, der in zip(batch.pages, batch.derived):
        if not _routed(page):
            per = {r: dict(SKIPPED) for r in routes}
        else:
            per = {r: runs[r].pages[page.key] for r in routes}
        unc = [_uncertain(page, r, per[r]) for r in routes if per[r]["status"] not in ("done", "skipped")]
        issues += [f"{per[r]['status']}:{r}:{page.key}" for r in routes if per[r]["status"] not in ("done", "skipped")]
        failed_pages.update(Counter(r for r in routes if per[r]["status"] not in ("done", "skipped")))
        entry = {"key": page.key, "doc_id": page.doc_id, "page_id": page.page_id,
                 "img_sha256": page.img_sha256, "der_sha256": page.der_sha256, "rotate": page.rotate,
                 "source": next(m["path"] for m in batch.manifest if m["img_sha256"] == page.img_sha256),
                 "derived": der["path"], "routes": per, "uncertain": unc}
        if record_type is not None:
            entry["record_type"] = record_type
        if page.text_class is not None:
            entry["text_class"] = page.text_class
            if page.text_class == "text":
                entry["text_file"] = f"text/{page.key}.jsonl"
        pages.append(entry)
    attempts = {r: {"run_id": run.run_id, "dir": run.directory.relative_to(batch_dir).as_posix(), "status": run.status}
                for r, run in runs.items() if run.directory is not None and run.run_id is not None}
    any_routed = any(_routed(p) for p in batch.pages)
    rec = {"schema": RECEIPT_SCHEMA, "batch_id": batch.batch_id, "routes": routes, "route_retry": retry,
           "status": "done" if not issues else "incomplete", "skipped": skipped, "pages": pages,
           "attempts": attempts, "issues": issues, "text_layer": _text_layer(batch.pages),
           "routes_skipped": [] if any_routed else list(routes)}
    rec["route_stats"] = {r: {"elapsed_ms": runs[r].elapsed_ms if r in runs else 0,
                              "failed_pages": failed_pages.get(r, 0)} for r in routes}
    if record_type is not None:
        rec["record_type"] = record_type
    if cloud is not None:
        rec["cloud"] = cloud
    return rec


def _read_pdfs(sources: list[Source], cfg: config_mod.Config, tools: dict[str, str]) -> tuple[dict, list[dict]]:
    pages: dict[str, list] = {}
    errors: list[dict] = []
    for src in sources:
        if src.sha256 in pages:
            continue
        try:
            pages[src.sha256] = textlayer.read_pdf(src.path, min_chars=cfg["text_layer.min_chars"],
                                                   garbage_max=cfg["text_layer.garbage_max"], tools=tools)
        except textlayer.PdfUnreadable as exc:
            errors.append({"path": src.name, "reason": "pdf_unreadable", "detail": str(exc)})
    return pages, errors


def _text_outputs(batch: Batch, project: Path, batch_dir: Path) -> list[str]:
    """Write text/<key>.jsonl, text/<doc>_whole.txt and page_map.jsonl; returns the written relative paths."""
    written: list[str] = []
    page_map: list[dict] = []
    docs: dict[str, list[dict]] = {}
    for page in batch.pages:
        if page.pdf_page is None:
            continue
        pp = page.pdf_page
        lines: list[str] = []
        if page.text_class == "text":
            rows = textlayer.pdftext_rows(pp.text, page_id=page.page_id, der_sha256=page.der_sha256, dpi=pp.dpi)
            rel = f"text/{page.key}.jsonl"
            _write_meta(batch_dir / rel, dumps_jsonl(rows))
            written.append(rel)
            lines = [r["text"] for r in rows]
        docs.setdefault(page.doc_id, []).append({
            "page_id": page.page_id, "key": page.key, "class": page.text_class, "dpi": pp.dpi,
            "size_px": list(pp.size_px), "whole": f"text/{page.doc_id}_whole.txt", "lines": lines})
    for doc, rows in docs.items():
        whole, pm = text_to_whole(rows)
        rel = f"text/{doc}_whole.txt"
        _write_meta(batch_dir / rel, whole.encode("utf-8"))
        written.append(rel)
        page_map += pm
    if page_map:
        _write_meta(batch_dir / "page_map.jsonl", dumps_jsonl(page_map))
        written.append("page_map.jsonl")
    return written


def _fail_receipt(work: Path, batch_id: str, names: list[str], retry: int, skipped: list[dict],
                  errors: list[dict]) -> Path:
    batch_dir = work / batch_id
    _ensure_dir(batch_dir)
    rec = {"schema": RECEIPT_SCHEMA, "batch_id": batch_id, "routes": names, "route_retry": retry,
           "status": "failed", "skipped": skipped, "errors": errors, "pages": [], "attempts": {}, "issues": [],
           "text_layer": False, "routes_skipped": []}
    path = batch_dir / "receipt.json"
    _write_meta(path, (json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    return path


@dataclass
class CloudOptions:
    """What the cloud adapters need besides the batch (WI-06a).

    ``source_env``: the environment the cloud CLI's allowlisted env is taken from (default: this process's);
    ``isolation_receipts``: route -> stored isolation receipt (default ``<project>/work/isolation/<route>/
    isolation_receipt.json``); ``routes_state``: the sender's route state file (default: packaged);
    ``timeout_s``: per call (default ``cloud.timeout_s``); ``reserve_usd``: per-call reservation and CLI budget
    (default ``cloud_budget.CALL_RESERVE_USD``, capped at the batch limit)."""

    source_env: Mapping[str, str] | None = None
    isolation_receipts: Mapping[str, Path] = field(default_factory=dict)
    routes_state: Path | None = None
    timeout_s: float | None = None
    reserve_usd: Any = None

    def isolation_receipt(self, project: Path, route: str) -> Path:
        given = self.isolation_receipts.get(route)
        return Path(given) if given is not None else project / "work" / "isolation" / route / "isolation_receipt.json"


def _masked_images(project: Path, batch_dir: Path) -> dict[str, tuple[str, Path]]:
    """page_id -> (der_sha256, path) of its masked image: the last ``_masked/`` line of ``derived.jsonl`` whose sha
    is in ``_masked/MANIFEST.jsonl``. An unreadable MANIFEST registers nothing (the sender would refuse anyway)."""
    try:
        listed = {r["der_sha256"] for r in mask.read_manifest(project)}
    except mask.MaskError:
        return {}
    found: dict[str, tuple[str, Path]] = {}
    for line in _masked_lines(batch_dir / "derived.jsonl", []):
        if line.get("der_sha256") in listed and isinstance(line.get("page_id"), str):
            found[line["page_id"]] = (line["der_sha256"], project / line["path"])
    return found


def _load_ledger(batch_dir: Path, cfg: config_mod.Config, cloud: CloudOptions) -> Ledger:
    spent, calls = 0.0, 0
    with contextlib.suppress(OSError, ValueError, TypeError, KeyError):
        data = json.loads((batch_dir / LEDGER_FILE).read_text(encoding="utf-8"))
        spent, calls = float(data["spent_usd"]), int(data["calls"])
    path = batch_dir / LEDGER_FILE

    def persist(snapshot: dict) -> None:
        body = {"schema": "klode-kit.cloud_ledger/1", "limit_usd": snapshot["limit_usd"],
                "spent_usd": snapshot["spent_usd"], "calls": snapshot["calls"]}
        _write_meta(path, (json.dumps(body, sort_keys=True) + "\n").encode("utf-8"))

    reserve = cloud.reserve_usd if cloud.reserve_usd is not None else CALL_RESERVE_USD
    return Ledger(cfg["cloud.max_cost_usd_per_batch"], spent_usd=max(spent, 0.0), calls=max(calls, 0),
                  reserve_usd=reserve, on_change=persist)


def _attach_cloud(route: Route, project: Path, batch_dir: Path, cfg: config_mod.Config, cloud: CloudOptions,
                  ledger: Ledger) -> None:
    if route.name == "codex" and cfg["codex.effort"] != adapters_codex.EFFORT_LOCK:
        route.problem = f"effort_locked: {adapters_codex.EffortLocked(cfg['codex.effort'])}"
        return
    adapter = adapters_claude if route.name == "claude" else adapters_codex
    masked = _masked_images(project, batch_dir)
    if not masked:
        route.problem = f"route_unavailable: no masked image for this batch; {NOT_MASKED}"
        return
    route.masked = {pid: sha for pid, (sha, _path) in masked.items()}
    receipts_dir = batch_dir / CLOUD_RECEIPTS
    isolation = cloud.isolation_receipt(project, route.name)

    def call(page: Page) -> list[dict]:
        if page.page_id not in masked:
            raise NotMasked(f"route_unavailable: page {page.key} is not masked; {NOT_MASKED}")
        sha, path = masked[page.page_id]
        return adapter.read_page(path, sha, page_id=page.page_id, project=project, receipt_dir=receipts_dir,
                                 isolation_receipt=isolation, ledger=ledger, routes_state=cloud.routes_state,
                                 config=cfg, source_env=cloud.source_env, timeout_s=cloud.timeout_s).rows

    route.call = call


def _receipt_type(path: Path) -> str | None:
    """``record_type`` of an earlier batch receipt (WI-13c), if it is a known one."""
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("record_type")
    except (OSError, ValueError, AttributeError):
        return None
    return value if value in RECORD_TYPES else None


def run(project: Path, inbox: Path, routes: list[str] | None = None, *, redo: bool = False,
        routes_enabled: Mapping[str, bool] | None = None, out: TextIO | None = None,
        cloud: CloudOptions | None = None, record_type: str | None = None,
        clock: Callable[[], float] | None = None) -> int:
    out = sys.stderr if out is None else out
    if record_type is not None and record_type not in RECORD_TYPES:
        print(f"kb-ocr: unknown document type {record_type!r}; types: {', '.join(RECORD_TYPES)}", file=out)
        return USAGE_EXIT
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
    if clock is None:  # a replayed run is deterministic: a logical clock that never advances measures 0 ms
        clock = (lambda: 0.0) if replay is not None else time.monotonic
    enabled = routes_enabled_default() if routes_enabled is None else dict(routes_enabled)
    work = project / "work"
    pdfs = [s for s in images if s.kind == "pdf"]
    pdf_pages: dict = {}
    if pdfs:
        try:
            tools = textlayer.require_tools()
        except textlayer.PopplerMissing as exc:
            print(f"kb-ocr: {exc}", file=out)
            return ENV_EXIT
        pdf_pages, errors = _read_pdfs(pdfs, cfg, tools)
        if errors:
            batch_id = ids.batch_id([s.sha256 for s in images])
            _ensure_dir(work)
            try:
                lock = _lock(work, batch_id)
            except LockBusy as exc:
                print(f"kb-ocr: {exc}", file=out)
                return ENV_EXIT
            try:
                path = _fail_receipt(work, batch_id, names, cfg["route_retry"], skipped, errors)
            finally:
                os.close(lock)
            for e in errors:
                print(f"kb-ocr: {e['reason']}: {e['path']} ({e['detail']})", file=out)
            print(f"kb-ocr: batch {batch_id} stopped, nothing extracted; receipt {path}", file=out)
            return USAGE_EXIT
    batch = plan(images, project, work, pdf_pages)
    routed = [p for p in batch.pages if _routed(p)]
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
        _write_meta(batch_dir / "derived.jsonl",
                    dumps_jsonl([*batch.derived, *_masked_lines(batch_dir / "derived.jsonl", batch.derived)]))
        text_files = _text_outputs(batch, project, batch_dir)
        started = _now()
        cloud_opts = cloud or CloudOptions()
        ledger = None
        if routed and any(specs[n].cloud for n in names):
            ledger = _load_ledger(batch_dir, cfg, cloud_opts)
            for name in names:
                if specs[name].cloud:
                    _attach_cloud(specs[name], project, batch_dir, cfg, cloud_opts, ledger)
        runs: dict[str, RouteRun] = {}
        stop: str | None = None
        for name in names if routed else []:
            spec = specs[name]
            if stop in ("budget", "privacy") and spec.cloud and spec.problem is None:
                spec.problem = ("budget_exceeded: the batch budget stopped cloud sending" if stop == "budget"
                                else "privacy_refused: the sender refused an image; cloud sending stopped")
            runs[name] = _run_route(spec, routed, batch_dir, cfg, redo, out, clock)
            if runs[name].stop in ("budget", "privacy") and stop != "privacy":
                stop = runs[name].stop
        cloud_block = None
        if ledger is not None:
            cloud_block = {"limit_usd": float(ledger.limit_usd), "spent_usd": float(ledger.spent_usd),
                           "calls": ledger.calls, "stop": stop}
        kept = record_type or _receipt_type(batch_dir / "receipt.json")  # sticky: a rerun without --type keeps it
        rec = _batch_receipt(batch, project, names, cfg["route_retry"], skipped, runs, batch_dir, cloud_block, kept)
        outputs = [{"path": n, "sha256": _sha((batch_dir / n).read_bytes())}
                   for n in ("manifest.jsonl", "derived.jsonl", *text_files)]
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
            if res["status"] not in ("done", "skipped"):
                print(f"kb-ocr: {res['status']}:{r}:{page['key']} ({res.get('error')})", file=out)
    if rec["routes_skipped"]:
        print(f"kb-ocr: text layer on every page; routes skipped: {','.join(rec['routes_skipped'])}", file=out)
    print(f"kb-ocr: batch {batch.batch_id}: {len(batch.pages)} page(s), routes {','.join(names)}, "
          f"{len(rec['issues'])} issue(s); receipt {(batch_dir / 'receipt.json')}", file=out)
    if stop == "privacy":
        print("kb-ocr: stopped: the sender refused an image (privacy, exit 60); no further cloud call", file=out)
        return PRIVACY_EXIT
    if stop == "budget":
        block = rec["cloud"]
        print(f"kb-ocr: stopped: cloud budget used up ({block['spent_usd']} of {block['limit_usd']} USD per batch, "
              f"{block['calls']} call(s)); no further cloud call (exit 50)", file=out)
        return BUDGET_EXIT
    return 0


def main(argv: list[str] | None = None, *, routes_enabled: Mapping[str, bool] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode-kit kb-ocr", description="OCR a directory of images on four routes.")
    parser.add_argument("--batch", required=True, type=Path, help="inbox directory with the images/PDFs, or one PDF")
    parser.add_argument("--project", type=Path, default=None, help="data project directory (default: cwd)")
    parser.add_argument("--routes", default=None, help="comma-separated subset of vision,mlx,claude,codex")
    parser.add_argument("--type", dest="record_type", choices=RECORD_TYPES, default=None,
                        help="document type, written to the batch receipt (decides records/<date>-<type>.md)")
    parser.add_argument("--redo", action="store_true", help="always start new attempts (never reuse done ones)")
    parser.add_argument("--isolation-receipt", action="append", default=[], metavar="ROUTE=PATH",
                        help="stored isolation receipt of a cloud route "
                             "(default <project>/work/isolation/<route>/isolation_receipt.json)")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return USAGE_EXIT if exc.code else 0
    receipts_by_route: dict[str, Path] = {}
    for item in args.isolation_receipt:
        route, sep, value = item.partition("=")
        if not sep or route not in CLOUD_ROUTES or not value:
            print(f"kb-ocr: --isolation-receipt must be ROUTE=PATH with ROUTE in {CLOUD_ROUTES}: {item!r}",
                  file=sys.stderr)
            return USAGE_EXIT
        receipts_by_route[route] = Path(value)
    project = Path.cwd() if args.project is None else args.project
    routes = None
    if args.routes is not None:
        routes = list(dict.fromkeys(r.strip() for r in args.routes.split(",") if r.strip()))
    try:
        return run(project, args.batch, routes, redo=args.redo, routes_enabled=routes_enabled,
                   cloud=CloudOptions(isolation_receipts=receipts_by_route), record_type=args.record_type)
    except config_mod.ConfigError as exc:
        print(f"kb-ocr: configuration error: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":  # pragma: no cover - entry is ``klode-kit kb-ocr``
    sys.exit(main())
