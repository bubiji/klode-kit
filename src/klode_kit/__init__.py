"""klode-kit: scanned documents -> citable klode knowledge base (Claude Code plugin)."""

from __future__ import annotations

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("klode-kit")
except _metadata.PackageNotFoundError:  # running from a bare source tree
    __version__ = "0+unknown"
