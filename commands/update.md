---
description: Sync FILETREE.md with current repository state — handles added / changed / removed / renamed.
allowed-tools: Read, Bash(python:*), Task
---

Sync FILETREE.md with the current state of the repository.

**First**, read the shared rules at
`${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` — especially the **UNCHANGED
bias** section, since 80%+ of `changed` items should resolve to `"UNCHANGED"`.
Internalize the summary style and **summary language** too.

Conduct this whole command — your own narration AND every summary — in the
project's canonical language. Resolve it ONCE, up front, per the priority chain
in SKILL.md "Summary language".

## Steps

1. **Generate work plan.** One call; the script chunks and writes the work to
   files (you never count or split):
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" todo --split
   ```
   If `FILETREE.md` doesn't exist, tell the user to run `/filetree:init` first
   and stop. Otherwise the output gives `split_dir` + `batches` (see SKILL.md
   "Processing the work plan").

2. **Process the batches** per SKILL.md (0 → skip to apply; 1 → inline; many →
   one `claude-haiku-4-5` sub-agent per batch). Each batch item is decided thus:
   - `added` (no `old_summary`): Read file, write fresh summary.
   - `changed` (has `old_summary`): **prefer `git diff HEAD -- <path>` over Read**
     — diff is 10–100× smaller and shows exactly what changed, all an UNCHANGED
     decision needs. Most changes → `"UNCHANGED"` (see UNCHANGED bias).
   - `symlink_target` present: do NOT Read — write `symlink → <target>`.

   You don't handle removed/renamed — `apply` recomputes them from repo state.
   Each sub-agent prompt MUST tell them to first
   `Read ${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` (summary style, UNCHANGED
   bias, part-file shape), then Read their assigned `batch_NN.json`, then write
   `<split_dir>/part_NN.json`. It MUST also state the canonical language
   explicitly — **"Write all summaries in <language>; if an item's `old_summary`
   is in another language, rewrite it in <language> even when the purpose is
   unchanged (do NOT output UNCHANGED)."** This is the one exception to the
   UNCHANGED bias; it converges a legacy mixed-language manifest gradually —
   one file per run, as that file's hash changes.

3. **Apply** all parts in one call (shell expands the glob; the script merges,
   computes hashes from disk, and syncs removed/renamed itself):
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" apply <split_dir>/part_*.json
   ```
   Part files carry only `{"updates": [{"path", "summary"}]}`. For the inline
   1-batch case you may instead pipe that one payload via stdin. For **0 batches**
   (deletion/rename-only drift) there are no part files to glob — pipe an empty
   payload so apply still runs: `echo '{"updates": []}' | python ... apply`.

4. **Verify coverage, then report.** If `apply` returns `missing_from_manifest`
   or `skipped_*`, summarize those into one more part and re-run `apply` (it
   merges) until clean. The result's `removed` / `renamed` counts are
   authoritative. Then report: added N, removed M, renamed R, summaries updated
   S, hashes refreshed (UNCHANGED) U.

## Do not

- Commit. User reviews and commits.
- `cat` / `Read` the resulting `FILETREE.md` after apply. The `apply` stdout
  (`{"total_entries": N, "received": ..., "applied": ...}`, plus optional
  `skipped_*` / `missing_from_manifest` keys) already confirms success; dumping
  the full manifest is pure token waste.
