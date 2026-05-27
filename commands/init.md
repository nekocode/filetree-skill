---
description: Generate FILETREE.md from scratch. Confirms overwrite if it already exists.
allowed-tools: Read, Edit, Write, Bash(python:*), Bash(grep:*), Task, AskUserQuestion
---

Generate FILETREE.md from scratch for the current repository.

**First**, read the shared rules at
`${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` — it defines the summary
style, UNCHANGED bias (not used here, but good to internalize for future
`/filetree:update` calls), and parallelization strategy.

## Steps

1. **Check existing.** If `FILETREE.md` exists in the repo root, ask the user
   to confirm overwrite (they likely meant `/filetree:update`); on decline,
   stop — do not enter step 2. Skip the prompt if absent.

2. **Wire `CLAUDE.md` / `AGENTS.md`.** Do this **before** `todo` so the
   manifest captures the post-wire hash; otherwise the first `/filetree:lint`
   flags the wired file as drifted.

   For each of `CLAUDE.md` and `AGENTS.md`:

   a. **Absent.** Skip — do not create.
   b. **Already wired.** `grep -iE '(\./)?FILETREE\.md' <file>` (full file,
      not a Read slice). Skip only if a match is a real reference — a
      backticked path, link, or bullet. Bare prose, code-fence examples,
      and negative warnings (`do not edit FILETREE.md`) do NOT count.
   c. **Otherwise propose an edit.** Read the file in full. If a section's
      existing bullets are `./*.md` paths (e.g. headings like `## References`,
      `## 引用`, `## Documentation`, `## Project layout`), append a matching
      bullet there. Else append a new short section at end. Match the file's
      language and bullet style (including full-width `——` in zh files).
      Wording must convey: read before `ls` / `grep` for the per-file
      purpose index.
   d. **Confirm via `AskUserQuestion`** before writing. Put the old → new
      diff in the `question` body or a `preview` (option labels are too
      short for a diff). On decline, skip — do not retry. Apply with `Edit`
      on non-empty files; use `Write` on a zero-byte file (Edit cannot
      anchor in empty content).

   Record per-file outcome (wired / absent / already-wired / declined) now —
   sub-agents in step 4 can evict step-2 context. If step 3 or 5 later
   crashes, the wire bullet stays on disk; re-running `/filetree:init` is
   idempotent (step 2.b will see the bullet and skip).

3. **Generate work plan.**
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" todo
   ```
   With no existing manifest, every tracked file lands in `added`. A wired
   `CLAUDE.md` / `AGENTS.md` shows up with its post-wire hash. (A gitignored
   one won't appear — wiring still works on disk, but the manifest only
   tracks files git sees.)

4. **Write summaries.** For each `added` entry: Read the file, write a one-line
   summary per the SKILL.md style guide. Emit only `{path, summary}` — `apply`
   computes hashes from disk, so you never join todo hashes onto summaries.

   This is a from-scratch generation: there is no prior summary, so **every file
   needs a real summary**. `UNCHANGED` is never valid here — that sentinel belongs
   to `/filetree:update` and would be silently dropped by `apply` (init starts from
   an empty manifest, nothing to refresh).

   When `stats.need_llm > 20`, parallelize per the **Part-file protocol** in
   SKILL.md: `mktemp -d`, one Task sub-agent per ~10 files, each writing its own
   `<parts_dir>/part_<i>.json`. Every sub-agent prompt MUST:
   - Tell them to first `Read ${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` for the
     summary **style** and the part-file shape — the "UNCHANGED bias" section there
     is `/filetree:update` scoped and does NOT apply to init.
   - State explicitly: **never output `UNCHANGED`; write a real summary for every file,
     including symlinks and auto-generated files** (judge them by their actual content).

5. **Apply.** For the parallel path, point `apply` at the part files (the shell
   expands the glob; the script merges them):
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" apply <parts_dir>/part_*.json
   ```
   For a small repo done inline, pipe one payload via stdin instead:
   ```json
   {"updates": [{"path": "...", "summary": "..."}], "removals": [], "renames": []}
   ```

6. **Verify coverage, then report.** Inspect `apply`'s return: if
   `missing_from_manifest` is non-empty (a sub-agent dropped a file), or
   `applied < received` / `skipped_unchanged_new` / `skipped_missing_path`
   appears, those files did NOT land — summarize them (no `UNCHANGED`) and
   re-run `apply` (it merges) until `missing_from_manifest` is gone and
   `applied == received`. Then report: total files indexed, files skipped
   (binary / lock), wired files (and skipped with reason: absent /
   already-wired / declined), time taken.

## Do not

- Commit. User reviews `FILETREE.md` and commits manually.
- Write summaries for files in `should_skip` — the script already filters them.
- Create `CLAUDE.md` or `AGENTS.md` if neither exists — that's the user's call.
- Proceed to step 2 if the user declined the overwrite in step 1.
- Wire the same file twice or retry a declined proposal.
- Run `todo` before wiring; that would lock the pre-wire hash into the
  manifest and the next lint would flag phantom drift.
