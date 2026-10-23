---
name: kb-check
description: Check a published klode knowledge base and print its check receipt. Minimal WI-00d2 spike version - only the plugin block (name, version, entry, root_sha) is real; the build checks arrive with WI-13a.
argument-hint: --kb <kb_root>
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-check *)
---

## Check receipt

!`${CLAUDE_PLUGIN_ROOT}/bin/klode-kit kb-check $ARGUMENTS --entry skill:/klode-kit:kb-check`

## Your task

The block above is the check receipt the command wrote to `builds/<build_id>/check_receipt.json` of the knowledge
base. Reply with that JSON exactly as shown and nothing else. If the block is an error message instead of JSON,
reply with the error message exactly as shown.
