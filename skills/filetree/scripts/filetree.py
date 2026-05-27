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
    # -z: NUL-delimited records, no quoting ambiguity for paths with spaces/newlines/non-ASCII.
    # core.quotePath=false: redundant under -z but kept as belt-and-braces and to match peer calls.
    # encoding='utf-8': pin decoding so a C/POSIX locale doesn't crash on multi-byte paths.
    tracked = subprocess.check_output(
        ['git', '-c', 'core.quotePath=false', 'ls-files', '-z'],
        encoding='utf-8',
    ).split('\0')
    # Submodule gitlinks (mode 160000) appear in `ls-files` but `git hash-object`
    # cannot hash them — exits 128 and crashes the whole pipeline. Filter them out.
    stage = subprocess.check_output(
        ['git', '-c', 'core.quotePath=false', 'ls-files', '--stage', '-z'],
        encoding='utf-8',
    ).split('\0')
    gitlinks = {
        rec.split('\t', 1)[1]
        for rec in stage
        if rec.startswith('160000 ') and '\t' in rec
    }
    untracked = subprocess.check_output(
        ['git', '-c', 'core.quotePath=false', 'ls-files', '--others', '--exclude-standard', '-z'],
        encoding='utf-8',
    ).split('\0')
    all_files = set(tracked) | set(untracked)
    return sorted(f for f in all_files if f and f not in gitlinks and not should_skip(f))


def hash_files(paths: list[str]) -> dict[str, str]:
    """Batch `git hash-object`; returns {path: 8-char hash}.

    Uses --stdin-paths to sidestep ARG_MAX on large repos. Validates that git
    returned one hash per input — defensive against partial-failure modes.
    """
    if not paths:
        return {}
    proc = subprocess.run(
        ['git', 'hash-object', '--stdin-paths'],
        input='\n'.join(paths),
        capture_output=True, encoding='utf-8', check=True,
    )
    out = proc.stdout.strip().splitlines()
    if len(out) != len(paths):
        raise RuntimeError(
            f'git hash-object: expected {len(paths)} hashes, got {len(out)}'
        )
    return {p: h[:8] for p, h in zip(paths, out)}


def detect_renames() -> list[tuple[str, str]]:
    """Parse staged rename pairs from `git status -z`. Trust git's default 50% similarity.

    Limitation: a worktree-only `mv old new` (no `git add`) appears as delete + untracked.
    Git cannot detect those as renames without staging, so neither can we.
    """
    out = subprocess.check_output(
        ['git', '-c', 'core.quotePath=false', 'status', '--porcelain=v1', '-z'],
        encoding='utf-8',
    )
    # porcelain v1 with -z: 'XY NEW\0OLD\0' for renames; 'XY PATH\0' otherwise.
    fields = out.split('\0')
    renames = []
    i = 0
    while i < len(fields):
        entry = fields[i]
        if len(entry) < 4:
            i += 1
            continue
        xy = entry[:2]
        new_path = entry[3:]
        if xy[0] in ('R', 'C') and i + 1 < len(fields):
            renames.append((fields[i + 1], new_path))
            i += 2
            continue
        i += 1
    return renames


def _unquote_git_path(s: str) -> str:
    """Decode git's legacy C-style quoted-octal path. Idempotent on raw paths.

    Migration hook: manifests produced before `core.quotePath=false` stored non-ASCII
    paths as e.g. `"templates/\\345\\205\\211.txt"`. We decode them transparently so
    upgrades don't see phantom remove+add churn.
    """
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        return s
    inner = s[1:-1]
    raw = bytearray()
    i = 0
    while i < len(inner):
        c = inner[i]
        if c == '\\' and i + 1 < len(inner):
            nxt = inner[i + 1]
            if nxt in '01234567' and i + 4 <= len(inner):
                raw.append(int(inner[i + 1:i + 4], 8))
                i += 4
                continue
            simple = {'n': 0x0A, 't': 0x09, 'r': 0x0D, '\\': 0x5C, '"': 0x22}
            raw.append(simple.get(nxt, ord(nxt)))
            i += 2
        else:
            raw.append(ord(c))
            i += 1
    return raw.decode('utf-8', errors='replace')


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
            filename = _unquote_git_path(filename)
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

    # Atomic write: tmp + os.replace, so a crash mid-write can't truncate the manifest.
    tmp = MANIFEST_PATH.with_name(MANIFEST_PATH.name + '.tmp')
    tmp.write_text('\n'.join(lines), encoding='utf-8')
    tmp.replace(MANIFEST_PATH)


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


def merge_payloads(payloads: list[dict]) -> dict:
    """Concatenate updates / removals / renames from several decision JSONs.

    Each parallel sub-agent writes its own part file; the script joins them so the
    main agent never hand-merges. Last writer wins per path is fine — apply itself
    is idempotent and order-independent for distinct paths.
    """
    merged = {'updates': [], 'removals': [], 'renames': []}
    for p in payloads:
        merged['updates'].extend(p.get('updates', []))
        merged['removals'].extend(p.get('removals', []))
        merged['renames'].extend(p.get('renames', []))
    return merged


def cmd_apply(updates_json: str) -> dict:
    """Apply LLM decisions to the manifest. UNCHANGED refreshes hash only.

    Hashes are computed from disk here, not taken from the payload: sub-agents emit
    only {path, summary}, so the main agent never joins todo hashes onto summaries
    (that manual join was the dominant source of dropped files). A payload `hash`,
    if present, is ignored.
    """
    require_git()
    updates = json.loads(updates_json)
    current_paths = set(list_current_files())
    manifest = parse_manifest()
    by_path = {e['path']: e for e in manifest}

    # Old paths intentionally retired in this call. A stale `updates` entry for one
    # of these is expected (the LLM redundantly re-listed a renamed/removed file) —
    # it must NOT be reported as a missing-path anomaly.
    retired_paths = {r['old_path'] for r in updates.get('renames', [])}
    retired_paths.update(updates.get('removals', []))

    # Single batched hash pass over every path we will touch that still exists on disk.
    to_hash = {u['path'] for u in updates.get('updates', [])}
    to_hash.update(r['new_path'] for r in updates.get('renames', []))
    disk_hashes = hash_files(sorted(p for p in to_hash if p in current_paths))

    # Rehash the new path: renames often carry small content edits.
    for r in updates.get('renames', []):
        old, new = r['old_path'], r['new_path']
        if old in by_path and new in current_paths:
            entry = by_path.pop(old)
            entry['path'] = new
            entry['hash'] = disk_hashes.get(new, entry['hash'])
            by_path[new] = entry

    for p in updates.get('removals', []):
        by_path.pop(p, None)

    received = len(updates.get('updates', []))
    applied = 0
    skipped_missing_path = []      # path not tracked by git and not retired here (hallucinated)
    skipped_unchanged_new = []     # UNCHANGED sentinel for a tracked file with no prior entry

    for u in updates.get('updates', []):
        p = u['path']
        s = u['summary']
        # Path no longer tracked by git. If it was retired (renamed/removed) in this same
        # call, the stale entry is benign — drop it quietly. Otherwise it's hallucinated:
        # LLMs sometimes emit entries for nonexistent files. Surface those.
        if p not in current_paths:
            if p not in retired_paths:
                skipped_missing_path.append(p)
            continue
        h = disk_hashes[p]
        if s == 'UNCHANGED':
            # UNCHANGED contract: refresh hash, keep old summary — linchpin of the cacheless design.
            if p in by_path:
                by_path[p]['hash'] = h
                applied += 1
            else:
                # Tracked file with no prior entry: UNCHANGED has nothing to refresh (init mode,
                # or a brand-new file the LLM wrongly marked UNCHANGED). Surface it instead of
                # dropping silently — otherwise received != applied with no clue why.
                skipped_unchanged_new.append(p)
        else:
            by_path[p] = {'path': p, 'hash': h, 'summary': s}
            applied += 1

    write_manifest(list(by_path.values()))

    # Coverage gap: any indexable file still missing from the manifest after apply.
    # A dropped sub-agent output or a forgotten summary lands here, so the caller can
    # fill it instead of hand-diffing todo against the payload. Empty on a healthy run.
    missing_from_manifest = sorted(current_paths - set(by_path))

    result = {'total_entries': len(by_path), 'received': received, 'applied': applied}
    if skipped_unchanged_new:
        result['skipped_unchanged_new'] = skipped_unchanged_new
    if skipped_missing_path:
        result['skipped_missing_path'] = skipped_missing_path
    if missing_from_manifest:
        result['missing_from_manifest'] = missing_from_manifest
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['todo', 'lint', 'apply'])
    parser.add_argument(
        'inputs', nargs='*',
        help='apply: one or more decision JSON files (shell glob ok); omit to read stdin',
    )
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
        if args.inputs:
            # Parallel sub-agents each drop a part file; merge them in-script so the
            # main agent never hand-joins. Shell expands the glob into argv.
            payloads = [json.loads(Path(f).read_text(encoding='utf-8')) for f in args.inputs]
            updates_json = json.dumps(merge_payloads(payloads))
        else:
            updates_json = sys.stdin.read()
        result = cmd_apply(updates_json)
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':  # pragma: no cover - CLI entry; tests call main() directly.
    main()
