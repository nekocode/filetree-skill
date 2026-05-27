---
name: filetree
description: >
  Shared rules for the filetree plugin — summary style, UNCHANGED bias for
  manifest updates, parallelization strategy. Referenced by /filetree:init
  and /filetree:update commands; not invoked directly.
license: MIT
---

# Filetree Skill — Shared Rules

Cross-cutting rules used by `/filetree:init` and `/filetree:update`. The
commands themselves contain step-by-step flows; this file holds rules that
apply across modes so they're maintained in one place.

`/filetree:lint` is read-only script invocation and does not need these rules.

---

## Summary style

One line, max 25 words, describes what the file is FOR (its role / purpose).
Not what it implements internally.

- Good: "JWT auth middleware; parses token from request header and injects user_id into context"
- Bad: "Defines AuthMiddleware class with __init__ and __call__ methods"
- Bad: "Handles auth" (too vague)

Present tense. Match the language (Chinese / English) of existing entries.
No marketing words.

---

## UNCHANGED bias (for /filetree:update ONLY)

> **Scope.** This entire section applies to `/filetree:update` only. During
> `/filetree:init` the manifest starts empty, so there is no old summary to
> keep — `UNCHANGED` has nothing to refresh and `apply` will drop it. In init,
> every file gets a real summary. Do not apply this bias to init sub-tasks.

**Why this matters.** Hash changes trigger the LLM, but most code changes
(typos, refactors, comments, small additions) don't change a file's purpose.
Outputting `"UNCHANGED"` lets `cmd_apply` refresh just the hash and keep the
existing summary — the manifest itself carries the memory of "I already
reviewed this version". In a healthy update run, 80%+ of `changed` items
should resolve to UNCHANGED. Writing a fresh 25-word summary when the old one
still fits wastes ~100x more tokens than a 4-byte `"UNCHANGED"` reply.

**Decision rule.** You have: old summary, old hash, new hash, and the file's
new content (prefer reading the `git diff` over the full file — diff is far
denser per token and is all you need for purpose-level judgement).

Output `"UNCHANGED"` if the old summary still describes the file's PURPOSE.
Refactors, renames, bug fixes, test additions, formatting, comment changes,
small additions — these almost always leave the purpose intact.

Output a new summary string only if:
- A major new feature has been added that meaningfully expands purpose
- A previously central concern has been removed
- The file has been substantially rewritten for a different goal

When in doubt, output UNCHANGED.

---

## Symlinks

Some `added` / `changed` items carry a `symlink_target` field. For those:
**do not Read the file** — a Read follows the link to the target's content
(wasteful, and fails on a broken link). Write exactly `symlink → <target>`
using the supplied `symlink_target`; do not infer a role you can't see.
The script already hashes symlinks correctly from the link string.

---

## Processing the work plan (`todo --split`)

Always run `todo --split` (the script chunks the LLM work and writes it to
files, so you never count, truncate, or hand-split). Output:

```json
{ "stats": {...}, "removed": [...], "renamed": [...],
  "split_dir": "/tmp/filetree_XXXX",
  "batches": [{"file": ".../batch_00.json", "count": 25}, ...] }
```

Each `batch_NN.json` is a JSON array of todo items (added + changed). Drive it
purely off `batches`:

- **0 batches** → no LLM work; no part files exist to glob, so apply the empty
  payload via stdin (it still syncs removed/renamed from repo state):
  ```bash
  echo '{"updates": []}' | python .../filetree.py apply
  ```
- **1 batch** → process it inline yourself: Read the one batch file, decide each
  item, write `<split_dir>/part_00.json`.
- **multiple batches** → spawn one `claude-haiku-4-5` sub-agent per batch (good
  enough, ~10x cheaper). Each sub-agent: `Read` SKILL.md, `Read` its assigned
  `batch_NN.json`, then write `<split_dir>/part_NN.json`. Sub-agents run in
  parallel and never see each other's batch.

### Part-file shape (no hand-merging, no hashes)

Each `part_NN.json` carries ONLY summaries — `hash` is computed from disk,
removed/renamed are recomputed from repo state, so neither belongs here:

```json
{"updates": [{"path": "...", "summary": "..." | "UNCHANGED"}]}
```

Apply all parts in one call (the shell expands the glob, the script merges):

```bash
python .../filetree.py apply <split_dir>/part_*.json
```

### Coverage check = `missing_from_manifest`, nothing else

`apply` returns `missing_from_manifest` listing any indexable file still without
an entry (a dropped sub-agent output, a forgotten file). Non-empty → summarize
those into one more part and re-run `apply` (it merges) until the key is absent.
Do NOT hand-roll a coverage diff (concatenating batch lists, comparing counts) —
it is redundant and error-prone. `applied < received`, `skipped_unchanged_new`,
or `skipped_missing_path` flag other anomalies the same way.
