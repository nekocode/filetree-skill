---
description: Sync FILETREE.md with current repository state — handles added / changed / removed / renamed.
allowed-tools: Read, Bash(python3:*), Task
---

Sync FILETREE.md with the current state of the repository.

**First**, read the shared rules at `${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` — especially the **UNCHANGED bias** section, since 80%+ of `changed` items should resolve to `"UNCHANGED"`. Internalize the summary style and **summary language** too.

Conduct this whole command — your own narration AND every summary — in the project's canonical language. Resolve it ONCE, up front, per the priority chain in SKILL.md "Summary language" — the `config.language` from step 1's output wins when set.

## Steps

1. **Generate work plan.** One call; the script chunks and writes the work to files (you never count or split):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" todo --split
   ```
   The output carries a `config` block (`manifest_path`, `language`) reflecting `.filetree.json` — read both from there, never re-parse the config file. If `manifest_exists` is `false`, the manifest hasn't been created yet — tell the user to run `/filetree:init` first and stop. (Use this flag, not an empty `total_in_manifest`: a present-but-empty manifest also reads 0.) Otherwise the output gives `split_dir` + `batches` (see SKILL.md "Processing the work plan").

2. **Process the batches** per SKILL.md (0 → skip to apply; otherwise spawn one `claude-haiku-4-5` sub-agent per batch — **every batch, including a lone one**; the main session itself never `Read`s a file or writes a summary). Each batch item is decided thus:
   - `added` (no `old_summary`): Read file, write fresh summary.
   - `changed` (has `old_summary`): **prefer `git diff HEAD -- <path>` over Read** — diff is 10–100× smaller and shows exactly what changed, all an UNCHANGED decision needs. If the diff is EMPTY (the change was already committed, so working tree == HEAD), fall back to `Read`ing the file — the hash moved, so judging purpose from a blank diff would falsely yield UNCHANGED. Most changes → `"UNCHANGED"` (see UNCHANGED bias).
   - `symlink_target` present: do NOT Read — write `symlink → <target>`.

   You don't handle removed/renamed — `apply` recomputes them from repo state. Each sub-agent prompt MUST tell them to first `Read ${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` (summary style, UNCHANGED bias, part-file shape), then Read their assigned `batch_NN.json`, then write `<split_dir>/part_NN.json`. It MUST also state the canonical language explicitly — **"Write all summaries in <language>; if an item's `old_summary` is in another language, rewrite it in <language> even when the purpose is unchanged (do NOT output UNCHANGED)."** This is the one exception to the UNCHANGED bias; it converges a legacy mixed-language manifest gradually — one file per run, as that file's hash changes.

3. **Apply** all parts in one call (shell expands the glob; the script merges, computes hashes from disk, and syncs removed/renamed itself):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" apply <split_dir>/part_*.json
   ```
   Part files carry only `{"updates": [{"path", "summary"}]}`. For **0 batches** (deletion/rename-only drift) there are no part files to glob — pipe an empty payload so apply still runs: `echo '{"updates": []}' | python3 ... apply`.

4. **Verify coverage, then report.** The completion gate is `missing_from_manifest` being empty; `skipped_unchanged_new` / `skipped_missing_path` are bad summaries to fix (a wrong `UNCHANGED`, a hallucinated path). Spawn one more `claude-haiku-4-5` sub-agent — give it the `split_dir`, the exact paths, and a non-colliding output name `part_fixup_NN.json` (NN incrementing) — to summarize those into an extra part, then re-run `apply <split_dir>/part_*.json` (the glob catches the fixup; it merges) until they clear — keep this fixup off the main session too. Ignore `skipped_excluded` (real files the config keeps out — nothing to fix; don't loop on `applied == received`, which these legitimately hold below). Then report straight from `apply`'s return — do NOT re-tally your own part files: `added`, `removed`, `renamed`, `summaries_updated`, `hashes_refreshed` (UNCHANGED). All five are authoritative script output.

## Do not

- Commit. User reviews and commits.
- `cat` / `Read` the resulting manifest after apply. The `apply` stdout (`{"total_entries", "received", "applied", "added", "summaries_updated", "hashes_refreshed", "removed", "renamed"}`, plus optional `skipped_*` / `missing_from_manifest` keys) already confirms success; dumping the full manifest is pure token waste.
