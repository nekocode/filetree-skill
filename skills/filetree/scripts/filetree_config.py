#!/usr/bin/env python3
"""filetree_config.py — project config + file indexability rules."""

# PEP 604 `str | None` is evaluated at class-body time (the Config dataclass field),
# which would TypeError on Python 3.9; future-annotations keeps annotations lazy so
# the documented `python >= 3.9` floor actually holds.
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MANIFEST_PATH = 'FILETREE.md'
CONFIG_PATH = Path('.filetree.json')

# Binary, asset and lock files — LLM summaries add no value here.
SKIP_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.webp', '.ico', '.svg', '.bmp',
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.mp4', '.mp3', '.wav', '.ogg', '.webm',
    '.zip', '.tar', '.gz', '.bz2', '.7z',
    '.pdf', '.psd', '.ai',
}
SKIP_FILENAMES = {
    'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml',
    'Cargo.lock', 'poetry.lock', 'Pipfile.lock', 'go.sum',
}


def should_skip(path: str) -> bool:
    """Built-in skip: binary/asset extensions and lock files. The manifest itself and
    config include/exclude are layered on in filter_indexable, not here."""
    p = Path(path)
    return p.suffix.lower() in SKIP_EXTENSIONS or p.name in SKIP_FILENAMES


@dataclass
class Config:
    """Project settings from .filetree.json; every field optional.

    exclude/include are gitignore-style; include rescues files from the built-in
    skip; language pins the summary language (None = command auto-detects).
    """
    manifest_path: str = DEFAULT_MANIFEST_PATH
    exclude: list[str] = field(default_factory=list)
    include: list[str] = field(default_factory=list)
    language: str | None = None


_CONFIG_KEYS = {'manifest_path', 'exclude', 'include', 'language'}


def load_config() -> Config:
    """Read .filetree.json into a Config; an absent file means all-default behavior.

    Validation is strict — a typo (`excludes`) or wrong type fails loudly with the
    offending key, never silently ignored. This is the only config entry point.
    """
    if not CONFIG_PATH.exists():
        return Config()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except json.JSONDecodeError as e:
        sys.exit(f"Error: {CONFIG_PATH} is not valid JSON: {e}")
    if not isinstance(raw, dict):
        sys.exit(f"Error: {CONFIG_PATH} must be a JSON object, got {type(raw).__name__}")
    unknown = sorted(set(raw) - _CONFIG_KEYS)
    if unknown:
        sys.exit(
            f"Error: {CONFIG_PATH} has unknown key(s): {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(_CONFIG_KEYS))}"
        )
    cfg = Config()
    if 'manifest_path' in raw:
        cfg.manifest_path = _validate_manifest_path(raw['manifest_path'])
    if 'exclude' in raw:
        cfg.exclude = _validate_str_list(raw['exclude'], 'exclude')
    if 'include' in raw:
        cfg.include = _validate_str_list(raw['include'], 'include')
    if raw.get('language') is not None:
        cfg.language = _validate_language(raw['language'])
    return cfg


def _validate_manifest_path(value) -> str:
    """A non-empty relative path inside the repo; reject absolute / parent escapes."""
    if not isinstance(value, str) or not value.strip():
        sys.exit("Error: .filetree.json `manifest_path` must be a non-empty string")
    p = Path(value)
    if p.is_absolute() or '..' in p.parts:
        sys.exit(
            f"Error: .filetree.json `manifest_path` must be a relative path inside "
            f"the repo, got {value!r}"
        )
    # An existing directory can't be overwritten by the atomic tmp.replace in
    # write_manifest (it would raise IsADirectoryError mid-apply); reject early.
    if p.is_dir():
        sys.exit(
            f"Error: .filetree.json `manifest_path` points at an existing directory, "
            f"not a file: {value!r}"
        )
    # POSIX slashes so it compares equal to git ls-files output on every platform.
    return p.as_posix()


def _validate_str_list(value, key) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        sys.exit(f"Error: .filetree.json `{key}` must be a list of strings")
    return value


def _validate_language(value) -> str:
    if not isinstance(value, str) or not value.strip():
        sys.exit("Error: .filetree.json `language` must be a non-empty string or null")
    return value.strip()


def match_gitignore(paths: list[str], patterns: list[str]) -> set[str]:
    """Paths matching any gitignore-style pattern, evaluated by git itself.

    Delegating to `git check-ignore` (not a hand-rolled matcher) buys faithful
    gitignore semantics for free — anchoring, `**`, negation, trailing-slash dirs.

    Isolation is the load-bearing detail. `core.excludesFile` is the LOWEST-precedence
    ignore source, so the earlier `-c core.excludesFile=...` + source-filter approach
    silently dropped our OWN matches whenever a higher-precedence host source (a tracked
    `.gitignore`, `.git/info/exclude`) hit the same path — exclude/include then no-op'd.
    Instead we evaluate the patterns in a throwaway git repo whose only ignore source is
    a `.gitignore` holding exactly our patterns; the host repo's ignore files are invisible
    there, so our patterns always win. GIT_CONFIG_GLOBAL/SYSTEM=/dev/null pins it further.
    `--no-index` keeps it a pure path-vs-pattern match (the temp paths don't exist on disk).
    """
    if not patterns or not paths:
        return set()
    env = {**os.environ, 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_SYSTEM': os.devnull}
    with tempfile.TemporaryDirectory(prefix='filetree_ignore_') as tmp:
        subprocess.run(['git', 'init', '-q', tmp], check=True, capture_output=True, env=env)
        (Path(tmp) / '.gitignore').write_text('\n'.join(patterns) + '\n', encoding='utf-8')
        proc = subprocess.run(
            ['git', '-C', tmp, 'check-ignore', '--no-index', '--verbose', '-z', '--stdin'],
            input='\0'.join(paths), capture_output=True, encoding='utf-8', env=env,
        )
        # 0 = some matched, 1 = none matched; anything else is a real failure.
        if proc.returncode not in (0, 1):
            raise RuntimeError(f'git check-ignore failed: {proc.stderr.strip()}')
    # Verbose -z record = <source>\0<linenum>\0<pattern>\0<path>, repeated. The only
    # ignore source is our temp .gitignore, so no source filter is needed. A negation
    # (`!foo`) is reported with its final decision "re-included", so it must NOT match.
    fields = proc.stdout.split('\0')
    matched = set()
    for i in range(0, len(fields) - 1, 4):
        _source, _line, pattern, path = fields[i:i + 4]
        if not pattern.startswith('!'):
            matched.add(path)
    return matched


def filter_indexable(paths: list[str], config: Config) -> list[str]:
    """Layered file filter, precedence high → low:

    1. config.exclude   — explicit user removal, wins over everything
    2. the manifest file — never index the manifest's own output
    3. built-in skip     — binary / lock files, UNLESS config.include rescues the path

    The manifest is matched by EXACT path (config.manifest_path), not basename: a stray
    file that merely shares the name (e.g. a nested packages/x/FILETREE.md) is an ordinary
    indexable file, by design — the manifest's identity is its configured path, not a name.
    """
    paths = list(paths)
    excluded = match_gitignore(paths, config.exclude)
    # `included` only ever rescues should_skip paths (built-in binary/lock skip), so only
    # those go through the matcher — piping every code file to git check-ignore is waste.
    skip_set = {p for p in paths if should_skip(p)}
    included = match_gitignore(sorted(skip_set), config.include)
    out = []
    for p in paths:
        if p in excluded:
            continue
        if p == config.manifest_path:
            continue
        if p in skip_set and p not in included:
            continue
        out.append(p)
    return out
