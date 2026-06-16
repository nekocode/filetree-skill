---
description: Generate FILETREE.md from scratch. Confirms overwrite if it already exists.
allowed-tools: Read, Edit, Write, Bash(python3:*), Task, AskUserQuestion
---

Generate FILETREE.md from scratch for the current repository.

**First**, read the shared rules at
`${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` — it defines the summary
style, **summary language**, UNCHANGED bias (not used here, but good to
internalize for future `/filetree:update` calls), and parallelization strategy.

Conduct this whole command — your own narration AND every summary — in the
project's canonical language. Resolve it ONCE per SKILL.md "Summary language"
(don't restate the chain here) — `config.language` from step 3's `todo` output
wins when set. Its next source is `CLAUDE.md` / `AGENTS.md`: step 2 opens them
only on the wire path — reuse that content if it did, else read now.

The manifest path is configurable via `.filetree.json`. The script is the only
config parser: take `manifest_path` from the `wire-target` / `todo` output below
and use it everywhere this doc says "the manifest" — never assume `FILETREE.md`,
never re-parse `.filetree.json` yourself.

## Steps

1. **Offer config, resolve targets, check existing.** Config comes first because it
   decides where the manifest goes and what gets indexed — it must be settled before
   anything is built.

   a. **Create the config, then confirm.** If `.filetree.json` does NOT exist, `Write`
      the default template below (it equals current defaults, so it changes nothing
      yet). Then use `AskUserQuestion` to ask whether to keep it. **That open prompt is
      the user's edit window** — while it waits, they can edit the file now on disk
      (relocate the manifest, add `exclude` / `include`, pin a `language`), and
      `wire-target` / `todo` below will pick up whatever they save. On **keep**,
      proceed. On **discard**, delete the file you just created
      (`python3 -c "import os; os.remove('.filetree.json')"`) and proceed with defaults.
      If `.filetree.json` ALREADY exists, do nothing here — never create, edit, or
      remove a pre-existing one (it's the user's, and may already be customized).
      ```json
      {
        "manifest_path": "FILETREE.md",
        "exclude": [],
        "include": [],
        "language": null
      }
      ```

   b. Then run `wire-target` — it returns the configured `manifest_path`,
      `manifest_exists`, and for each of `CLAUDE.md` / `AGENTS.md`
      `{exists, is_symlink, real_path, matches}`:
      ```bash
      python3 "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" wire-target
      ```
      If `manifest_exists` is `true`, ask the user to confirm overwrite (they likely
      meant `/filetree:update`); on decline, stop — do not enter step 2. Skip the
      prompt when `false`.

2. **Wire `CLAUDE.md` / `AGENTS.md`.** Do this **before** `todo` so the
   manifest captures the post-wire hash; otherwise the first `/filetree:lint`
   flags the wired file as drifted. Reuse the `wire-target` output from step 1
   (these files are often symlinks → editing the link path fails, so always edit
   `real_path`). Then per file:

   a. **`exists: false`** → skip; do not create.
   b. **Already wired.** `matches` lists every line mentioning the manifest (the
      script searched for the configured `manifest_path` name). Skip if one is a
      real reference — a backticked path, link, or bullet. Bare prose and negative
      warnings (`do not edit ...`) do NOT count.
   c. **Otherwise propose an edit to `real_path`** (NEVER the link name — Edit
      refuses to write through a symlink). Read `real_path` in full. The bullet
      must reference the configured `manifest_path` (not a hardcoded FILETREE.md).
      If a section's bullets are `./*.md` paths (`## References`, `## 引用`,
      `## Documentation`, `## Project layout`), append a matching bullet there;
      else append a short new section at end. Match the file's language and bullet
      style (full-width `——` in zh files). Convey: read before `ls` / `grep` for
      the per-file index.
   d. **Confirm via `AskUserQuestion`** before writing. Put the old → new diff in
      the `question` body or a `preview`. On decline, skip — do not retry. `Edit`
      `real_path` (use `Write` only on a zero-byte file).

   Record per-file outcome (wired / absent / already-wired / declined) now —
   sub-agents in step 4 can evict step-2 context. If step 3 or 5 later
   crashes, the wire bullet stays on disk; re-running `/filetree:init` is
   idempotent (step 2.b's `matches` will show the bullet and skip).

3. **Generate work plan.** One call; the script chunks and writes the work to
   files (you never count or split):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" todo --split
   ```
   With no existing manifest, every tracked file is new work, written into the
   `batches` files (the full `added` / `changed` lists are omitted from `--split`
   stdout — drive off `batches`). The output also carries the `config` block
   (`manifest_path`, `language`) — use `config.language` for the canonical language if
   set. A wired
   `CLAUDE.md` / `AGENTS.md` shows up with its post-wire hash. (A gitignored one
   won't appear — wiring still works on disk, but the manifest only tracks files
   git sees. `exclude`/`include` from `.filetree.json` are already applied.)

4. **Write summaries.** Process the batches per SKILL.md "Processing the work
   plan" (0 → skip to apply; 1 → inline; many → one `claude-haiku-4-5` sub-agent
   per batch). For each item: Read the file, write a one-line summary per the
   SKILL.md style guide. Items with a `symlink_target` field: do not Read — write
   `symlink → <target>`.

   This is a from-scratch generation: there is no prior summary, so **every file
   needs a real summary**. `UNCHANGED` is never valid here — that sentinel belongs
   to `/filetree:update` and would be silently dropped by `apply` (init starts from
   an empty manifest, nothing to refresh).

   Every sub-agent prompt MUST:
   - Tell them to first `Read ${CLAUDE_PLUGIN_ROOT}/skills/filetree/SKILL.md` for the
     summary **style** and part-file shape, then Read their assigned `batch_NN.json`
     and write `<split_dir>/part_NN.json` — the "UNCHANGED bias" section there is
     `/filetree:update` scoped and does NOT apply to init.
   - **State the canonical language explicitly: "Write all summaries in <language>."**
     Fill in the language you resolved above — sub-agents must not re-detect it, or
     parallel batches diverge and the manifest mixes languages.
   - State explicitly: **never output `UNCHANGED`. For every NON-symlink file write a
     real summary judged by its actual content** (including auto-generated files).
     For items with a `symlink_target` field, do NOT Read — write `symlink → <target>`.

5. **Apply** all parts in one call (shell expands the glob; the script merges and
   computes hashes from disk):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/skills/filetree/scripts/filetree.py" apply <split_dir>/part_*.json
   ```
   Part files carry only `{"updates": [{"path", "summary"}]}`. For the inline
   1-batch case you may instead pipe that one payload via stdin. (An empty repo
   yields 0 batches — there are no part files to glob; pipe `{"updates": []}` via
   stdin instead.)

6. **Verify coverage, then report.** The completion gate is `missing_from_manifest`
   being empty (an indexable file with no entry — a sub-agent dropped it). If it's
   non-empty, summarize those files (no `UNCHANGED`) and re-run `apply` (it merges)
   until it clears. `skipped_unchanged_new` / `skipped_missing_path` flag bad
   summaries (a wrong `UNCHANGED`, or a hallucinated path) — fix and re-apply those.
   Ignore `skipped_excluded` (real files the config keeps out — nothing to fix; do
   NOT loop on `applied == received`, which these legitimately hold below). Then
   report: total files indexed, files skipped (binary / lock / excluded), wired files
   (and skipped with reason: absent / already-wired / declined), time taken.

## Do not

- Commit. User reviews the manifest and commits manually.
- Touch a pre-existing `.filetree.json` — create (and offer to discard) the scaffold only when none exists.
- Write summaries for files in `should_skip` — the script already filters them.
- Create `CLAUDE.md` or `AGENTS.md` if neither exists — that's the user's call.
- Proceed to step 2 if the user declined the overwrite in step 1.
- Wire the same file twice or retry a declined proposal.
- Run `todo` before wiring; that would lock the pre-wire hash into the
  manifest and the next lint would flag phantom drift.
