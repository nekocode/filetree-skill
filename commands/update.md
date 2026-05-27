---
description: Sync FILETREE.md with current repository state — handles added / changed / removed / renamed.
allowed-tools: Read, Bash(python:*), Task
---

Sync FILETREE.md with the current state of the repository.

**First**, read the shared rules at
`${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` — especially the **UNCHANGED
bias** section, since 80%+ of `changed` items should resolve to `"UNCHANGED"`.
Internalize the summary style too, in case any items go to `added`.

## Steps

1. **Generate work plan.**
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" todo
   ```
   If `FILETREE.md` doesn't exist, tell the user to run `/filetree:init` first
   and stop.

2. **Process the work plan.**
   - `added`: Read file, write fresh summary (style guide in SKILL.md)
   - `changed`: **prefer `git diff HEAD -- <path>` over Read** — diff is usually
     10–100× smaller than the full file and shows exactly what changed, which is
     all UNCHANGED decisions need. Only fall back to Read if diff is empty or
     misleading (e.g., the file was never committed, so diff shows the whole file
     anyway).
   - `removed`, `renamed`: nothing for you to do; the script handles them in apply

   If you already edited the file in this session, you may decide UNCHANGED from
   your own working memory without re-reading.

   When `stats.need_llm > 20`, use Task sub-agents (one per ~10 files). Sub-agents
   run with isolated context, so each sub-agent prompt MUST instruct them to first
   `Read ${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` to internalize the summary
   style and UNCHANGED bias before processing their batch.

3. **Apply.** Pipe decisions to stdin:
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" apply
   ```
   Payload shape:
   ```json
   {
     "updates": [{"path": "...", "hash": "...", "summary": "..." | "UNCHANGED"}],
     "removals": ["path1", "path2"],
     "renames": [{"old_path": "...", "new_path": "..."}]
   }
   ```

4. **Report.** added N, removed M, renamed R, summaries updated S,
   hashes refreshed (UNCHANGED) U.

## Do not

- Commit. User reviews and commits.
- `cat` / `Read` the resulting `FILETREE.md` after apply. The `apply` stdout
  (`{"total_entries": N, "received": ..., "applied": ...}`, plus optional
  `skipped_*` keys) already confirms success; dumping the full manifest is pure
  token waste.
