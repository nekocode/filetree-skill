#!/usr/bin/env python3
"""filetree.py — deterministic operations for FILETREE.md maintenance."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from filetree_config import (
    DEFAULT_MANIFEST_PATH,
    Config,
    filter_indexable,
    load_config,
)

# Nested markdown list format. Indent is 2 spaces per depth level (depth = indent / 2).
#   File line: `<indent>- `name`: summary <!--hash:xxxxxxxx-->`  (backtick-quoted, carries hash)
#   Dir line:  `<indent>- name`                                  (no backtick, no hash, structural only)
# The backtick prefix is what tells a file line apart from a directory line.
# DIR_RE is only ever matched against the machine-written manifest, which has no
# prose bullets — a stray top-level `- note` would otherwise read as a directory.
FILE_RE = re.compile(
    r'^(?P<indent>(?:  )*)- `(?P<name>[^`]+)`: (?P<summary>.+?) <!--hash:(?P<hash>[a-f0-9]+)-->\s*$'
)
DIR_RE = re.compile(r'^(?P<indent>(?:  )*)- (?P<name>(?!`).+?)\s*$')


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


def list_current_files(config: Config = None) -> list[str]:
    """Tracked + untracked-unignored files, deduped, sorted, config-filtered."""
    # `config` is optional only so unit tests can call this helper bare; production goes
    # through cmd_todo / cmd_apply, the single load_config() entry points, which always
    # pass config down. A bare call deliberately defaults to empty Config() (no disk read).
    config = config or Config()
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
    candidates = sorted(f for f in all_files if f and f not in gitlinks)
    # filter_indexable preserves order, so the sorted input stays sorted.
    return filter_indexable(candidates, config)


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


def compute_renames(manifest_by_path: dict, config: Config = None) -> list[dict]:
    """Rename pairs git detected, kept to manifest-known sources and indexable targets.

    Deterministic from repo state, so both `cmd_todo` and `cmd_apply` derive renames
    here rather than trusting an LLM-relayed payload — the agent never hand-carries them.
    The target is run through the same config filter so a rename INTO an excluded path
    correctly degrades to a removal.
    """
    config = config or Config()
    # No early-return for empty pairs: filter_indexable([]) short-circuits (match_gitignore
    # returns on empty paths) and the comprehension over empty pairs yields [] anyway.
    pairs = detect_renames()
    indexable_new = set(filter_indexable([n for _o, n in pairs], config))
    return [
        {'old_path': o, 'new_path': n}
        for o, n in pairs
        if o in manifest_by_path and n in indexable_new
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


def parse_manifest(manifest_path: str = DEFAULT_MANIFEST_PATH) -> list[dict]:
    """Read the nested-list manifest into [{path, summary, hash}].

    Reconstructs each file's full path from its indentation depth: a stack holds
    the directory name at every depth, so a file at depth d joins dir_stack[:d]
    with its own name. Directory lines only update the stack; files emit entries.
    """
    mpath = Path(manifest_path)
    if not mpath.exists():
        return []
    entries = []
    dir_stack: list[str] = []  # dir_stack[d] = directory name declared at depth d
    for line in mpath.read_text(encoding='utf-8').splitlines():
        m = FILE_RE.match(line)
        if m:
            depth = len(m.group('indent')) // 2
            name = _unquote_git_path(m.group('name'))
            parent = '/'.join(dir_stack[:depth])
            full_path = f'{parent}/{name}' if parent else name
            entries.append({
                'path': full_path,
                'summary': m.group('summary').strip(),
                'hash': m.group('hash'),
            })
            continue
        m = DIR_RE.match(line)
        if m:
            depth = len(m.group('indent')) // 2
            # Truncate to this depth, then push: a shallower dir resets deeper ones.
            # DIR_RE's `\s*$` already excludes trailing whitespace — no strip needed.
            del dir_stack[depth:]
            dir_stack.append(_unquote_git_path(m.group('name')))
    return entries


def write_manifest(entries: list[dict], manifest_path: str = DEFAULT_MANIFEST_PATH) -> None:
    """Render entries as a nested markdown list (a directory tree), sorted stably."""
    # Build a nested tree from full paths: each node holds child dirs + leaf files.
    root: dict = {'dirs': {}, 'files': []}
    for e in entries:
        *dirs, _name = e['path'].split('/')
        node = root
        for d in dirs:
            node = node['dirs'].setdefault(d, {'dirs': {}, 'files': []})
        node['files'].append(e)

    lines = [
        '# Project Filetree',
        '',
        '_Auto-maintained by `/filetree:update`. Each entry carries a content hash; mismatched hashes indicate stale summaries._',
        '',
    ]

    def emit(node: dict, depth: int) -> None:
        indent = '  ' * depth
        # Directories first, then files; each lexicographically — stable, no spurious diffs.
        for dname in sorted(node['dirs']):
            lines.append(f'{indent}- {dname}')
            emit(node['dirs'][dname], depth + 1)
        # All files in a node share the same directory prefix, so sorting by full
        # path is identical to sorting by basename — and avoids a Path() per compare.
        for e in sorted(node['files'], key=lambda x: x['path']):
            filename = e['path'].rsplit('/', 1)[-1]
            lines.append(f"{indent}- `{filename}`: {e['summary']} <!--hash:{e['hash']}-->")

    emit(root, 0)
    lines.append('')  # trailing newline at EOF

    # Atomic write: tmp + os.replace, so a crash mid-write can't truncate the manifest.
    mpath = Path(manifest_path)
    # A relocated manifest (e.g. docs/FILETREE.md) needs its parent dir to exist.
    mpath.parent.mkdir(parents=True, exist_ok=True)
    tmp = mpath.with_name(mpath.name + '.tmp')
    tmp.write_text('\n'.join(lines), encoding='utf-8')
    tmp.replace(mpath)


DEFAULT_BATCH_SIZE = 25


def cmd_todo(batch_size: int = 0, split_dir: str = None, config: Config = None) -> dict:
    """Diff current files vs manifest; emit the LLM todo list.

    With `split_dir` set, the LLM work (added + changed) is chunked into
    `<split_dir>/batch_<NN>.json` files of `batch_size` items each, and the result
    carries `split_dir` + `batches` as `[{file, count}]`. The caller drops the full
    added/changed lists from stdout (they live in the files now), so a large repo
    can't blow past a read limit and force re-parsing. Without `split_dir` the result
    is the plain diff — the agent never improvises chunking or temp files.
    """
    require_git()
    config = config or load_config()
    current_paths = set(list_current_files(config))
    manifest = parse_manifest(config.manifest_path)
    manifest_by_path = {e['path']: e for e in manifest}

    renames = compute_renames(manifest_by_path, config)
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
        # Whether the manifest file exists on disk. Lets /filetree:update tell
        # "no manifest yet → run /filetree:init" apart from a present-but-empty
        # manifest (both have total_in_manifest == 0), without guessing.
        'manifest_exists': Path(config.manifest_path).exists(),
        'stats': {
            'total_in_repo': len(current_paths),
            'total_in_manifest': len(manifest_by_path),
            'need_llm': len(added) + len(changed),
        },
        # Surfaced so the command reads manifest_path / language from here (DRY):
        # the script is the single config parser, the command never re-reads it.
        'config': {
            'manifest_path': config.manifest_path,
            'language': config.language,
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


def cmd_apply(updates_json: str, config: Config = None) -> dict:
    """Apply LLM summaries to the manifest. UNCHANGED refreshes hash only.

    The payload carries only `{updates: [{path, summary}]}`:
    - Hashes are computed from disk, never taken from the payload (the old manual
      hash-join was the dominant source of dropped files).
    - Removals and renames are recomputed from repo state here, not relayed by the
      agent — they're deterministic, so carrying them through the LLM was pure churn.
    """
    require_git()
    config = config or load_config()
    updates = json.loads(updates_json).get('updates', [])
    current_paths = set(list_current_files(config))
    manifest = parse_manifest(config.manifest_path)
    by_path = {e['path']: e for e in manifest}

    # Recompute the deterministic edits from repo state.
    renames = compute_renames(by_path, config)
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
    # Three-way breakdown so the caller reports straight from script output instead of
    # re-tallying its own part files (LLM arithmetic = the churn we converge away):
    #   added            — first summary for a path absent from the prior manifest
    #   summaries_updated — replaced summary for a path that already had an entry
    #   hashes_refreshed  — UNCHANGED: kept old summary, refreshed hash only
    added = 0
    summaries_updated = 0
    hashes_refreshed = 0
    skipped_missing_path = []      # path absent from disk and not retired here (hallucinated)
    skipped_excluded = []          # real file on disk, dropped by config.exclude / built-in skip
    skipped_unchanged_new = []     # UNCHANGED sentinel for a tracked file with no prior entry

    for u in updates:
        p = u['path']
        s = u['summary']
        # Path not in the indexable set. Three cases, distinguished so the caller gets an
        # accurate diagnostic instead of crying "hallucination" at a real file:
        #   retired here (renamed/removed) → benign stale entry, drop quietly
        #   still on disk → real file dropped by config.exclude / built-in skip, not a bug
        #   absent from disk → genuinely hallucinated; surface it
        if p not in current_paths:
            if p in retired_paths:
                pass
            elif Path(p).exists():
                skipped_excluded.append(p)
            else:
                skipped_missing_path.append(p)
            continue
        h = disk_hashes[p]
        if s == 'UNCHANGED':
            # UNCHANGED contract: refresh hash, keep old summary — linchpin of the cacheless design.
            if p in by_path:
                by_path[p]['hash'] = h
                hashes_refreshed += 1
            else:
                # Tracked file with no prior entry: UNCHANGED has nothing to refresh (init mode,
                # or a brand-new file the LLM wrongly marked UNCHANGED). Surface it instead of
                # dropping silently — otherwise received != applied with no clue why.
                skipped_unchanged_new.append(p)
        else:
            # Real summary lands the same way either way; the branch only picks the counter.
            if p in by_path:
                summaries_updated += 1
            else:
                added += 1
            by_path[p] = {'path': p, 'hash': h, 'summary': s}

    write_manifest(list(by_path.values()), config.manifest_path)

    # Coverage gap: any indexable file still missing from the manifest after apply.
    # A dropped sub-agent output or a forgotten summary lands here, so the caller can
    # fill it instead of hand-diffing todo against the payload. Empty on a healthy run.
    missing_from_manifest = sorted(current_paths - set(by_path))

    # `applied` stays the sum (received-minus-skipped) for callers that just check
    # payload-vs-persisted; the three-way split feeds the update/init report directly.
    applied = added + summaries_updated + hashes_refreshed
    result = {
        'total_entries': len(by_path), 'received': received, 'applied': applied,
        'added': added, 'summaries_updated': summaries_updated,
        'hashes_refreshed': hashes_refreshed,
        'removed': len(removed), 'renamed': len(renames),
    }
    if skipped_unchanged_new:
        result['skipped_unchanged_new'] = skipped_unchanged_new
    if skipped_excluded:
        result['skipped_excluded'] = skipped_excluded
    if skipped_missing_path:
        result['skipped_missing_path'] = skipped_missing_path
    if missing_from_manifest:
        result['missing_from_manifest'] = missing_from_manifest
    return result


WIRE_FILES = ('CLAUDE.md', 'AGENTS.md')


def cmd_wire_target() -> dict:
    """Resolve where to wire the manifest reference for CLAUDE.md / AGENTS.md.

    These are commonly symlinks (e.g. → .ai/rules.md); editing the link path fails
    with 'refusing to write through symlink'. The script resolves the real target and
    surfaces any existing manifest mention, so the agent edits the right file once
    instead of reading, hitting the symlink wall, then probing with readlink itself.

    `manifest_path` + `manifest_exists` are surfaced too so the agent wires the configured
    path (not a hardcoded FILETREE.md) and knows whether the manifest already exists without
    a separate stat. `matches` is searched for the configured manifest path.
    """
    require_git()
    config = load_config()
    # Match the FULL configured path, not just the basename: a bare-basename regex yields
    # false "already wired" hits when manifest_path has a common name (e.g. docs/index.md
    # would match an unrelated `index.md` mention). 'FILETREE.md' (default) is unchanged.
    ref_re = re.compile(re.escape(config.manifest_path), re.IGNORECASE)
    out = {
        'manifest_path': config.manifest_path,
        'manifest_exists': Path(config.manifest_path).exists(),
    }
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
            # Lines mentioning the manifest, so the agent can judge "already wired?"
            # without re-reading (a backticked path / link = wired; prose / a
            # "do not edit" warning = not a real reference).
            'matches': [ln for ln in text.splitlines() if ref_re.search(ln)],
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
