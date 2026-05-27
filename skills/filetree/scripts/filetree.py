#!/usr/bin/env python3
"""filetree.py — deterministic operations for FILETREE.md maintenance."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
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
    # Tracked files deleted from the worktree but not yet staged still appear in
    # `ls-files`, yet `git hash-object` can't open them — exits 128 and crashes the
    # whole batch. Drop them: the file is gone from disk, so it correctly flows into
    # the manifest's `removed` bucket instead of needing the user to `git rm` first.
    deleted = set(subprocess.check_output(
        ['git', '-c', 'core.quotePath=false', 'ls-files', '--deleted', '-z'],
        encoding='utf-8',
    ).split('\0'))
    all_files = (set(tracked) | set(untracked)) - deleted
    return sorted(f for f in all_files if f and f not in gitlinks and not should_skip(f))


def _read_symlink_bytes(path: str) -> bytes:
    """Raw on-disk link target as bytes — what git hashes a symlink blob from.

    Bytes, not str: os.readlink decodes with surrogateescape, and a non-UTF-8
    target would then crash on re-encode (hashing) or json.dumps (todo output).
    """
    return os.readlink(os.fsencode(path))


def hash_files(paths: list[str]) -> dict[str, str]:
    """Batch `git hash-object`; returns {path: 8-char hash}.

    Regular files go through --stdin-paths to sidestep ARG_MAX on large repos.
    Symlinks are hashed separately from their link-target STRING: --stdin-paths
    *follows* a link (hashing the target's CONTENT, not the link) and exits 128
    on a broken link, which would crash the whole batch. Git stores a symlink as
    a blob of its target path, so hashing that string is both git-consistent
    (matches `ls-files --stage`) and crash-proof on broken links.
    """
    if not paths:
        return {}
    link_set = {p for p in paths if Path(p).is_symlink()}
    regular = [p for p in paths if p not in link_set]
    result: dict[str, str] = {}
    if regular:
        proc = subprocess.run(
            ['git', 'hash-object', '--stdin-paths'],
            input='\n'.join(regular),
            capture_output=True, encoding='utf-8', check=True,
        )
        out = proc.stdout.strip().splitlines()
        if len(out) != len(regular):
            raise RuntimeError(
                f'git hash-object: expected {len(regular)} hashes, got {len(out)}'
            )
        result.update({p: h[:8] for p, h in zip(regular, out)})
    for p in link_set:
        # Hash the raw link-target BYTES as a blob (no trailing newline), exactly how
        # git stores the symlink, so the hash matches ls-files --stage. Bytes (not str
        # + encoding='utf-8') so a non-UTF-8 target can't raise UnicodeEncodeError.
        proc = subprocess.run(
            ['git', 'hash-object', '--stdin'],
            input=_read_symlink_bytes(p),
            capture_output=True, check=True,
        )
        result[p] = proc.stdout.decode('ascii').strip()[:8]
    return result


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


def compute_renames(manifest_by_path: dict) -> list[dict]:
    """Rename pairs git detected, kept to manifest-known sources and indexable targets.

    Deterministic from repo state, so both `cmd_todo` and `cmd_apply` derive renames
    here rather than trusting an LLM-relayed payload — the agent never hand-carries them.
    """
    return [
        {'old_path': o, 'new_path': n}
        for o, n in detect_renames()
        if o in manifest_by_path and not should_skip(n)
    ]


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


DEFAULT_BATCH_SIZE = 25


def cmd_todo(batch_size: int = 0, split_dir: str = None) -> dict:
    """Diff current files vs manifest; emit the LLM todo list.

    With `split_dir` set, the LLM work (added + changed) is chunked into
    `<split_dir>/batch_<NN>.json` files of `batch_size` items each, and the result
    carries `split_dir` + `batches` as `[{file, count}]`. The caller drops the full
    added/changed lists from stdout (they live in the files now), so a large repo
    can't blow past a read limit and force re-parsing. Without `split_dir` the result
    is the plain diff — the agent never improvises chunking or temp files.
    """
    require_git()
    current_paths = set(list_current_files())
    manifest = parse_manifest()
    manifest_by_path = {e['path']: e for e in manifest}

    renames = compute_renames(manifest_by_path)
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

    # Annotate symlinks so the LLM writes "symlink → target" without Read-ing them
    # (a Read follows the link to the target's content — wasteful, and fails on a
    # broken link). Deterministic, so the script supplies the target directly.
    for item in added + changed:
        if Path(item['path']).is_symlink():
            # Decode for JSON display; 'replace' keeps a non-UTF-8 target from
            # crashing json.dumps (the hash still comes from the raw bytes).
            item['symlink_target'] = _read_symlink_bytes(item['path']).decode('utf-8', 'replace')

    result = {
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

    if split_dir is not None:
        size = batch_size or DEFAULT_BATCH_SIZE
        items = added + changed
        out_dir = Path(split_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        batch_refs = []
        for i in range(0, len(items), size):
            chunk = items[i:i + size]
            f = out_dir / f'batch_{i // size:02d}.json'
            f.write_text(json.dumps(chunk, ensure_ascii=False, indent=2), encoding='utf-8')
            batch_refs.append({'file': str(f), 'count': len(chunk)})
        result['split_dir'] = str(out_dir)
        result['batches'] = batch_refs
    return result


def merge_payloads(payloads: list[dict]) -> dict:
    """Merge `updates` across part files; dedup per path, last writer wins.

    Each parallel sub-agent writes its own part file with only `{path, summary}`
    entries — removals/renames are not a part-file concern (apply recomputes them
    from repo state). Dedup matters because a retry part or overlapping batches can
    re-list a path (the apply glob re-matches old + new parts); two entries for one
    path would inflate received/applied and raise a false `skipped_unchanged_new`.
    """
    updates_by_path = {}  # path -> entry, preserving last occurrence
    for p in payloads:
        for u in p.get('updates', []):
            updates_by_path[u['path']] = u
    return {'updates': list(updates_by_path.values())}


def cmd_apply(updates_json: str) -> dict:
    """Apply LLM summaries to the manifest. UNCHANGED refreshes hash only.

    The payload carries only `{updates: [{path, summary}]}`:
    - Hashes are computed from disk, never taken from the payload (the old manual
      hash-join was the dominant source of dropped files).
    - Removals and renames are recomputed from repo state here, not relayed by the
      agent — they're deterministic, so carrying them through the LLM was pure churn.
    """
    require_git()
    updates = json.loads(updates_json).get('updates', [])
    current_paths = set(list_current_files())
    manifest = parse_manifest()
    by_path = {e['path']: e for e in manifest}

    # Recompute the deterministic edits from repo state.
    renames = compute_renames(by_path)
    renamed_olds = {r['old_path'] for r in renames}
    removed = sorted(set(by_path) - current_paths - renamed_olds)
    # Old paths retired in this call. A stale `updates` entry for one of these is
    # expected (LLM re-listed a renamed/removed file) — not a missing-path anomaly.
    retired_paths = renamed_olds | set(removed)

    # Single batched hash pass over every path we will touch that still exists on disk.
    to_hash = {u['path'] for u in updates}
    to_hash.update(r['new_path'] for r in renames)
    disk_hashes = hash_files(sorted(p for p in to_hash if p in current_paths))

    # Rehash the new path: renames often carry small content edits.
    for r in renames:
        old, new = r['old_path'], r['new_path']
        if old in by_path and new in current_paths:
            entry = by_path.pop(old)
            entry['path'] = new
            entry['hash'] = disk_hashes.get(new, entry['hash'])
            by_path[new] = entry

    for p in removed:
        by_path.pop(p, None)

    received = len(updates)
    applied = 0
    skipped_missing_path = []      # path not tracked by git and not retired here (hallucinated)
    skipped_unchanged_new = []     # UNCHANGED sentinel for a tracked file with no prior entry

    for u in updates:
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

    result = {
        'total_entries': len(by_path), 'received': received, 'applied': applied,
        'removed': len(removed), 'renamed': len(renames),
    }
    if skipped_unchanged_new:
        result['skipped_unchanged_new'] = skipped_unchanged_new
    if skipped_missing_path:
        result['skipped_missing_path'] = skipped_missing_path
    if missing_from_manifest:
        result['missing_from_manifest'] = missing_from_manifest
    return result


WIRE_FILES = ('CLAUDE.md', 'AGENTS.md')
FILETREE_REF_RE = re.compile(r'FILETREE\.md', re.IGNORECASE)


def cmd_wire_target() -> dict:
    """Resolve where to wire the FILETREE.md reference for CLAUDE.md / AGENTS.md.

    These are commonly symlinks (e.g. → .ai/rules.md); editing the link path fails
    with 'refusing to write through symlink'. The script resolves the real target and
    surfaces any existing FILETREE.md mention, so the agent edits the right file once
    instead of reading, hitting the symlink wall, then probing with readlink itself.
    """
    require_git()
    out = {}
    for name in WIRE_FILES:
        p = Path(name)
        if not p.exists():  # follows the link; a dangling symlink counts as absent here
            out[name] = {'exists': False}
            continue
        # read_text follows the symlink to the real content.
        text = p.read_text(encoding='utf-8', errors='replace')
        out[name] = {
            'exists': True,
            'is_symlink': p.is_symlink(),
            # Absolute real path — the agent must Edit THIS, never the link name.
            'real_path': os.path.realpath(name),
            # Lines mentioning FILETREE.md, so the agent can judge "already wired?"
            # without re-reading (a backticked path / link = wired; prose / a
            # "do not edit" warning = not a real reference).
            'matches': [ln for ln in text.splitlines() if FILETREE_REF_RE.search(ln)],
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['todo', 'lint', 'apply', 'wire-target'])
    parser.add_argument(
        'inputs', nargs='*',
        help='apply: one or more decision JSON files (shell glob ok); omit or pass `-` to read stdin',
    )
    parser.add_argument(
        '--batch-size', type=int, default=0, metavar='N',
        help='todo: items per --split batch (default 25); requires --split',
    )
    parser.add_argument(
        '--split', action='store_true',
        help='todo: write each batch to a temp dir as batch_NN.json; stdout returns '
             'only a summary + batch file refs (no full file list to truncate / re-parse)',
    )
    args = parser.parse_args()

    if args.command == 'wire-target':
        if args.inputs:
            parser.error('wire-target takes no file arguments')
        print(json.dumps(cmd_wire_target(), ensure_ascii=False, indent=2))
        return

    if args.command in ('todo', 'lint'):
        # `inputs` is only meaningful for apply; reject stray args instead of ignoring them.
        if args.inputs:
            parser.error(f'{args.command} takes no file arguments')
        # --batch-size / --split are todo-only; lint is pure drift detection.
        if args.command == 'lint' and (args.batch_size or args.split):
            parser.error('lint takes no --batch-size / --split')
        # --batch-size only sizes split batches; alone it would be a silent no-op.
        if args.batch_size and not args.split:
            parser.error('--batch-size requires --split')
        split_dir = tempfile.mkdtemp(prefix='filetree_') if args.split else None
        result = cmd_todo(batch_size=args.batch_size, split_dir=split_dir)
        if split_dir:
            # Full lists now live in the batch files; keep stdout small so the agent
            # never truncates and re-parses (the exact churn this flag removes).
            result.pop('added', None)
            result.pop('changed', None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.command == 'lint':
            # CI-friendly: exit 1 on drift.
            drift = (
                len(result['added']) + len(result['changed'])
                + len(result['removed']) + len(result['renamed'])
            )
            sys.exit(0 if drift == 0 else 1)
    elif args.command == 'apply':
        # `-` is the conventional stdin sentinel; treat it the same as no args so a
        # piped payload works whether the caller omits inputs or writes `apply -`.
        if args.inputs and args.inputs != ['-']:
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
