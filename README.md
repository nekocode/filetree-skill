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

`/filetree:init` handles this on first run — it scans the repo root for `CLAUDE.md` / `AGENTS.md`, skips files already referencing `FILETREE.md`, and for the rest proposes a bullet whose location and style matches the existing file. You confirm each edit before it lands.

Caveats:

- If neither `CLAUDE.md` nor `AGENTS.md` exists, the plugin won't create one — it's your call which (if any) to seed. Create the file you want, then re-run `/filetree:init`.
- Wiring runs at init time only. If you add `CLAUDE.md` / `AGENTS.md` later, re-run `/filetree:init` (it'll ask before overwriting `FILETREE.md`) or wire by hand.

To wire by hand, drop a line like this into your `CLAUDE.md`:

```markdown
- `./FILETREE.md` — Per-file purpose index. Read before `ls` / `grep` when overviewing the repo or locating an implementation.
```

The agent then treats `FILETREE.md` as a cheap index — one read replaces dozens of `ls` / `grep` / `cat` calls during orientation.

## Project config (`.filetree.json`)

Optional. Drop a `.filetree.json` at the repo root and commit it to share with the team. Absent → defaults.

```json
{
  "manifest_path": "docs/FILETREE.md",
  "exclude": ["migrations/", "**/*.gen.ts", "/build"],
  "include": ["*.svg"],
  "language": "zh"
}
```

| Key | Effect | Default |
|---|---|---|
| `manifest_path` | Where the manifest is written (relative path inside the repo) | `FILETREE.md` |
| `exclude` | gitignore-style patterns to keep tracked files OUT of the manifest | `[]` |
| `include` | gitignore-style patterns to index files normally skipped (e.g. `*.svg`) | `[]` |
| `language` | Pin the summary language (e.g. `"zh"`) instead of auto-detecting | `null` |

`exclude` / `include` accept full gitignore syntax (`/build`, `**`, `!keep.gen.ts`, trailing-slash dirs). Invalid config fails fast with a clear error.

## Manifest format

```markdown
# Project Filetree

_Auto-maintained by `/filetree:update`. Content hashes live in the sidecar `FILETREE.hash.json`; do not edit it by hand._

- src
  - auth
    - `middleware.py`: JWT validation middleware; parses bearer token and injects user_id into request context
    - `jwt_utils.py`: Pure JWT signing / verification helpers, framework-agnostic
- `README.md`: Project entry doc
```

- Nested unordered list = directory tree; 2 spaces per depth level
- Directory line `- name` is structural; file line `` - `name`: summary `` — pure prose, no inline noise
- Per level: directories first, then files, each lexical → no spurious diffs
- Content hashes are stored out-of-band in `FILETREE.hash.json` (`{path: hash}`), keeping the manifest ~18% smaller and free of per-line hex noise. A pre-sidecar manifest with inline `<!--hash:-->` is auto-migrated on the next update.

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

## License

MIT. See `.claude-plugin/plugin.json`.
