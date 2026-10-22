"""Synthetic flows that exercise the test-seam and audit infrastructure (WI-00s).

Development tooling only: no production command calls this module. It exists so the
crashpoint / audit-hook / partition machinery can be specified and verified before the
real flows (writeback, publish, receipts, run_batch, send) exist. It is run as
``python -m klode_kit.devtools.synthflow <scenario> --root <dir> [...]`` so that its
``__main__`` belongs to ``klode_kit`` exactly like the real entry points.

Scenarios
- ``partition-ok``: one durable write per segment, a loop crash point, a project child
  process, a foreign child process and non-durable noise.
- ``partition-bad-head`` / ``-mid`` / ``-tail`` / ``-child``: two durable writes in one segment.
- ``ops``: every filesystem mutation kind once, each in its own segment.
- ``seams``: stand-in for send + writeback used by the 00s-14 / 00s-15 seam tests.
- ``child-write`` / ``child-write-twice``: helpers spawned by the scenarios above.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from klode_kit.seams import crashpoint, startup


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _spawn_self(scenario: str, root: Path) -> None:
    subprocess.run([sys.executable, "-m", "klode_kit.devtools.synthflow", scenario, "--root", str(root)], check=True)


def _noise(root: Path) -> None:
    private = tempfile.mkdtemp(prefix="kk-synth-")
    _write(Path(private) / "scratch.txt", "scratch")
    shutil.rmtree(private)
    with open(os.devnull, "w") as fh:
        fh.write("discard")
    os.makedirs(root / "__pycache__", exist_ok=True)
    _write(root / "__pycache__" / "fake.cpython-312.pyc", "pyc")


def partition_ok(root: Path) -> None:
    work = root / "work"
    os.mkdir(work)
    crashpoint("synth.after_mkdir")
    fd, tmp = tempfile.mkstemp(dir=work, prefix=".state-")
    os.write(fd, b'{"v": 1}')
    os.close(fd)
    crashpoint("synth.before_replace")
    os.replace(tmp, work / "state.json")
    for i in range(2):
        crashpoint("synth.loop")
        _write(work / f"item{i}.txt", f"item {i}")
    crashpoint("synth.before_symlink")
    os.symlink("state.json", work / "current")
    crashpoint("synth.before_child")
    _spawn_self("child-write", root)
    subprocess.run(
        [sys.executable, "-c", "import sys; open(sys.argv[1], 'w').write('foreign')", str(work / "foreign.txt")],
        check=True,
    )
    _noise(root)


def bad_head(root: Path) -> None:
    _write(root / "a.txt", "a")
    _write(root / "b.txt", "b")
    crashpoint("synth.first")
    _write(root / "c.txt", "c")


def bad_mid(root: Path) -> None:
    _write(root / "a.txt", "a")
    crashpoint("synth.a")
    fd, tmp = tempfile.mkstemp(dir=root)
    os.close(fd)
    os.replace(tmp, root / "b.txt")
    crashpoint("synth.b")


def bad_tail(root: Path) -> None:
    crashpoint("synth.last")
    os.mkdir(root / "d")
    _write(root / "d" / "x.txt", "x")


def bad_child(root: Path) -> None:
    _write(root / "a.txt", "a")
    crashpoint("synth.before_child")
    _spawn_self("child-write-twice", root)


def child_write(root: Path) -> None:
    _write(root / "work" / "child.txt", "child")


def child_write_twice(root: Path) -> None:
    _write(root / "c1.txt", "1")
    _write(root / "c2.txt", "2")


def ops(root: Path) -> None:
    steps = [
        ("mkdir", lambda: os.mkdir(root / "d")),
        ("open", lambda: _write(root / "t.bin", "0123456789")),
        ("truncate-path", lambda: os.truncate(root / "t.bin", 4)),
    ]
    for name, fn in steps:
        crashpoint(f"ops.{name}")
        fn()
    crashpoint("ops.open-rw")
    fd = os.open(root / "t.bin", os.O_RDWR)
    crashpoint("ops.truncate-fd")
    os.ftruncate(fd, 2)
    os.close(fd)
    more = [
        ("chmod", lambda: os.chmod(root / "t.bin", 0o600)),
        ("rename", lambda: os.rename(root / "t.bin", root / "d" / "t.bin")),
        ("link", lambda: os.link(root / "d" / "t.bin", root / "hard.bin")),
        ("symlink", lambda: os.symlink("hard.bin", root / "soft.bin")),
        ("remove", lambda: os.remove(root / "soft.bin")),
        ("unlink", lambda: os.unlink(root / "hard.bin")),
        ("remove-inner", lambda: os.remove(root / "d" / "t.bin")),
        ("rmdir", lambda: os.rmdir(root / "d")),
    ]
    for name, fn in more:
        crashpoint(f"ops.{name}")
        fn()


def seams(root: Path, out: Path, spawn_claude: bool) -> None:
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("part1: source checked\n")
    crashpoint("send.after_source_check")
    if spawn_claude:
        res = subprocess.run(["claude", "-p", "synth"], capture_output=True, check=True)
        with open(out, "ab") as fh:
            fh.write(b"claude: " + res.stdout)
    crashpoint("writeback.synth_commit")
    with open(out, "a", encoding="utf-8") as fh:
        fh.write("part2: committed\n")


SCENARIOS = {
    "partition-ok": partition_ok,
    "partition-bad-head": bad_head,
    "partition-bad-mid": bad_mid,
    "partition-bad-tail": bad_tail,
    "partition-bad-child": bad_child,
    "child-write": child_write,
    "child-write-twice": child_write_twice,
    "ops": ops,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="klode_kit.devtools.synthflow")
    parser.add_argument("scenario", choices=[*SCENARIOS, "seams"])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--spawn-claude", action="store_true")
    args = parser.parse_args(argv)
    startup()
    if args.scenario == "seams":
        if args.out is None:
            parser.error("seams needs --out")
        seams(args.root, args.out, args.spawn_claude)
    else:
        SCENARIOS[args.scenario](args.root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
