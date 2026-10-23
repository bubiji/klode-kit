"""Minimal stdio MCP server for host probes (WI-00d2 matrix 00d2-06/08). Synthetic; no data, one no-op tool.

Answers ``initialize``, ``tools/list``, ``ping`` and ``tools/call`` (tool ``spike_echo``); notifications are ignored.
"""

from __future__ import annotations

import json
import os
import sys


def _reply(msg_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}) + "\n")
    sys.stdout.flush()


def main() -> int:
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:
            continue
        if method == "initialize":
            _reply(msg_id, {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "klode-kit-spike-mcp", "version": "0.0.1"}})
        elif method == "tools/list":
            _reply(msg_id, {"tools": [{"name": "spike_echo", "description": "Echo the build marker.",
                                       "inputSchema": {"type": "object", "properties": {}}}]})
        elif method == "tools/call":
            _reply(msg_id, {"content": [{"type": "text", "text": os.environ.get("KLODE_KIT_BUILD", "none")}]})
        else:
            _reply(msg_id, {})
    return 0


if __name__ == "__main__":
    sys.exit(main())
