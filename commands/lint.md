---
description: Report drift between FILETREE.md and current repo, read-only. Prompts to run /filetree:update on drift.
allowed-tools: Bash(python:*)
---

Run the drift check and present results to the user:

```bash
python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" lint
```

The script outputs JSON and exits 1 if there is any drift, 0 if clean.

Format the JSON for the user, grouped by category (added / changed / removed /
renamed) with counts. If `stats.need_llm > 0` or there are any `removed` /
`renamed` entries, remind the user to run `/filetree:update` to sync.

Do not call any LLM. Do not modify any files. Do not load the filetree
SKILL.md — lint is pure script invocation, no shared rules apply here.

Do not echo the raw JSON back to the user verbatim. A summary of counts plus
the drift paths (truncated if many) is enough; users can re-run the script if
they want full output.
