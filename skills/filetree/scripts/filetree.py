#!/usr/bin/env python3
"""filetree.py — deterministic operations for FILETREE.md maintenance."""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MANIFEST_PATH = Path('FILETREE.md')

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
    'FILETREE.md',
}

# Entry format: - `filename` — summary <!--hash:xxxxxxxx-->
ENTRY_RE = re.compile(r'^- `([^`]+)` — (.+?) <!--hash:([a-f0-9]+)-->\s*$')
SECTION_RE = re.compile(r'^## (.+?)/?\s*$')


def require_git():
    """Require a git repository; all change detection depends on git."""
    try:
        subprocess.run(
            ['git', 'rev-parse', '--git-dir'],
            check=True, capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.exit(
            "Error: This skill requires the project to be a git repository.\n"
            "       Run `git init && git add . && git commit -m \"initial\"` first."
        )


def should_skip(path: str) -> bool:
    """Skip binary extensions and lock files."""
    p = Path(path)
    return p.suffix.lower() in SKIP_EXTENSIONS or p.name in SKIP_FILENAMES


def list_current_files() -> list[str]:
    """Tracked + untracked-unignored files, deduped and sorted."""
    tracked = subprocess.check_output(
        ['git', 'ls-files'], text=True,
    ).splitlines()
    untracked = subprocess.check_output(
        ['git', 'ls-files', '--others', '--exclude-standard'], text=True,
    ).splitlines()
    all_files = set(tracked) | set(untracked)
    return sorted(f for f in all_files if f and not should_skip(f))


def hash_files(paths: list[str]) -> dict[str, str]:
    """Batch `git hash-object`; returns {path: 8-char hash}."""
    if not paths:
        return {}
    out = subprocess.check_output(
        ['git', 'hash-object'] + paths, text=True,
    ).strip().splitlines()
    return {p: h[:8] for p, h in zip(paths, out)}


def detect_renames() -> list[tuple[str, str]]:
    """Parse rename pairs from `git status`; trust git's default 50% similarity."""
    out = subprocess.check_output(
        ['git', 'status', '--porcelain=v1'], text=True,
    )
    renames = []
    for line in out.splitlines():
        if line[:2].strip().startswith('R'):
            rest = line[3:]
            if ' -> ' in rest:
                old, new = rest.split(' -> ', 1)
                renames.append((old.strip(), new.strip()))
    return renames


def parse_manifest() -> list[dict]:
    """Read FILETREE.md into [{path, summary, hash}]."""
    if not MANIFEST_PATH.exists():
        return []
    entries = []
    section = ''
    for line in MANIFEST_PATH.read_text(encoding='utf-8').splitlines():
        m = SECTION_RE.match(line)
        if m:
            section = m.group(1).strip().rstrip('/')
            if section == '(root)':
                section = ''
            continue
        m = ENTRY_RE.match(line)
        if m:
            filename, summary, h = m.groups()
            # Backward-compat: legacy entries stored the full path.
            if '/' in filename:
                full_path = filename
            elif section:
                full_path = f'{section}/{filename}'
            else:
                full_path = filename
            entries.append({
                'path': full_path,
                'summary': summary.strip(),
                'hash': h,
            })
    return entries


def write_manifest(entries: list[dict]) -> None:
    """Group by directory, sort stably, write back to FILETREE.md."""
    by_dir: dict[str, list[dict]] = {}
    for e in entries:
        d = str(Path(e['path']).parent)
        if d == '.':
            d = ''
        by_dir.setdefault(d, []).append(e)

    lines = [
        '# Project Filetree',
        '',
        '_Auto-maintained by `/filetree:update`. Each entry carries a content hash; mismatched hashes indicate stale summaries._',
        '',
    ]

    for d in sorted(by_dir):
        heading = f'{d}/' if d else '(root)/'
        lines.append(f'## {heading}')
        lines.append('')
        for e in sorted(by_dir[d], key=lambda x: x['path']):
            filename = Path(e['path']).name
            lines.append(
                f"- `{filename}` — {e['summary']} <!--hash:{e['hash']}-->"
            )
        lines.append('')

    MANIFEST_PATH.write_text('\n'.join(lines), encoding='utf-8')


def cmd_todo() -> dict:
    """Diff current files vs manifest; emit the LLM todo list."""
    require_git()
    current_paths = set(list_current_files())
    manifest = parse_manifest()
    manifest_by_path = {e['path']: e for e in manifest}

    renames_raw = detect_renames()
    renames = [
        {'old_path': o, 'new_path': n}
        for o, n in renames_raw
        if o in manifest_by_path and not should_skip(n)
    ]
    renamed_olds = {r['old_path'] for r in renames}
    renamed_news = {r['new_path'] for r in renames}

    added_paths = sorted(current_paths - set(manifest_by_path) - renamed_news)
    removed = sorted(set(manifest_by_path) - current_paths - renamed_olds)
    common = sorted(current_paths & set(manifest_by_path))

    to_hash = common + added_paths
    hashes = hash_files(to_hash)

    changed = []
    for p in common:
        if hashes[p] != manifest_by_path[p]['hash']:
            changed.append({
                'path': p,
                'old_summary': manifest_by_path[p]['summary'],
                'old_hash': manifest_by_path[p]['hash'],
                'new_hash': hashes[p],
            })

    added = [{'path': p, 'hash': hashes[p]} for p in added_paths]

    return {
        'added': added,
        'changed': changed,
        'removed': removed,
        'renamed': renames,
        'stats': {
            'total_in_repo': len(current_paths),
            'total_in_manifest': len(manifest_by_path),
            'need_llm': len(added) + len(changed),
        },
    }


def cmd_apply(updates_json: str) -> dict:
    """Apply LLM decisions to the manifest. UNCHANGED refreshes hash only."""
    require_git()
    updates = json.loads(updates_json)
    manifest = parse_manifest()
    by_path = {e['path']: e for e in manifest}

    # Rehash the new path: renames often carry small content edits.
    for r in updates.get('renames', []):
        old, new = r['old_path'], r['new_path']
        if old in by_path:
            entry = by_path.pop(old)
            entry['path'] = new
            entry['hash'] = hash_files([new]).get(new, entry['hash'])
            by_path[new] = entry

    for p in updates.get('removals', []):
        by_path.pop(p, None)

    for u in updates.get('updates', []):
        p = u['path']
        h = u['hash']
        s = u['summary']
        # UNCHANGED contract: refresh hash, keep old summary — linchpin of the cacheless design.
        if s == 'UNCHANGED' and p in by_path:
            by_path[p]['hash'] = h
        else:
            by_path[p] = {'path': p, 'hash': h, 'summary': s}

    write_manifest(list(by_path.values()))
    return {'total_entries': len(by_path)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['todo', 'lint', 'apply'])
    args = parser.parse_args()

    if args.command in ('todo', 'lint'):
        result = cmd_todo()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == 'lint':
            # CI-friendly: exit 1 on drift.
            drift = (
                len(result['added']) + len(result['changed'])
                + len(result['removed']) + len(result['renamed'])
            )
            sys.exit(0 if drift == 0 else 1)
    elif args.command == 'apply':
        result = cmd_apply(sys.stdin.read())
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':  # pragma: no cover - CLI entry; tests call main() directly.
    main()
