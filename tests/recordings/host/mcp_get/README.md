Recorded `claude mcp get spike-klode` output (Claude Code 2.1.285, macOS, 2026-10-23), WI-00d2 matrix 00d2-08.
Home directory replaced by `/Users/<user>`. Server: `tests/recordings/host/mini_mcp_server.py` (synthetic), `.mcp.json`
entry carries `env: {KLODE_KIT_BUILD: ...}` (r4.2 version marker, question ⑮).

- `connected.txt`: approved in `.claude/settings.local.json` (`enabledMcpjsonServers`), trusted folder.
- `pending.txt`: no approval (same output for an approved server in an untrusted folder).
- `failed.txt`: approved, command does not exist.
- `missing.txt`: unknown server name (stdout+stderr, last line is the exit code). The list of configured server names is REDACTED to `server-a, server-b` (they were the recording user's own servers).
