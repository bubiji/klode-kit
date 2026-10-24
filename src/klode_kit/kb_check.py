"""Compatibility import path of the ``/klode-kit:kb-check`` kernel (moved to ``klode_kit.check.kb_check``, WI-10a)."""

from klode_kit.check.kb_check import *  # noqa: F401,F403
from klode_kit.check.kb_check import PLUGIN_ROOT, main, plugin_block, root_sha, run_files  # noqa: F401
