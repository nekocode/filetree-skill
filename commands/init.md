---
description: Generate FILETREE.md from scratch. Confirms overwrite if it already exists.
allowed-tools: Read, Bash(python:*), Task, AskUserQuestion
---

Generate FILETREE.md from scratch for the current repository.

**First**, read the shared rules at
`${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` — it defines the summary
style, UNCHANGED bias (not used here, but good to internalize for future
`/filetree:update` calls), and parallelization strategy.

## Steps

1. **Check existing.** If `FILETREE.md` exists in the repo root, ask the user
   to confirm overwrite (they likely meant `/filetree:update`). Skip if absent.

2. **Generate work plan.**
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" todo
   ```
   With no existing manifest, every tracked file appears in `added`.

3. **Write summaries.** For each `added` entry: Read the file, write a one-line
   summary per the SKILL.md style guide.

   When `stats.need_llm > 20`, use Task sub-agents (one per ~10 files). Sub-agents
   run with isolated context, so each sub-agent prompt MUST instruct them to first
   `Read ${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` to internalize the summary
   style before writing — otherwise shared rules won't apply to their output.

4. **Apply.** Pipe the decision JSON to stdin:
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" apply
   ```
   Payload shape:
   ```json
   {
     "updates": [{"path": "...", "hash": "...", "summary": "..."}],
     "removals": [],
     "renames": []
   }
   ```

5. **Report.** Total files indexed, files skipped (binary / lock), time taken.

## Do not

- Commit. User reviews `FILETREE.md` and commits manually.
- Write summaries for files in `should_skip` — the script already filters them.
