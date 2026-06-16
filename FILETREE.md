# Project Filetree

_Auto-maintained by `/filetree:update`. Content hashes live in the sidecar `FILETREE.hash.json`; do not edit it by hand._

- .claude-plugin
  - `marketplace.json`: Claude Code marketplace manifest; lists the filetree plugin, its source and version for `/plugin marketplace add`
  - `plugin.json`: Plugin manifest; declares name, version, author, license, and the commands/ and skills/ directories
- commands
  - `init.md`: /filetree:init command spec; generates FILETREE.md from scratch, offering config and wiring CLAUDE.md/AGENTS.md
  - `lint.md`: /filetree:lint command spec; runs the read-only drift check and formats results, never calling the LLM
  - `update.md`: /filetree:update command spec; syncs FILETREE.md with repo state under the UNCHANGED bias
- docs
  - `index.html`: Standalone landing page for the filetree plugin; paper-aesthetic marketing site with live manifest demo and copy-install buttons
- hooks
  - `commit_guard.py`: PreToolUse hook guard; blocks a Claude-issued git commit while FILETREE.md is stale, deferring the fix to /filetree:update (opt-in, fail-open)
  - `hooks.json`: Plugin hook registration; wires commit_guard.py to the PreToolUse Bash event
- skills
  - filetree
    - scripts
      - `filetree.py`: Core CLI; deterministic todo/apply/lint/wire-target operations that diff the repo against the manifest and write it
      - `filetree_config.py`: .filetree.json parsing and file-indexability rules; skip lists, gitignore-style exclude/include, and the hash sidecar path
    - `SKILL.md`: Shared rules for /filetree:init and /filetree:update; summary style, language resolution, UNCHANGED bias, and work-plan processing
- tests
  - `conftest.py`: Pytest fixtures; loads filetree.py via importlib and provides an isolated git_repo fixture
  - `test_commit_guard.py`: Unit tests for commit_guard.py; git-commit detection, fail-open decision off-ramps, lint exit-code mapping, and deny-payload shape
  - `test_filetree.py`: Unit and integration tests for filetree.py; manifest parse/write round-trips and end-to-end todo→apply flows
  - `test_filetree_config.py`: Unit tests for filetree_config.py; skip rules, .filetree.json validation, gitignore matching, and filter_indexable
- `.filetree.json`: Project config for this repo; pins manifest_path=FILETREE.md and summary language to English
- `.gitignore`: Git ignore rules; excludes Python build/cache, test/coverage artifacts, env files, and local planning docs
- `README.md`: English project README; rationale, install, commands, .filetree.json config, manifest format, and development guide
- `README.zh.md`: Chinese project README; mirror of README.md covering rationale, install, commands, config, and development
