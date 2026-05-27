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

1. **Generate work plan.** Pass `--batch-size 25` up front so a large run comes
   back pre-chunked — no second `todo` call to count or split:
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" todo --batch-size 25
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
   your own working memory without re-reading. Items with a `symlink_target`
   field: do not Read — write `symlink → <target>` (see SKILL.md Symlinks).

   When `need_llm > 25`, step 1's output carries a `batches` key. Parallelize per
   the **Part-file protocol** in SKILL.md: `mktemp -d` once, one Task sub-agent
   per batch, each given its batch's items **inline** and writing its own
   `<parts_dir>/part_<i>.json`. Each sub-agent prompt MUST instruct them to first
   `Read ${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` for the summary style,
   UNCHANGED bias, and part-file shape before processing their batch. Do NOT
   write batch files to disk or hand-roll a coverage check — trust
   `missing_from_manifest`.

3. **Apply.** For the parallel path, point `apply` at the part files (the shell
   expands the glob; the script merges them):
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" apply <parts_dir>/part_*.json
   ```
   For a small set done inline, pipe one payload via stdin instead. Emit only
   `{path, summary}` — `apply` computes hashes from disk:
   ```json
   {
     "updates": [{"path": "...", "summary": "..." | "UNCHANGED"}],
     "removals": ["path1", "path2"],
     "renames": [{"old_path": "...", "new_path": "..."}]
   }
   ```

4. **Verify coverage, then report.** If `apply` returns `missing_from_manifest`
   (a dropped sub-agent output) or `skipped_*`, summarize those and re-run
   `apply` (it merges) until clean. Then report: added N, removed M, renamed R,
   summaries updated S, hashes refreshed (UNCHANGED) U.

## Do not

- Commit. User reviews and commits.
- `cat` / `Read` the resulting `FILETREE.md` after apply. The `apply` stdout
  (`{"total_entries": N, "received": ..., "applied": ...}`, plus optional
  `skipped_*` / `missing_from_manifest` keys) already confirms success; dumping
  the full manifest is pure token waste.
