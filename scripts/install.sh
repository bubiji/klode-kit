#!/bin/sh
# klode-kit installer (WI-15a): builds / repairs <repo>/.venv from the committed uv.lock.
#
#   scripts/install.sh [--no-ocr] [--with-mlx] [--dev] [--keep-extra]
#
#   --no-ocr      skip the macOS OCR extra (ocrmac)
#   --with-mlx    also install the MLX engine (Apple silicon; ~ the mlx-vlm dependency tree, no weights)
#   --dev         also install the dev group (pytest, hypothesis, ...)
#   --keep-extra  do not remove packages that are not in the lock (default: exact sync)
#
# Properties (venv addendum V1-V7):
#   - runs from any cwd; the repository is found from this script's REAL path (V2), no absolute paths inside
#   - idempotent: a second run changes nothing (uv sync --locked never rewrites uv.lock)
#   - a venv whose python cannot run, or that was built for another directory (renamed / moved plugin
#     directory, V7), is MOVED to <repo>/_trash/venv-<stamp>-<pid> and rebuilt - nothing is deleted
#   - installs PyPI packages only (--locked); downloads no Python (--no-python-downloads), no weights;
#     writes nothing outside the repository except uv's own package cache; never touches ~/.claude etc.
#   - after the sync: import smoke (klode_kit, klode.lib), launcher --version, then the install
#     fingerprint .venv/klode-kit-install.json (repo root, venv python, lock sha, klode version)
#
# Exit codes: 0 ok; 2 usage; 40 environment problem (uv missing, no python >= 3.12, smoke failed).
set -eu

resolve() {
    p=$1
    while [ -L "$p" ]; do
        link=$(readlink "$p")
        case $link in
            /*) p=$link ;;
            *) p=$(dirname "$p")/$link ;;
        esac
    done
    dir=$(cd -P "$(dirname "$p")" 2>/dev/null && pwd -P) || return 1
    printf '%s/%s\n' "$dir" "$(basename "$p")"
}

usage() {
    echo "usage: scripts/install.sh [--no-ocr] [--with-mlx] [--dev] [--keep-extra]" >&2
}

ocr=1
mlx=0
dev=0
exact=1
for arg in "$@"; do
    case $arg in
        --no-ocr) ocr=0 ;;
        --with-mlx) mlx=1 ;;
        --dev) dev=1 ;;
        --keep-extra) exact=0 ;;
        -h | --help) usage; exit 0 ;;
        *) echo "install.sh: unknown option: $arg" >&2; usage; exit 2 ;;
    esac
done

self=$(resolve "$0") || { echo "install.sh: cannot resolve own path: $0" >&2; exit 40; }
root=$(dirname "$(dirname "$self")")
venv=$root/.venv
py=$venv/bin/python

if ! command -v uv >/dev/null 2>&1; then
    echo "install.sh: 'uv' is not on PATH. Install uv first (https://docs.astral.sh/uv/), then re-run: $root/scripts/install.sh" >&2
    exit 40
fi

unset VIRTUAL_ENV UV_PROJECT_ENVIRONMENT UV_PYTHON PYTHONPATH PYTHONHOME || true
cd "$root"

# ---- 1. decide whether the existing venv must be rebuilt (V7) -----------------------------------------
reason=""
if [ -e "$venv" ] || [ -L "$venv" ]; then
    if ! "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' >/dev/null 2>&1; then
        reason="its python cannot run ($py)"
    elif [ -f "$venv/klode-kit-install.json" ]; then
        recorded=$("$py" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("repo_root", ""))' \
            "$venv/klode-kit-install.json" 2>/dev/null || true)
        here=$(cd -P "$root" && pwd -P)
        if [ "$recorded" != "$here" ]; then
            reason="it was built for another location (${recorded:-unreadable fingerprint})"
        fi
    fi
fi
if [ -n "$reason" ]; then
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    dest=$root/_trash/venv-$stamp-$$
    mkdir -p "$root/_trash"
    echo "install.sh: rebuilding .venv - $reason; the old one is kept at $dest"
    mv "$venv" "$dest"
fi

# ---- 2. sync from the lock ----------------------------------------------------------------------------
set -- sync --project "$root" --locked --no-python-downloads --extra core
os=$(uname -s)
arch=$(uname -m)
if [ "$os" = Darwin ] && [ "$ocr" = 1 ]; then
    set -- "$@" --extra ocr-mac
fi
if [ "$mlx" = 1 ]; then
    if [ "$os" = Darwin ] && [ "$arch" = arm64 ]; then
        set -- "$@" --extra mlx
    else
        echo "install.sh: --with-mlx needs Apple silicon (macOS arm64); skipped" >&2
    fi
fi
if [ "$dev" = 1 ]; then
    set -- "$@" --group dev
else
    set -- "$@" --no-dev
fi
if [ "$exact" = 0 ]; then
    set -- "$@" --inexact
fi
if [ ! -e "$venv" ] && uv python find --no-python-downloads 3.12 >/dev/null 2>&1; then
    set -- "$@" --python 3.12
fi
echo "install.sh: uv $*"
uv "$@" || { echo "install.sh: uv sync failed (is uv.lock up to date? run: uv lock)" >&2; exit 40; }

# ---- 3. smoke: the venv imports the project and klode; the launcher answers ---------------------------
if ! PYTHONPATH="$root/src" "$py" -P -c 'import klode_kit, klode, klode.lib' 2>/dev/null; then
    echo "install.sh: smoke failed: cannot import klode_kit / klode.lib with $py" >&2
    exit 40
fi
version=$("$root/bin/klode-kit" --version) || { echo "install.sh: smoke failed: bin/klode-kit --version" >&2; exit 40; }

# ---- 4. fingerprint (V6 input) ------------------------------------------------------------------------
"$py" -P - "$root" "$venv/klode-kit-install.json" <<'PYEOF'
import hashlib, importlib.metadata as md, json, os, sys

root, out = sys.argv[1], sys.argv[2]
with open(os.path.join(root, "uv.lock"), "rb") as fh:
    lock_sha = hashlib.sha256(fh.read()).hexdigest()
data = {
    "schema": "klode-kit.install/1",
    "repo_root": os.path.realpath(root),
    "venv_python": os.path.realpath(os.path.join(root, ".venv", "bin", "python")),
    "python_version": "%d.%d.%d" % sys.version_info[:3],
    "lock_sha256": lock_sha,
    "klode_version": md.version("klode"),
    "klode_kit_version": md.version("klode-kit"),
}
tmp = out + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, sort_keys=True)
    fh.write("\n")
os.replace(tmp, out)
PYEOF

echo "install.sh: ok - klode-kit $version, klode $("$py" -P -c 'import importlib.metadata as m; print(m.version("klode"))')"
