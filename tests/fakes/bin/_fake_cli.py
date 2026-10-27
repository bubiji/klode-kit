"""Replay fake behind tests/fakes/bin/{claude,codex} (tests only; never a real model call).

Usage (via the sh wrappers): ``_fake_cli.py <tool> <args...>``.

- Every invocation appends one JSON line to ``$KLODE_KIT_FAKE_LOG`` (when set):
  ``{"tool", "argv", "argv_sha", "cwd", "pid"}``.
- The scenario is ``$KLODE_KIT_REPLAY_DIR/<tool>/<argv_sha>.json`` (default replay dir:
  ``tests/fixtures/replay``), with ``argv_sha`` = sha256 of the compact UTF-8 JSON array of
  the arguments. It holds ``{"stdout": str, "stderr": str, "exit_code": int}``; stdout and
  stderr are replayed byte for byte.
- No scenario -> exit 97, ``no replay scenario`` on stderr, nothing on stdout.
- A process started with the scrubbed cloud environment (only ``PATH HOME TMPDIR LANG ANTHROPIC_API_KEY USER``,
  r5) never sees ``KLODE_KIT_FAKE_LOG`` / ``KLODE_KIT_REPLAY_DIR``. For that case a test points ``HOME`` at a
  synthetic directory holding ``.klode-kit-fake.json`` = ``{"log": <path>, "replay_dir": <path>}``; the
  environment variables, when present, win (WI-00c3).
- No scenario -> live recordings (WI-00c3): ``$KLODE_KIT_RECORDINGS_DIR/<tool>/*.json`` (default
  ``tests/recordings``; ``recordings_dir`` in the HOME settings) whose ``argv_sha`` equals the sha of the compact
  UTF-8 JSON array ``[tool, *args]`` (``isolation_check.argv_sha``: program name included) are replayed the same
  way. Only then exit 97.
- Behaviour scenarios (WI-00d1, execution bridge): when ``$KLODE_KIT_FAKE_SCENARIO`` names a JSON file, it
  replaces the argv-sha lookup for every call. The file is ``{"steps": [step, ...]}`` or
  ``{"by_card": {"cards_content/<x>.md": [step, ...]}}`` (the card is the first ``cards_content/...md`` found in
  the arguments). The n-th call carrying the same last argument (the prompt) plays step n; the last step repeats.
  A step may hold, applied in this order: ``stamps`` (path; JSON lines ``{"event": "start"|"end", "t", "pid"}``),
  ``pid_file`` (path; own pid appended), ``spawn_child_pid_file`` (path; a ``sleep``-forever child in the same
  process group is started and its pid appended), ``write_files`` ({relative path: content}, written under the
  cwd), ``sleep_s``, ``write_files_after``, then ``stdout`` / ``stderr`` / ``exit_code``. In paths, contents and
  stdout the literal ``{card}`` is replaced by the card.
  A card named in the last argument (the prompt) is preferred over earlier ones (``--json-schema`` holds a pattern).
  ``wait_starts: K`` (WI-15d, needs ``stamps``) is a barrier: after its start stamp the call waits until the stamps file
  holds K start events (at most ``wait_starts_timeout_s``, default 30 s), then goes on (``sleep_s`` ...).
  ``ignore_sigterm`` (bool, applied first) makes this process and the child it spawns ignore SIGTERM.
  A step may carry ``if_prompt_has`` (WI-09c): a text the prompt (last argument) must contain. When the prompt contains
  the text of at least one such step, only those steps are played (in order, counted per prompt); otherwise only the
  steps without the key - so a repair round, whose prompt carries the problem list, gets its own answers.
"""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

NO_SCENARIO_EXIT = 97


def argv_sha(args: list[str]) -> str:
    return hashlib.sha256(json.dumps(args, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _home_settings() -> dict:
    cfg = Path(os.environ.get("HOME") or "/nonexistent") / ".klode-kit-fake.json"
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# A recording's ``argv`` is stored with the recording machine's repository location replaced by this placeholder
# (privacy), but its ``argv_sha`` was taken over the real absolute path (``--settings <repo>/src/.../cloud_settings.json``).
# On another checkout (CI) the sha therefore differs; the argv itself, with the placeholder mapped to this
# checkout, still equals the one being replayed.
RECORDED_ROOT = "<home>/Documents/Claude/Projects/klode-kit"


def _same_argv(recorded: object, tool: str, args: list[str]) -> bool:
    if not isinstance(recorded, list) or not all(isinstance(x, str) for x in recorded):
        return False
    root = str(Path(__file__).resolve().parents[3])
    mapped = [root + x[len(RECORDED_ROOT):] if x.startswith(RECORDED_ROOT + "/") else x for x in recorded]
    return mapped == [tool, *args]


def _recording(tool: str, args: list[str], home: dict) -> Path | None:
    default_dir = Path(__file__).resolve().parents[2] / "recordings"
    root = Path(os.environ.get("KLODE_KIT_RECORDINGS_DIR") or home.get("recordings_dir") or default_dir) / tool
    key = argv_sha([tool, *args])
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or "stdout" not in data:
            continue
        if data.get("argv_sha") == key or _same_argv(data.get("argv"), tool, args):
            return path
    return None


def main() -> int:
    tool, args = sys.argv[1], sys.argv[2:]
    sha = argv_sha(args)
    home = _home_settings()
    log = os.environ.get("KLODE_KIT_FAKE_LOG") or home.get("log")
    if log:
        entry = {"tool": tool, "argv": args, "argv_sha": sha, "cwd": os.getcwd(), "pid": os.getpid()}
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    scenario_file = os.environ.get("KLODE_KIT_FAKE_SCENARIO")
    if scenario_file:
        return _behave(Path(scenario_file), args)

    default_dir = Path(__file__).resolve().parents[2] / "fixtures" / "replay"
    replay_dir = Path(os.environ.get("KLODE_KIT_REPLAY_DIR") or home.get("replay_dir") or default_dir)
    scenario = replay_dir / tool / f"{sha}.json"
    if not scenario.is_file():
        scenario = _recording(tool, args, home) or scenario
    if not scenario.is_file():
        sys.stderr.write(f"fake {tool}: no replay scenario for argv sha {sha} (looked in {replay_dir / tool})\n")
        return NO_SCENARIO_EXIT

    data = json.loads(scenario.read_text(encoding="utf-8"))
    sys.stdout.buffer.write(data.get("stdout", "").encode("utf-8"))
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(data.get("stderr", "").encode("utf-8"))
    sys.stderr.buffer.flush()
    return int(data.get("exit_code", 0))


CARD_RE = re.compile(r"cards_content/[^\s'\"]+?\.md")


def _call_index(scenario: Path, key: str) -> int:
    """0-based count of earlier calls with the same prompt (locked counter file beside the scenario)."""
    counter = scenario.with_name(scenario.name + ".calls." + argv_sha([key])[:16])
    fd = os.open(counter, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        raw = os.read(fd, 64).decode() or "0"
        n = int(raw)
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, str(n + 1).encode())
        return n
    finally:
        os.close(fd)


def _stamp(path: str, event: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": event, "t": time.time(), "pid": os.getpid()}) + "\n")


def _starts(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            return sum(1 for line in fh if '"event": "start"' in line)
    except OSError:
        return 0


def _wait_starts(path: str, count: int, timeout_s: float) -> None:
    """Barrier: return once ``count`` start stamps are in the stamps file (or after ``timeout_s``)."""
    deadline = time.monotonic() + timeout_s
    while _starts(path) < count and time.monotonic() < deadline:
        time.sleep(0.01)


def _append(path: str, line: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _write(files: dict, card: str) -> None:
    for rel, content in files.items():
        target = Path.cwd() / rel.replace("{card}", card)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(content.replace("{card}", card))
            fh.flush()


def _behave(scenario: Path, args: list[str]) -> int:
    data = json.loads(scenario.read_text(encoding="utf-8"))
    found = CARD_RE.search(" ".join(args))
    card = found.group(0) if found else ""
    in_prompt = CARD_RE.search(args[-1]) if args else None  # the prompt wins: --json-schema also holds a card pattern
    card = in_prompt.group(0) if in_prompt else card
    steps = data["by_card"][card] if "by_card" in data else data["steps"]
    prompt = args[-1] if args else ""
    special = [s for s in steps if "if_prompt_has" in s and s["if_prompt_has"] in prompt]
    steps = special or [s for s in steps if "if_prompt_has" not in s]
    idx = _call_index(scenario, args[-1] if args else "")
    step = steps[min(idx, len(steps) - 1)]
    sub = (lambda v: v.replace("{card}", card))
    if step.get("ignore_sigterm"):  # inherited by the spawned child too: only SIGKILL ends this group
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    if step.get("stamps"):
        _stamp(sub(step["stamps"]), "start")
        if step.get("wait_starts"):
            _wait_starts(sub(step["stamps"]), int(step["wait_starts"]), float(step.get("wait_starts_timeout_s", 30)))
    if step.get("pid_file"):
        _append(sub(step["pid_file"]), str(os.getpid()))
    if step.get("spawn_child_pid_file"):
        child = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(3600)"])
        _append(sub(step["spawn_child_pid_file"]), str(child.pid))
    _write(step.get("write_files", {}), card)
    if step.get("sleep_s"):
        time.sleep(float(step["sleep_s"]))
    _write(step.get("write_files_after", {}), card)
    sys.stdout.buffer.write(sub(step.get("stdout", "")).encode("utf-8"))
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(sub(step.get("stderr", "")).encode("utf-8"))
    sys.stderr.buffer.flush()
    if step.get("stamps"):
        _stamp(sub(step["stamps"]), "end")
    return int(step.get("exit_code", 0))


if __name__ == "__main__":
    sys.exit(main())
