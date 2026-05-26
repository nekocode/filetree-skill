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
   summary per the SKILL.md style guide.

   When `stats.need_llm > 20`, use Task sub-agents (one per ~10 files). Sub-agents
   run with isolated context, so each sub-agent prompt MUST instruct them to first
   `Read ${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` to internalize the summary
   style before writing — otherwise shared rules won't apply to their output.

5. **Apply.** Pipe the decision JSON to stdin:
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

6. **Report.** Total files indexed, files skipped (binary / lock), wired
   files (and skipped with reason: absent / already-wired / declined),
   time taken.

## Do not

- Commit. User reviews `FILETREE.md` and commits manually.
- Write summaries for files in `should_skip` — the script already filters them.
- Create `CLAUDE.md` or `AGENTS.md` if neither exists — that's the user's call.
- Proceed to step 2 if the user declined the overwrite in step 1.
- Wire the same file twice or retry a declined proposal.
- Run `todo` before wiring; that would lock the pre-wire hash into the
  manifest and the next lint would flag phantom drift.
