"""Fake cloud CLIs and synthetic projects for the WI-05b sender tests (matrix 05b-*).

Boundary (plan section 0.4): the ``claude`` / ``codex`` child processes. ``FakeCloud(dir)`` writes two executables
named ``claude`` and ``codex`` into ``<dir>/bin``:

- a ``#!/usr/bin/perl`` front that records the environment exactly as the process received it and then execs the
  Python part. A Python or shell front cannot do this: the interpreter adds keys of its own before any user code
  runs (PEP 538 ``LC_CTYPE``, macOS ``__CF_USER_TEXT_ENCODING`` / ``__PYVENV_LAUNCHER__``, sh ``PWD``/``SHLVL``);
- ``fake_impl.py`` (run with this interpreter): appends one JSON line per call to ``<dir>/calls.jsonl`` (and to
  ``mirror_log`` when the state names one) with ``tool``, ``argv`` (arguments after the program), ``cwd`` (real
  path), ``env`` (as received), ``version_probe``; for model calls also ``cwd_mode``, ``cwd_uid``, ``cwd_files``,
  ``pngs`` ({name: sha256 of the bytes it would send}) and ``snapshot`` (a copy of the cwd's regular files, so a
  test can rebuild the reviewed directory after the sender removed it). Behaviour comes from ``<dir>/state.json``:
  ``versions`` ({tool: "x.y.z"} for ``--version``), ``stdout``, ``exit_code``, ``sleep_s``, ``started`` (touch
  ``started.<pid>`` before sleeping), ``guard`` (run the real ``klode-kit hook isolation-guard`` on a Read of the
  image, as a real Claude session would), ``write_extra`` (leave a file of that name in the cwd), ``move_cwd``
  (move the cwd away into the fake's directory).

Paired real paths: the model lane runs the same sender against the real CLIs (06-11/12, 00c2-06/07).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterator

from _support.flows import REPO
from _support.mask_replay import FIXTURES, ReplayVision, make_batch
from klode_kit.audit import mask
from klode_kit.ocr import cloud_argv as CA
from klode_kit.ocr import isolation_check as IC

RX = FIXTURES / "fx-rx-stamp" / "images" / "p001.png"
FIXTURE_PNG = FIXTURES / "fx-labs-1x" / "images" / "p001.png"
VERSIONS = {"claude": IC.CLAUDE_MIN_VERSION, "codex": IC.CODEX_MIN_VERSION}
MODEL_STDOUT = '{"type": "result", "result": "ok"}\n'

_PERL = r"""#!/usr/bin/perl
# WI-05b test fake front (tests/_support/send_fake.py): record the environment exactly as received.
use strict;
use warnings;
my $dir = $0;
$dir =~ s{/[^/]+$}{};
my $snap = "$dir/../env.$$";
open(my $fh, '>', $snap) or die "env snapshot: $!";
binmode $fh;
print $fh "$_=$ENV{$_}\0" for sort keys %ENV;
close $fh;
my $py = '@PYTHON@';
exec {$py} $py, "$dir/../fake_impl.py", $snap, $0, @ARGV or die "exec: $!";
"""

_IMPL = r'''"""WI-05b fake cloud CLI body; see tests/_support/send_fake.py."""
import fcntl, hashlib, json, os, shutil, subprocess, sys, time

snap, self_path, *args = sys.argv[1:]
here = os.path.dirname(os.path.dirname(os.path.abspath(self_path)))
tool = os.path.basename(self_path)
with open(os.path.join(here, "state.json"), encoding="utf-8") as fh:
    state = json.load(fh)
env = {}
with open(snap, "rb") as fh:
    for item in fh.read().split(b"\0"):
        if item:
            key, _, value = item.decode("utf-8", "replace").partition("=")
            env[key] = value
version = args == ["--version"]
cwd = os.getcwd()
entry = {"tool": tool, "argv": args, "cwd": cwd, "env": env, "version_probe": version, "pid": os.getpid()}
if not version:
    st = os.stat(cwd)
    names = sorted(os.listdir(cwd))
    pngs = {}
    for name in names:
        if name.endswith(".png"):
            with open(os.path.join(cwd, name), "rb") as img:
                pngs[name] = hashlib.sha256(img.read()).hexdigest()
    snapdir = os.path.join(here, "snap-%d" % os.getpid())
    os.mkdir(snapdir)
    for name in names:
        src = os.path.join(cwd, name)
        if os.path.isfile(src) and not os.path.islink(src):
            shutil.copyfile(src, os.path.join(snapdir, name))
    entry.update(cwd_mode=st.st_mode & 0o7777, cwd_uid=st.st_uid, cwd_files=names, pngs=pngs, snapshot=snapdir)
line = json.dumps(entry, ensure_ascii=False) + "\n"
for log in (os.path.join(here, "calls.jsonl"), state.get("mirror_log")):
    if log:
        with open(log, "a", encoding="utf-8") as out:
            fcntl.flock(out.fileno(), fcntl.LOCK_EX)
            out.write(line)
if version:
    sys.stdout.write("%s (fake)\n" % state["versions"][tool])
    sys.exit(0)
if state.get("guard") and tool == "claude":
    image = [n for n in sorted(os.listdir(cwd)) if n.endswith(".png")][0]
    event = {"hook_event_name": "PreToolUse", "tool_name": "Read", "cwd": cwd,
             "tool_input": {"file_path": os.path.join(cwd, image)}}
    subprocess.run(["klode-kit", "hook", "isolation-guard"], input=json.dumps(event).encode(), check=False,
                   capture_output=True)
if state.get("write_extra"):
    with open(os.path.join(cwd, state["write_extra"]), "w") as extra:
        extra.write("left behind by the fake\n")
if state.get("move_cwd"):
    os.rename(cwd, os.path.join(here, "moved-cwd-%d" % os.getpid()))
if state.get("started"):
    open(os.path.join(here, "started.%d" % os.getpid()), "w").close()
time.sleep(float(state.get("sleep_s", 0)))
sys.stdout.write(state.get("stdout", ""))
sys.stdout.flush()
sys.exit(int(state.get("exit_code", 0)))
'''


class FakeCloud:
    """``<root>/bin/{claude,codex}`` plus their state and call log (see module docstring)."""

    def __init__(self, root: Path, **state: Any):
        self.root = Path(root)
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        (self.root / "fake_impl.py").write_text(_IMPL, encoding="utf-8")
        front = _PERL.replace("@PYTHON@", sys.executable.replace("\\", "\\\\").replace("'", "\\'"))
        for tool in ("claude", "codex"):
            exe = self.bin / tool
            exe.write_text(front, encoding="utf-8")
            exe.chmod(0o755)
        self.state: dict[str, Any] = {"versions": dict(VERSIONS), "stdout": MODEL_STDOUT, "exit_code": 0}
        self.set(**state)

    def set(self, **state: Any) -> None:
        self.state.update(state)
        (self.root / "state.json").write_text(json.dumps(self.state), encoding="utf-8")

    def calls(self) -> list[dict]:
        log = self.root / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]

    def model_calls(self) -> list[dict]:
        return [c for c in self.calls() if not c["version_probe"]]

    def started(self) -> list[Path]:
        return sorted(self.root.glob("started.*"))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_env(fake: FakeCloud, home: Path, systmp: Path) -> dict[str, str]:
    return {"PATH": os.pathsep.join([str(fake.bin), "/usr/bin", "/bin"]), "HOME": str(home),
            "TMPDIR": str(systmp), "LANG": "C.UTF-8", "USER": "synthetic"}


def masked_project(project: Path) -> tuple[Path, dict]:
    """A data project holding fx-rx-stamp, masked the WI-05a way; returns (project, its MANIFEST row)."""
    project.mkdir(parents=True, exist_ok=True)
    batch = make_batch(project, [RX])
    rows = mask.mask_batch(project, batch, engine=ReplayVision())
    return project, rows[0]


def routes_state(path: Path, **enabled: bool) -> Path:
    data = {"schema": "klode-kit.routes_enabled/1", "routes_enabled": {"claude": True, "codex": True, **enabled},
            "evidence": {}}
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@contextlib.contextmanager
def _tempdir(path: Path) -> Iterator[None]:
    saved = tempfile.tempdir
    path.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(path)
    try:
        yield
    finally:
        tempfile.tempdir = saved


def build(route: str, send_dir: Path, env: dict[str, str], **kw: Any) -> CA.Invocation:
    if route == "claude":
        kw.setdefault("model", "opus")
        kw.setdefault("effort", "max")
        kw.setdefault("max_budget_usd", 5.0)
        return CA.claude(send_dir, source_env=env, **kw)
    kw.setdefault("model", "gpt-6-sol")
    kw.setdefault("effort", "max")
    return CA.codex(send_dir, source_env=env, **kw)


def isolation_receipt(route: str, fake: FakeCloud, home: Path, scratch: Path, out: Path,
                      **changes: Any) -> Path:
    """A layer-(a) PASS isolation receipt for ``route`` made by the real builder and review (fixture image,
    synthetic project, a private TMPDIR under ``scratch``); ``changes`` overwrite receipt keys afterwards."""
    systmp = scratch / "receipt-tmp"
    project = scratch / "receipt-project"
    project.mkdir(parents=True, exist_ok=True)
    with _tempdir(systmp):
        png = FIXTURE_PNG.read_bytes()
        send_dir = CA.prepare_send_dir(png, sha256(png))
        inv = build(route, send_dir, source_env(fake, home, systmp))
        res = IC.check(inv, project=project)
        assert res.ok, res.failed
        receipt = res.receipt()
    receipt.update(changes)
    out.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return out


def rebuild_cwd(call: dict) -> Path:
    """Recreate the directory the fake ran in (path, mode 0700, files) from its snapshot; returns it."""
    cwd = Path(call["cwd"])
    cwd.mkdir(mode=0o700)
    os.chmod(cwd, 0o700)
    for name in call["cwd_files"]:
        (cwd / name).write_bytes((Path(call["snapshot"]) / name).read_bytes())
    return cwd


def invocation_of(call: dict) -> CA.Invocation:
    """The (argv, env, cwd) triple a fake call recorded, as an ``Invocation``."""
    return CA.Invocation(call["tool"], (call["tool"], *call["argv"]), Path(call["cwd"]), dict(call["env"]))


def plugin_root() -> Path:
    return REPO
