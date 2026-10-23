"""PreToolUse hook: a Bash ``claude`` / ``codex`` call may only name registered images (WI-05b; design 02 r2.2-B
"第二道闸"). The sender (``ocr/send.py``) is the first gate and holds without this hook (05b-14).

Wired by ``hooks/hooks.json`` (plugin root) as an exec-form command hook on ``PreToolUse`` / matcher ``Bash``,
filtered with ``if: Bash(claude *)`` and ``if: Bash(codex *)``:
``{"command": "${CLAUDE_PLUGIN_ROOT}/bin/klode-kit", "args": ["hook", "privacy-guard"]}``. Format and fields
per https://code.claude.com/docs/en/plugins/components#hooks and https://code.claude.com/docs/en/hooks ("Common
fields", "Exec form and shell form"); the ``if`` filter is best-effort per those docs, so this script decides on
its own input and lets every non-cloud command through.

Input (stdin, https://code.claude.com/docs/en/hooks): the PreToolUse event - ``hook_event_name``, ``cwd``,
``tool_input.command``. The data project is ``$CLAUDE_PROJECT_DIR`` ("the project root where the session
started", same page) or, without it, the event ``cwd``.

Decision:

- the command is split into simple commands (``;``, ``&&``, ``||``, ``|``, ``&``, parentheses, newlines); leading
  ``VAR=value`` words and the wrappers ``env`` / ``command`` / ``exec`` / ``nohup`` / ``time`` are skipped; a
  ``cd <dir>`` moves the directory later relative names are resolved against;
- a simple command whose program (basename) is ``claude`` or ``codex`` is a cloud call. The images it names are
  the ``-i`` / ``--image`` values (comma lists split) and every other word that ends in an image extension, or,
  inside a longer word such as a prompt, every image-looking file name. Relative names are resolved against the
  shell directory and, for codex, also against ``-C`` / ``--cd``; every candidate that exists must pass;
- an image passes when it is a regular file outside any ``_mask_review`` directory (as given and by real path)
  whose sha256 is registered: ``send.approved_shas(project)`` (``_masked/MANIFEST.jsonl`` rows + the synthetic
  fixtures MANIFEST);
- exit 0 when every named image of every cloud call passes (and for commands that are not cloud calls); exit 2
  ("blocks the tool call", stderr shown to Claude) otherwise, and whenever the input cannot be judged: stdin not a
  PreToolUse JSON object with a string ``tool_input.command``, unbalanced quotes, a named image that does not
  exist, a corrupt MANIFEST. stderr names the reason and at most a sha prefix - never a path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from klode_kit.audit import mask

ALLOW_EXIT = 0
DENY_EXIT = 2
CLOUD_PROGRAMS = frozenset({"claude", "codex"})
WRAPPERS = frozenset({"env", "command", "exec", "nohup", "time"})
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif")
_PUNCT = "();<>|&\n"
_ASSIGN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_NAME = re.compile(r"[^\s'\"`,;:()<>|&]+\.(?:png|jpe?g|gif|webp|bmp|tiff?|heic|heif)(?![A-Za-z0-9])", re.IGNORECASE)


class Deny(Exception):
    """The call is blocked; the message is shown to Claude (no paths)."""


def simple_commands(command: str) -> list[list[str]]:
    """Words of each simple command; ``ValueError`` on unbalanced quotes."""
    lex = shlex.shlex(command, posix=True, punctuation_chars=_PUNCT)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    commands: list[list[str]] = []
    current: list[str] = []
    for token in lex:
        if token and all(ch in _PUNCT for ch in token):
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def _strip_prefix(words: list[str]) -> list[str]:
    i = 0
    while i < len(words):
        word = words[i]
        if _ASSIGN.match(word) or (word in WRAPPERS) or (i > 0 and words[0] == "env" and word.startswith("-")):
            i += 1
            continue
        break
    return words[i:]


def _is_image_word(word: str) -> bool:
    return word.lower().endswith(IMAGE_EXTS)


def image_refs(words: list[str]) -> tuple[list[str], list[str]]:
    """(named images, ``-C`` dirs) of one cloud command (program word excluded)."""
    refs: list[str] = []
    dirs: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        value: str | None = None
        for flag in ("-i", "--image", "-C", "--cd"):
            if word == flag and i + 1 < len(words):
                value, i = words[i + 1], i + 1
                break
            if flag.startswith("--") and word.startswith(flag + "="):
                value = word.split("=", 1)[1]
                break
        else:
            flag = None
        if flag in ("-C", "--cd"):
            dirs.append(value or "")
        elif flag in ("-i", "--image"):
            refs += [v for v in (value or "").split(",") if v]
        elif _is_image_word(word):
            refs.append(word)
        else:
            refs += _NAME.findall(word)
        i += 1
    return refs, dirs


def _sha_file(path: Path) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _check_image(ref: str, bases: list[Path], approved: frozenset[str]) -> None:
    candidates = [Path(ref)] if os.path.isabs(ref) else [base / ref for base in bases]
    existing = [c for c in dict.fromkeys(candidates) if os.path.lexists(c)]
    if not existing:
        raise Deny("a named image does not exist, so it cannot be verified")
    for c in existing:
        if mask.REVIEW_DIR in Path(os.path.abspath(c)).parts or mask.REVIEW_DIR in Path(os.path.realpath(c)).parts:
            raise Deny(f"images under {mask.REVIEW_DIR}/ are never sent to a cloud CLI")
        if not os.path.isfile(c):
            raise Deny("a named image is not a regular file")
        sha = _sha_file(c)
        if sha not in approved:
            raise Deny(f"image {sha[:12]} is not registered in _masked/MANIFEST.jsonl (mask it first)")


def decide(event: Any, env: dict[str, str]) -> None:
    """Return when allowed; raise ``Deny`` otherwise."""
    if not isinstance(event, dict) or event.get("hook_event_name") != "PreToolUse":
        raise Deny("stdin is not a PreToolUse event")
    tool_input = event.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        raise Deny("tool_input.command missing")
    cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and os.path.isabs(event["cwd"]) else os.getcwd()
    project = env.get("CLAUDE_PROJECT_DIR") or cwd
    try:
        commands = simple_commands(command)
    except ValueError as exc:
        raise Deny(f"cannot parse the command ({exc})") from None
    approved: frozenset[str] | None = None
    shell_dir = Path(cwd)
    for words in commands:
        words = _strip_prefix(words)
        if not words:
            continue
        program = os.path.basename(words[0])
        if program == "cd" and len(words) > 1:
            shell_dir = shell_dir / words[1]
            continue
        if program not in CLOUD_PROGRAMS:
            continue
        refs, dirs = image_refs(words[1:])
        if not refs:
            continue
        if approved is None:
            from klode_kit.ocr.send import approved_shas

            try:
                approved = approved_shas(project)
            except mask.MaskError:
                raise Deny("the _masked MANIFEST cannot be read") from None
        bases = [shell_dir, *(shell_dir / d for d in dirs)]
        for ref in refs:
            _check_image(ref, bases, approved)


def main(argv: list[str] | None = None) -> int:
    try:
        raw = sys.stdin.buffer.read()
        event = json.loads(raw.decode("utf-8"))
        decide(event, dict(os.environ))
    except Deny as exc:
        print(f"privacy_guard: blocked: {exc}", file=sys.stderr)
        return DENY_EXIT
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"privacy_guard: blocked: cannot judge the call ({type(exc).__name__})", file=sys.stderr)
        return DENY_EXIT
    return ALLOW_EXIT


if __name__ == "__main__":
    sys.exit(main())
