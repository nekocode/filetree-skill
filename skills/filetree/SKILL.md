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

## UNCHANGED bias (for /filetree:update)

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

## Parallelization

If `stats.need_llm > 20`, use the Task tool to parallelize summary
generation. Spawn one sub-agent per ~10 files. Use claude-haiku-4-5
for sub-agents (good enough, ~10x cheaper than Sonnet / Opus).
