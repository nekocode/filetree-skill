# filetree

[中文版](README.zh.md)

A Claude Code plugin that maintains `FILETREE.md` — a one-line description per file with content hashes for staleness detection. Lets the LLM grasp repo layout in a few hundred tokens before touching code.

## Why

Every new Claude Code session relearns the codebase: `ls`, `grep`, open file, read, repeat. The discovery is expensive and not reusable across sessions.

| Pain point | filetree's answer |
|-----|-----|
| LLM keeps rediscovering layout each session | Persisted as `FILETREE.md`, checked into git, shared with collaborators |
| Summary docs go stale silently | Per-entry content hash; mismatch reveals drift immediately |
| Rewriting every description on small refactors wastes tokens | `UNCHANGED` bias — LLM refreshes hash only when purpose is intact (~100x cheaper) |
| Tooling adds a sqlite / daemon / watcher | Single markdown file. Change detection delegated to git. Zero background process |

## Install

**Via marketplace:**

```
/plugin marketplace add nekocode/filetree-skill
/plugin install filetree
```

**Local development / dog-fooding** (no install needed):

```sh
cd /path/to/filetree-skill
claude --plugin-dir .
```

After editing `commands/` or `SKILL.md`, run `/reload-plugins` inside the session for a hot reload.

## Commands

| Command | Purpose |
|---------|---------|
| `/filetree:init` | Generate `FILETREE.md` from scratch. Refuses to overwrite without confirmation |
| `/filetree:update` | Sync `FILETREE.md` with current repo state (added / changed / removed / renamed) |
| `/filetree:lint` | Read-only drift check. Exits non-zero on drift, CI-friendly. **Does not call the LLM** |

All commands refuse to commit `FILETREE.md`. You review the diff and commit yourself.

## Wire it into CLAUDE.md / AGENTS.md

`/filetree:init` handles this on first run — it scans the repo root for `CLAUDE.md` / `AGENTS.md`, skips files already referencing `FILETREE.md`, and for the rest proposes a bullet whose location and style matches the existing file. You confirm each edit before it lands. Because wiring runs **before** the manifest is hashed, the post-wire content is what enters `FILETREE.md` directly — no second-pass refresh.

Caveats:

- If neither `CLAUDE.md` nor `AGENTS.md` exists, the plugin won't create one — it's your call which (if any) to seed. Create the file you want, then re-run `/filetree:init`.
- Wiring runs at init time only. If you add `CLAUDE.md` / `AGENTS.md` later, re-run `/filetree:init` (it'll ask before overwriting `FILETREE.md`) or wire by hand.

To wire by hand, drop a line like this into your `CLAUDE.md`:

```markdown
- `./FILETREE.md` — Per-file purpose index. Read before `ls` / `grep` when overviewing the repo or locating an implementation.
```

The agent then treats `FILETREE.md` as a cheap index — one read replaces dozens of `ls` / `grep` / `cat` calls during orientation.

## How It Works

### Manifest format

```markdown
# Project Filetree

_Auto-maintained by `/filetree:update`. Each entry carries a content hash; mismatched hashes indicate stale summaries._

## src/auth/

- `middleware.py` — JWT validation middleware; parses bearer token and injects user_id into request context <!--hash:a1b2c3d4-->
- `jwt_utils.py` — Pure JWT signing / verification helpers, framework-agnostic <!--hash:e5f6g7h8-->

## (root)/

- `README.md` — Project entry doc <!--hash:9a8b7c6d-->
```

- Section header = directory path (trailing `/`); root files live under `(root)/`
- Each entry stores filename only (not the full path) + summary + 8-char content hash (from `git hash-object`)
- Stable sort (sections + entries) → no spurious diffs

### Data flow (`/filetree:update`)

```
filetree.py todo
  ├─ git ls-files (tracked + untracked, exclude .gitignore)
  ├─ git hash-object on all paths
  ├─ git status --porcelain  (rename detection, trust git 50% heuristic)
  └─ diff vs current FILETREE.md
        ↓ JSON
{added, changed, removed, renamed, stats.need_llm}
        ↓
LLM processes added (write fresh summary)
            changed (UNCHANGED or new summary)
        ↓ JSON via stdin
filetree.py apply
  ├─ UNCHANGED → refresh hash only, keep summary
  ├─ new summary → overwrite entry
  ├─ rename → move entry + rehash
  └─ write FILETREE.md
```

### UNCHANGED bias

On a healthy `update`, **80%+ of `changed` items should resolve to `"UNCHANGED"`** — refactors, formatting, comment edits, bug fixes, small additions almost always leave a file's purpose intact. The LLM emits a 4-byte `"UNCHANGED"` reply; `apply` refreshes the hash and keeps the old summary. The manifest itself carries the memory of "I already reviewed this version" — no separate cache needed.

## Compatibility

| Requirement | Version | Notes |
|---|---|---|
| `git` | any modern release | Required at runtime; non-git repos fail fast with a clear error |
| `python` | ≥ 3.9 | Uses PEP 585 `list[dict]` builtin generics. Stdlib only — zero third-party deps |
| Claude Code | any | Plugin format. `claude` is shipped as a native binary; Node is not required |

## Development

```sh
# install pytest if you don't have it
python -m pip install pytest pytest-cov

# run tests
python -m pytest tests/ -q

# with coverage (target: 100% lines)
python -m pytest tests/ --cov=filetree --cov-report=term-missing
```

Tests load the script via `importlib` (see `tests/conftest.py`), so no package install is needed.

Lint your own `FILETREE.md` while iterating:

```sh
python skills/filetree/scripts/filetree.py lint
```

Exit code 1 = drift, 0 = clean. Wire it into pre-commit or CI as needed:

```yaml
# .github/workflows/filetree.yml
- run: python skills/filetree/scripts/filetree.py lint
```

## Non-Goals

To prevent scope creep — filetree explicitly **does not**:

- Track function / class / hunk-level changes (file-level is the resolution)
- Build a semantic search or vector index
- Run a watcher / daemon / background process
- Auto-commit (review power stays with you)
- Map dependencies between files (not a call graph)

## License

MIT. See `.claude-plugin/plugin.json`.
