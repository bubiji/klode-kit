"""Audit-hook installer, distributed by putting this directory on PYTHONPATH (matrix 0.3 v7/v8).

The hook is installed ONLY in klode-kit processes:
- ``KLODE_KIT_AUDIT_ROLE=1``, and
- ``__main__`` is ``klode_kit`` or one of its submodules (``python -m klode_kit...``,
  judged from ``sys.orig_argv`` because ``__main__`` is not loaded yet when site runs).

Anything else - including the klode CLI even when it is a Python process - gets no hook and
is an explicit blind spot. Project Python descendants inherit the environment and append to
the same ``KLODE_KIT_AUDIT_LOG`` (O_APPEND, one write per record), so their events merge in
real order.

Recorded events: every filesystem mutation (write-mode ``open``/``os.open``, rename/replace,
symlink, link, remove/unlink, mkdir, rmdir, truncate/ftruncate, chmod, chown, utime, ...),
read-mode opens, ``tempfile.mkdtemp``/``mkstemp``, ``subprocess.Popen`` and
``klode_kit.crashpoint``. Paths are resolved at event time (realpath of the parent, final
component kept) so that later renames cannot re-classify them.
"""

import os
import sys


def _main_module(argv):
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "-m":
            return argv[i + 1] if i + 1 < len(argv) else None
        if arg.startswith("-m"):
            return arg[2:]
        if arg == "-c" or arg.startswith("-c"):
            return None
        if arg in ("-X", "-W", "-Q"):
            i += 2
            continue
        if arg.startswith("-"):
            i += 1
            continue
        return None
    return None


def _wanted():
    if os.environ.get("KLODE_KIT_AUDIT_ROLE") != "1":
        return False
    module = _main_module(list(getattr(sys, "orig_argv", [])))
    return module is not None and (module == "klode_kit" or module.startswith("klode_kit."))


_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
# event -> (index of target path, index of dir_fd or None, index of source or None)
_MUTATIONS = {
    "os.rename": (1, 3, 0),
    "os.link": (1, 3, 0),
    "os.symlink": (1, 2, 0),
    "os.remove": (0, 1, None),
    "os.rmdir": (0, 1, None),
    "os.mkdir": (0, 2, None),
    "os.chmod": (0, 2, None),
    "os.chown": (0, 3, None),
    "os.truncate": (0, None, None),
    "os.utime": (0, 3, None),
    "os.chflags": (0, None, None),
    "os.lchflags": (0, None, None),
    "os.setxattr": (0, None, None),
    "os.removexattr": (0, None, None),
    "os.mkfifo": (0, 2, None),
    "os.mknod": (0, 3, None),
}


def _install(log_path):
    import json
    import threading

    fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    local = threading.local()

    def fd_path(num):
        try:
            if sys.platform == "darwin":
                import fcntl

                raw = fcntl.fcntl(num, fcntl.F_GETPATH, b"\0" * 1024)
                return os.fsdecode(raw.split(b"\0", 1)[0])
            return os.readlink(f"/proc/self/fd/{num}")
        except OSError:
            return None

    def resolve(target, dir_fd=None):
        if isinstance(target, int):
            return fd_path(target), False
        try:
            text = os.fsdecode(target)
        except TypeError:
            return None, True
        unresolved = False
        if not os.path.isabs(text):
            base = fd_path(dir_fd) if isinstance(dir_fd, int) else os.getcwd()
            if base is None:
                base, unresolved = os.getcwd(), True
            text = os.path.join(base, text)
        if text == os.devnull:
            return text, unresolved
        head, tail = os.path.split(os.path.normpath(text))
        return os.path.join(os.path.realpath(head), tail), unresolved

    def record(ev, args):
        if ev == "open":
            target, _mode, flags = args
            if isinstance(target, int):
                return None
            path, unresolved = resolve(target)
            mut = bool((flags or 0) & _WRITE_FLAGS)
            return {"ev": ev, "mut": mut, "path": path, "unresolved": unresolved}
        if ev in _MUTATIONS:
            ti, di, si = _MUTATIONS[ev]
            dir_fd = args[di] if di is not None and di < len(args) else None
            path, unresolved = resolve(args[ti], dir_fd)
            rec = {"ev": ev, "mut": True, "path": path, "unresolved": unresolved}
            if si is not None:
                rec["src"] = resolve(args[si])[0] if not isinstance(args[si], int) else None
            return rec
        if ev in ("tempfile.mkdtemp", "tempfile.mkstemp"):
            return {"ev": ev, "mut": False, "path": resolve(args[0])[0]}
        if ev == "klode_kit.crashpoint":
            return {"ev": ev, "name": str(args[0])}
        if ev == "subprocess.Popen":
            argv = args[1]
            if isinstance(argv, (str, bytes)):
                argv = [argv]
            return {"ev": ev, "argv": [os.fsdecode(a) if isinstance(a, (str, bytes)) else str(a) for a in (argv or [])]}
        return None

    watched = {"open", "tempfile.mkdtemp", "tempfile.mkstemp", "klode_kit.crashpoint", "subprocess.Popen", *_MUTATIONS}

    def hook(ev, args):
        if ev not in watched or getattr(local, "busy", False):
            return
        local.busy = True
        try:
            try:
                rec = record(ev, args)
            except Exception as exc:  # never lose an event silently
                rec = {"ev": ev, "mut": True, "path": None, "unresolved": True, "error": repr(exc)}
            if rec is not None:
                rec["pid"] = os.getpid()
                os.write(fd, (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8"))
        finally:
            local.busy = False

    sys.addaudithook(hook)


if _wanted():
    _log = os.environ.get("KLODE_KIT_AUDIT_LOG")
    if _log:
        _install(_log)
    else:
        sys.stderr.write("klode-kit: KLODE_KIT_AUDIT_ROLE=1 but KLODE_KIT_AUDIT_LOG is not set; audit hook not installed\n")
