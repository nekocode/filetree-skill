#!/usr/bin/env python3
"""commit_guard.py — PreToolUse hook that blocks a stale-manifest commit.

Wired by hooks/hooks.json on `PreToolUse` / matcher `Bash`. The flow:

    Claude runs `git commit`  ->  this guard runs `filetree.py lint`
      lint clean (exit 0)      ->  allow the commit
      lint drift (exit 1)      ->  deny, telling Claude to /filetree:update first

A hook can only run shell, never an LLM skill. So the guard cannot *fix* the
manifest; it blocks and hands the fix back to Claude, who runs the update skill
and retries the commit. lint is the deterministic gate, update is the repair.

Two deliberate properties:
  * Opt-in. No-op unless `.filetree.json` sets `"commit_guard": true`. Installing
    the plugin must never silently gate anyone's commits.
  * Fail-open. Any malfunction (bad stdin, non-git tree, broken config, lint
    crash) allows the commit. A guard bug must never wedge the user's workflow.

Only fires for commits Claude issues through the Bash tool — a manual `git
commit` in the user's own terminal never reaches a Claude Code hook.

Two-stage filter, to keep this off the Bash hot path. A PreToolUse matcher can
only match the tool *name* (Bash), not the command, so the hook would otherwise
start python on every Bash call. hooks.json does a coarse shell pre-filter first
(only a payload containing "commit" ever reaches python); this script then does
the precise `git commit` check. So a non-commit Bash call never starts python.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# scripts/ is not a package; put it on the path so the one config parser is reused
# (never re-implement .filetree.json reading — see filetree_config.load_config).
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PLUGIN_ROOT / 'skills' / 'filetree' / 'scripts'
sys.path.insert(0, str(SCRIPTS_DIR))

LINT_SCRIPT = SCRIPTS_DIR / 'filetree.py'

import filetree_config  # noqa: E402 — must follow the sys.path.insert above

# Shell operators that separate one command from the next. We test each segment
# independently so `git add . && git commit -m x` is recognized via its 2nd segment.
_SEGMENT_SPLIT = re.compile(r'&&|\|\||[;\n|&]')

# git global options (before the subcommand) that consume the FOLLOWING token as
# their value; we must skip both to reach the subcommand. The `--opt=value` form
# is a single token handled by the generic `startswith('-')` branch.
_VALUE_OPTS = {'-C', '-c', '--git-dir', '--work-tree', '--namespace', '--super-prefix'}

# Benign command wrappers that may precede `git`; skipped when finding the command
# word so `env FOO=bar git commit` is still seen as a commit.
_WRAPPERS = {'sudo', 'command', 'nice', 'env', 'time', 'nohup', 'stdbuf'}

_ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')

DENY_REASON = (
    'FILETREE.md is stale (filetree drift detected). Before committing, run '
    '/filetree:update to refresh the manifest, `git add` the regenerated '
    'FILETREE.md and FILETREE.hash.json, then retry the commit. '
    '(To turn off this gate, set "commit_guard": false in .filetree.json.)'
)


def _command_word_index(tokens: list[str]) -> int:
    """Index of a segment's real command word, skipping leading `VAR=val`
    assignments and benign wrappers (sudo/env/...). len(tokens) if none."""
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if _ASSIGNMENT.match(tok) or tok in _WRAPPERS:
            i += 1
            continue
        return i
    return i


def _segment_is_git_commit(segment: str) -> bool:
    """True if a single command segment is a `git commit` invocation."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        # Unbalanced quotes etc. — fall back to a loose token split so an odd
        # command still gets a best-effort check rather than silently passing.
        tokens = segment.split()
    start = _command_word_index(tokens)
    if start >= len(tokens) or os.path.basename(tokens[start]) != 'git':
        return False
    i = start + 1
    # Walk past global options to the subcommand.
    while i < len(tokens):
        tok = tokens[i]
        if tok in _VALUE_OPTS:
            i += 2
            continue
        if tok.startswith('-'):
            i += 1
            continue
        return tok == 'commit'
    return False


def is_git_commit(command: str) -> bool:
    """True if any segment of a (possibly compound) shell command is `git commit`."""
    return any(_segment_is_git_commit(seg) for seg in _SEGMENT_SPLIT.split(command))


def _repo_root() -> str | None:
    """Repo top-level path, or None if not inside a git work tree."""
    try:
        out = subprocess.run(
            ['git', 'rev-parse', '--show-toplevel'],
            check=True, capture_output=True, encoding='utf-8',
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return out or None


def _lint_drift() -> bool:
    """Run `filetree.py lint`; True only on its exit-1 drift signal. Any other
    code (0 clean, 2+ crash) is treated as no-block to honor fail-open."""
    proc = subprocess.run(
        [sys.executable, str(LINT_SCRIPT), 'lint'],
        capture_output=True, encoding='utf-8',
    )
    return proc.returncode == 1


def decide(raw_stdin: str) -> dict | None:
    """Map a PreToolUse stdin payload to a deny dict, or None to allow.

    None means allow at every off-ramp (not a commit, not git, guard disabled,
    manifest clean, or anything went wrong) — fail-open by construction.

    Side effect: on a guarded git commit this chdirs to the repo root, since
    both load_config (relative .filetree.json) and lint resolve from there. The
    hook runs as a short-lived subprocess, so the cwd change dies with it.

    The config+lint stage is wrapped in a catch-all that swallows BOTH SystemExit
    (load_config exits on a malformed .filetree.json) and ordinary exceptions (an
    unreadable config, a lint that won't spawn). A guard malfunction must allow
    the commit, never block it — so any failure here resolves to None.
    """
    try:
        command = json.loads(raw_stdin).get('tool_input', {}).get('command', '')
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None
    if not command or not is_git_commit(command):
        return None
    top = _repo_root()
    if top is None:
        return None
    try:
        os.chdir(top)
        if not filetree_config.load_config().commit_guard:
            return None
        if not _lint_drift():
            return None
    except (SystemExit, Exception):
        return None
    return {
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'permissionDecision': 'deny',
            'permissionDecisionReason': DENY_REASON,
        }
    }


def main():
    # Always exit 0: a "deny" is carried by the JSON below, never by exit code,
    # so a guard error can never masquerade as a block (exit 2 would block too).
    # The outer guard extends fail-open to the IO decide() can't see — a broken
    # stdin pipe on read, or a closed stdout on print.
    try:
        decision = decide(sys.stdin.read())
        if decision is not None:
            print(json.dumps(decision))
    except Exception:
        pass


if __name__ == '__main__':  # pragma: no cover - CLI entry; tests call helpers directly.
    main()
