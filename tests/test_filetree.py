"""Unit + integration tests for filetree.py.

Coverage:
- Pure functions: should_skip, parse_manifest, write_manifest, round-trip.
- Integration: tmpdir + git init, end-to-end cmd_todo → cmd_apply.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import filetree


# ===== Pure-function unit tests =====


class TestShouldSkip:
    def test_binary_extensions(self):
        assert filetree.should_skip('logo.png')
        assert filetree.should_skip('font.woff2')
        assert filetree.should_skip('demo.MP4')  # Case-insensitive.

    def test_lock_files(self):
        assert filetree.should_skip('package-lock.json')
        assert filetree.should_skip('poetry.lock')
        assert filetree.should_skip('a/b/yarn.lock')  # Skipped in subdirectories too.

    def test_manifest_itself(self):
        assert filetree.should_skip('FILETREE.md')

    def test_normal_code_files(self):
        assert not filetree.should_skip('src/auth.py')
        assert not filetree.should_skip('README.md')
        assert not filetree.should_skip('Makefile')


class TestParseManifest:
    def test_empty_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert filetree.parse_manifest() == []

    def test_basic(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path('FILETREE.md').write_text(
            '# Project Filetree\n\n'
            '## src/auth/\n\n'
            '- `middleware.py` — JWT 校验中间件 <!--hash:a1b2c3d4-->\n'
            '- `jwt_utils.py` — JWT 工具 <!--hash:e5f6a7b8-->\n'
            '\n'
            '## (root)/\n\n'
            '- `README.md` — 项目说明 <!--hash:11223344-->\n',
            encoding='utf-8',
        )
        entries = filetree.parse_manifest()
        paths = {e['path'] for e in entries}
        assert paths == {
            'src/auth/middleware.py',
            'src/auth/jwt_utils.py',
            'README.md',
        }
        by_path = {e['path']: e for e in entries}
        assert by_path['src/auth/middleware.py']['hash'] == 'a1b2c3d4'
        assert by_path['src/auth/middleware.py']['summary'] == 'JWT 校验中间件'

    def test_tolerates_full_path_entries(self, tmp_path, monkeypatch):
        """Tolerate legacy format where entries store the full path."""
        monkeypatch.chdir(tmp_path)
        Path('FILETREE.md').write_text(
            '# Project Filetree\n\n'
            '## legacy/\n\n'
            '- `legacy/sub/old.py` — 旧条目带完整路径 <!--hash:deadbeef-->\n',
            encoding='utf-8',
        )
        entries = filetree.parse_manifest()
        assert entries[0]['path'] == 'legacy/sub/old.py'


class TestWriteManifest:
    def test_section_grouping_and_sorting(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        entries = [
            {'path': 'README.md', 'summary': 'doc', 'hash': '11111111'},
            {'path': 'src/b.py', 'summary': 'b', 'hash': '22222222'},
            {'path': 'src/a.py', 'summary': 'a', 'hash': '33333333'},
        ]
        filetree.write_manifest(entries)
        text = Path('FILETREE.md').read_text(encoding='utf-8')
        # Section lexical order: (root) before src.
        assert text.index('(root)/') < text.index('## src/')
        # Entries under src in lexical order.
        assert text.index('a.py') < text.index('b.py')

    def test_header_uses_namespaced_command(self, tmp_path, monkeypatch):
        """Manifest header must reference the namespaced command /filetree:update."""
        monkeypatch.chdir(tmp_path)
        filetree.write_manifest([
            {'path': 'a.py', 'summary': 's', 'hash': '11111111'},
        ])
        text = Path('FILETREE.md').read_text(encoding='utf-8')
        assert '/filetree:update' in text
        assert '/update-filetree' not in text  # Legacy name forbidden.

    def test_round_trip(self, tmp_path, monkeypatch):
        """write → parse must round-trip cleanly."""
        monkeypatch.chdir(tmp_path)
        original = [
            {'path': 'src/auth.py', 'summary': '认证模块', 'hash': 'aaaaaaaa'},
            {'path': 'main.py', 'summary': 'entry point', 'hash': 'bbbbbbbb'},
        ]
        filetree.write_manifest(original)
        parsed = filetree.parse_manifest()
        # Path set matches.
        assert {e['path'] for e in parsed} == {e['path'] for e in original}
        # Fields match.
        by_path = {e['path']: e for e in parsed}
        for e in original:
            assert by_path[e['path']]['summary'] == e['summary']
            assert by_path[e['path']]['hash'] == e['hash']


# ===== Integration tests: real git repository =====


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """Empty git repo + chdir, fully isolated from host gitconfig.

    Host ~/.gitconfig must not leak in: a developer who set
    `core.quotePath=false` globally would otherwise see the new regression
    test pass even after the fix is reverted.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('GIT_AUTHOR_NAME', 'test')
    monkeypatch.setenv('GIT_AUTHOR_EMAIL', 'test@test')
    monkeypatch.setenv('GIT_COMMITTER_NAME', 'test')
    monkeypatch.setenv('GIT_COMMITTER_EMAIL', 'test@test')
    # /dev/null sidesteps having to place a file (anywhere inside tmp_path leaks
    # into the repo; anywhere outside leaks across tests). POSIX-only — fine here.
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', '/dev/null')
    monkeypatch.setenv('GIT_CONFIG_SYSTEM', '/dev/null')
    monkeypatch.delenv('GIT_CONFIG_COUNT', raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    subprocess.run(['git', 'init', '-q'], check=True, cwd=tmp_path)
    return tmp_path


class TestIntegration:
    def test_todo_on_empty_manifest(self, git_repo):
        """Without FILETREE.md, every file lands in `added`."""
        Path('a.py').write_text('print(1)\n')
        Path('src').mkdir()
        Path('src/b.py').write_text('x = 2\n')
        Path('logo.png').write_bytes(b'\x89PNG\r\n')  # Should be skipped.

        result = filetree.cmd_todo()

        added_paths = {a['path'] for a in result['added']}
        assert added_paths == {'a.py', 'src/b.py'}
        assert result['changed'] == []
        assert result['stats']['need_llm'] == 2
        # Binaries do not enter the manifest.
        assert 'logo.png' not in added_paths

    def test_apply_then_todo_clean(self, git_repo):
        """After apply writes, a follow-up todo should report no drift."""
        Path('a.py').write_text('hello\n')
        todo = filetree.cmd_todo()
        h = todo['added'][0]['hash']

        updates = {
            'updates': [{'path': 'a.py', 'hash': h, 'summary': 'greeting'}],
            'removals': [],
            'renames': [],
        }
        filetree.cmd_apply(json.dumps(updates))

        # Manifest written.
        manifest = filetree.parse_manifest()
        assert len(manifest) == 1
        assert manifest[0]['summary'] == 'greeting'

        # Re-run todo: no changes.
        again = filetree.cmd_todo()
        assert again['added'] == []
        assert again['changed'] == []
        assert again['stats']['need_llm'] == 0

    def test_change_detected_by_hash(self, git_repo):
        """When file contents change, `changed` reflects it."""
        Path('a.py').write_text('v1\n')
        todo = filetree.cmd_todo()
        h1 = todo['added'][0]['hash']
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'hash': h1, 'summary': 'v1 file'}],
            'removals': [], 'renames': [],
        }))

        Path('a.py').write_text('v2 changed\n')
        todo2 = filetree.cmd_todo()
        assert len(todo2['changed']) == 1
        assert todo2['changed'][0]['path'] == 'a.py'
        assert todo2['changed'][0]['old_summary'] == 'v1 file'
        assert todo2['changed'][0]['old_hash'] == h1
        assert todo2['changed'][0]['new_hash'] != h1

    def test_unchanged_keeps_old_summary(self, git_repo):
        """UNCHANGED: refresh hash, keep the old summary."""
        Path('a.py').write_text('v1\n')
        todo = filetree.cmd_todo()
        h1 = todo['added'][0]['hash']
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'hash': h1, 'summary': '原本的描述'}],
            'removals': [], 'renames': [],
        }))

        Path('a.py').write_text('v1 with comment\n')
        todo2 = filetree.cmd_todo()
        new_hash = todo2['changed'][0]['new_hash']
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'hash': new_hash, 'summary': 'UNCHANGED'}],
            'removals': [], 'renames': [],
        }))

        manifest = filetree.parse_manifest()
        assert manifest[0]['summary'] == '原本的描述'  # Old summary retained.
        assert manifest[0]['hash'] == new_hash  # Hash updated.

    def test_removal(self, git_repo):
        """After a file is deleted, it appears in `removed`."""
        Path('a.py').write_text('x\n')
        Path('b.py').write_text('y\n')
        todo = filetree.cmd_todo()
        filetree.cmd_apply(json.dumps({
            'updates': [
                {'path': a['path'], 'hash': a['hash'], 'summary': a['path']}
                for a in todo['added']
            ],
            'removals': [], 'renames': [],
        }))

        Path('b.py').unlink()
        todo2 = filetree.cmd_todo()
        assert todo2['removed'] == ['b.py']

        filetree.cmd_apply(json.dumps({
            'updates': [], 'removals': ['b.py'], 'renames': [],
        }))
        manifest = filetree.parse_manifest()
        assert {e['path'] for e in manifest} == {'a.py'}

    def test_lint_exit_codes(self, git_repo, capsys, monkeypatch):
        """lint exits 1 on drift and 0 when clean."""
        Path('a.py').write_text('x\n')
        # Drift present.
        monkeypatch.setattr(sys, 'argv', ['filetree.py', 'lint'])
        with pytest.raises(SystemExit) as ei:
            filetree.main()
        assert ei.value.code == 1

        # No drift after apply.
        todo = filetree.cmd_todo()
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'hash': todo['added'][0]['hash'],
                        'summary': 'x'}],
            'removals': [], 'renames': [],
        }))
        monkeypatch.setattr(sys, 'argv', ['filetree.py', 'lint'])
        # Even with no drift, sys.exit(0) still raises SystemExit.
        with pytest.raises(SystemExit) as ei2:
            filetree.main()
        assert ei2.value.code == 0

    def test_rename_via_main_apply(self, git_repo, capsys, monkeypatch):
        """Exercise the apply rename path end-to-end: main + cmd_apply + git rename detection."""
        # Initial state: a.py committed and present in the manifest.
        Path('a.py').write_text('shared content here\n')
        subprocess.run(['git', 'add', 'a.py'], check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'init'], check=True)

        todo = filetree.cmd_todo()
        h1 = todo['added'][0]['hash']
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'hash': h1, 'summary': '原始文件'}],
            'removals': [], 'renames': [],
        }))

        # Perform the rename via `git mv`.
        subprocess.run(['git', 'mv', 'a.py', 'b.py'], check=True)
        todo2 = filetree.cmd_todo()
        # git should recognize it as a rename.
        assert len(todo2['renamed']) == 1
        assert todo2['renamed'][0]['old_path'] == 'a.py'
        assert todo2['renamed'][0]['new_path'] == 'b.py'

        # Drive the main() apply branch via stdin.
        import io as _io
        payload = json.dumps({
            'updates': [], 'removals': [],
            'renames': [{'old_path': 'a.py', 'new_path': 'b.py'}],
        })
        monkeypatch.setattr(sys, 'stdin', _io.StringIO(payload))
        monkeypatch.setattr(sys, 'argv', ['filetree.py', 'apply'])
        filetree.main()
        out = capsys.readouterr().out
        assert '"total_entries": 1' in out

        manifest = filetree.parse_manifest()
        assert manifest[0]['path'] == 'b.py'
        assert manifest[0]['summary'] == '原始文件'  # Summary carried over.


    def test_non_ascii_paths_end_to_end(self, git_repo):
        """Non-ASCII paths must survive ls-files / hash-object / status round-trip.

        Covers both ls-files codepaths: `cn` is committed (tracked path),
        `jp` is untracked (--others path).
        """
        Path('templates').mkdir()
        cn = 'templates/光大信用卡-授权委托书模板.txt'
        jp = 'templates/サンプル.txt'
        Path(cn).write_text('cn content\n', encoding='utf-8')
        Path(jp).write_text('jp content\n', encoding='utf-8')
        subprocess.run(['git', 'add', cn], check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'cn'], check=True)

        todo = filetree.cmd_todo()
        added_paths = {a['path'] for a in todo['added']}
        assert cn in added_paths  # tracked, not yet in manifest
        assert jp in added_paths  # untracked

        # Round-trip through apply + re-read to catch any encoding loss.
        filetree.cmd_apply(json.dumps({
            'updates': [
                {'path': a['path'], 'hash': a['hash'], 'summary': 'tpl'}
                for a in todo['added']
            ],
            'removals': [], 'renames': [],
        }, ensure_ascii=False))
        manifest_paths = {e['path'] for e in filetree.parse_manifest()}
        assert cn in manifest_paths
        assert jp in manifest_paths

    def test_non_ascii_rename_detected(self, git_repo):
        """detect_renames must catch a staged rename of a non-ASCII path."""
        old = '光大-v1.txt'
        new = '光大-v2.txt'
        Path(old).write_text('content\n', encoding='utf-8')
        subprocess.run(['git', 'add', old], check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'init'], check=True)

        todo = filetree.cmd_todo()
        h = todo['added'][0]['hash']
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': old, 'hash': h, 'summary': '模板'}],
            'removals': [], 'renames': [],
        }, ensure_ascii=False))

        subprocess.run(['git', 'mv', old, new], check=True)
        todo2 = filetree.cmd_todo()
        assert len(todo2['renamed']) == 1, todo2
        assert todo2['renamed'][0]['old_path'] == old
        assert todo2['renamed'][0]['new_path'] == new

    def test_path_with_spaces_rename_detected(self, git_repo):
        """Renames of paths with spaces must round-trip (the `-z` parser handles quoting)."""
        old = 'old name.py'
        new = 'new name.py'
        Path(old).write_text('shared content here\n')
        subprocess.run(['git', 'add', old], check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'init'], check=True)

        todo = filetree.cmd_todo()
        h = todo['added'][0]['hash']
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': old, 'hash': h, 'summary': 'orig'}],
            'removals': [], 'renames': [],
        }))

        subprocess.run(['git', 'mv', old, new], check=True)
        todo2 = filetree.cmd_todo()
        assert len(todo2['renamed']) == 1, todo2
        assert todo2['renamed'][0]['old_path'] == old
        assert todo2['renamed'][0]['new_path'] == new


    def test_submodule_gitlink_is_skipped(self, git_repo, tmp_path_factory):
        """Submodule gitlinks (mode 160000) must not enter the file list.

        Regression: `git ls-files` lists submodule paths, but `git hash-object`
        cannot hash a gitlink and exits 128, crashing the whole pipeline.
        """
        # External source repo for the submodule. Must live outside git_repo so
        # `git submodule add` treats it as a foreign URL, not a nested worktree.
        src = tmp_path_factory.mktemp('sub-src')
        subprocess.run(['git', 'init', '-q'], check=True, cwd=src)
        (src / 'README.md').write_text('sub\n')
        subprocess.run(['git', 'add', '-A'], check=True, cwd=src)
        subprocess.run(['git', 'commit', '-q', '-m', 'init'], check=True, cwd=src)

        # Real file alongside the submodule so we can assert it survives.
        Path('a.py').write_text('print(1)\n')

        # Recent git versions require opt-in for file:// submodule URLs.
        subprocess.run(
            ['git', '-c', 'protocol.file.allow=always',
             'submodule', 'add', '-q', f'file://{src}', 'vendor/sub'],
            check=True,
        )

        files = filetree.list_current_files()
        assert 'a.py' in files
        assert 'vendor/sub' not in files

        # Full pipeline: `git hash-object` previously crashed here on the gitlink.
        result = filetree.cmd_todo()
        added_paths = {e['path'] for e in result['added']}
        assert 'a.py' in added_paths
        assert 'vendor/sub' not in added_paths


class TestEdgeCases:
    def test_hash_files_empty(self):
        """Empty paths short-circuit without calling git."""
        assert filetree.hash_files([]) == {}

    def test_require_git_exits_outside_repo(self, tmp_path, monkeypatch):
        """A non-git directory triggers SystemExit."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as ei:
            filetree.require_git()
        # Exit code is the error message string.
        assert 'git repository' in str(ei.value.code)

    def test_unquote_git_path_legacy_octal(self):
        """Legacy quoted-octal paths decode back to UTF-8."""
        # Git's old quoting of 'templates/光.txt'.
        quoted = '"templates/\\345\\205\\211.txt"'
        assert filetree._unquote_git_path(quoted) == 'templates/光.txt'
        # Raw paths pass through unchanged (idempotent).
        assert filetree._unquote_git_path('templates/光.txt') == 'templates/光.txt'

    def test_parse_manifest_migrates_legacy_octal(self, tmp_path, monkeypatch):
        """A manifest produced by pre-fix code is silently upgraded on read."""
        monkeypatch.chdir(tmp_path)
        Path('FILETREE.md').write_text(
            '# Project Filetree\n\n'
            '## templates/\n\n'
            '- `"\\345\\205\\211.txt"` — 模板 <!--hash:deadbeef-->\n',
            encoding='utf-8',
        )
        entries = filetree.parse_manifest()
        assert entries[0]['path'] == 'templates/光.txt'

    def test_cmd_apply_rejects_hallucinated_path(self, git_repo):
        """Updates referencing nonexistent files are skipped, not persisted."""
        Path('real.py').write_text('x\n')
        filetree.cmd_apply(json.dumps({
            'updates': [
                {'path': 'real.py', 'hash': '11111111', 'summary': 'real'},
                {'path': 'ghost.py', 'hash': 'deadbeef', 'summary': 'hallucinated'},
            ],
            'removals': [], 'renames': [],
        }))
        manifest_paths = {e['path'] for e in filetree.parse_manifest()}
        assert 'real.py' in manifest_paths
        assert 'ghost.py' not in manifest_paths

    def test_cmd_apply_unchanged_after_rename_does_not_persist_sentinel(self, git_repo):
        """UNCHANGED for an already-renamed-away path must not write 'UNCHANGED' as summary."""
        Path('a.py').write_text('x\n')
        subprocess.run(['git', 'add', 'a.py'], check=True)
        subprocess.run(['git', 'commit', '-q', '-m', 'init'], check=True)
        todo = filetree.cmd_todo()
        h_a = todo['added'][0]['hash']
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'hash': h_a, 'summary': 'orig'}],
            'removals': [], 'renames': [],
        }))

        subprocess.run(['git', 'mv', 'a.py', 'b.py'], check=True)
        # Same call: rename a→b AND a stale UNCHANGED update referring to 'a.py'.
        result = filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'hash': h_a, 'summary': 'UNCHANGED'}],
            'removals': [], 'renames': [{'old_path': 'a.py', 'new_path': 'b.py'}],
        }))
        manifest = filetree.parse_manifest()
        # b.py should carry the original summary; a.py must not exist as a ghost.
        by_path = {e['path']: e for e in manifest}
        assert 'a.py' not in by_path
        assert by_path['b.py']['summary'] == 'orig'
        # The retired old path is benign — it must NOT be flagged as a missing-path anomaly.
        assert 'skipped_missing_path' not in result

    def test_cmd_apply_unchanged_for_new_path_is_surfaced(self, git_repo):
        """UNCHANGED with no prior entry (e.g. init mode) must be reported, not dropped silently."""
        Path('real.py').write_text('x\n')
        Path('fresh.py').write_text('y\n')
        result = filetree.cmd_apply(json.dumps({
            'updates': [
                {'path': 'real.py', 'hash': '11111111', 'summary': 'real'},
                {'path': 'fresh.py', 'hash': '22222222', 'summary': 'UNCHANGED'},
            ],
            'removals': [], 'renames': [],
        }))
        # real.py persisted; fresh.py's UNCHANGED has nothing to refresh → surfaced, sentinel never written.
        by_path = {e['path']: e for e in filetree.parse_manifest()}
        assert by_path['real.py']['summary'] == 'real'
        assert 'fresh.py' not in by_path
        assert result['received'] == 2
        assert result['applied'] == 1
        assert result['skipped_unchanged_new'] == ['fresh.py']

    def test_cmd_apply_reports_received_applied_counts(self, git_repo):
        """apply exposes received/applied so callers can detect payload != persisted."""
        Path('real.py').write_text('x\n')
        result = filetree.cmd_apply(json.dumps({
            'updates': [
                {'path': 'real.py', 'hash': '11111111', 'summary': 'real'},
                {'path': 'ghost.py', 'hash': 'deadbeef', 'summary': 'hallucinated'},
            ],
            'removals': [], 'renames': [],
        }))
        assert result['received'] == 2
        assert result['applied'] == 1
        assert result['skipped_missing_path'] == ['ghost.py']

    def test_cmd_apply_computes_hash_from_disk_ignoring_payload(self, git_repo):
        """apply hashes paths itself; a bogus payload hash must not land in the manifest."""
        Path('a.py').write_text('real content\n')
        real_hash = filetree.hash_files(['a.py'])['a.py']
        filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'hash': 'deadbeef', 'summary': 's'}],
            'removals': [], 'renames': [],
        }))
        by_path = {e['path']: e for e in filetree.parse_manifest()}
        assert by_path['a.py']['hash'] == real_hash  # disk hash, not 'deadbeef'

    def test_cmd_apply_payload_without_hash_field(self, git_repo):
        """Sub-agents emit only {path, summary}; apply must not require a hash key."""
        Path('a.py').write_text('x\n')
        result = filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'a.py', 'summary': 'no hash key'}],
            'removals': [], 'renames': [],
        }))
        by_path = {e['path']: e for e in filetree.parse_manifest()}
        assert by_path['a.py']['summary'] == 'no hash key'
        assert by_path['a.py']['hash'] == filetree.hash_files(['a.py'])['a.py']
        assert result['applied'] == 1

    def test_cmd_apply_reports_missing_from_manifest(self, git_repo):
        """A tracked file with no summary must surface as a coverage gap, not vanish."""
        Path('covered.py').write_text('x\n')
        Path('dropped.py').write_text('y\n')  # no summary provided — simulates a dropped sub-agent output
        result = filetree.cmd_apply(json.dumps({
            'updates': [{'path': 'covered.py', 'summary': 'covered'}],
            'removals': [], 'renames': [],
        }))
        assert result['missing_from_manifest'] == ['dropped.py']

    def test_cmd_apply_clean_run_has_no_missing(self, git_repo):
        """When every tracked file is summarized, missing_from_manifest is absent."""
        Path('a.py').write_text('x\n')
        Path('b.py').write_text('y\n')
        result = filetree.cmd_apply(json.dumps({
            'updates': [
                {'path': 'a.py', 'summary': 'a'},
                {'path': 'b.py', 'summary': 'b'},
            ],
            'removals': [], 'renames': [],
        }))
        assert 'missing_from_manifest' not in result

    def test_merge_payloads_concatenates(self):
        """merge_payloads joins updates/removals/renames across part files."""
        merged = filetree.merge_payloads([
            {'updates': [{'path': 'a', 'summary': 'a'}], 'removals': ['x'], 'renames': []},
            {'updates': [{'path': 'b', 'summary': 'b'}], 'removals': [], 'renames': [{'old_path': 'o', 'new_path': 'n'}]},
            {},  # tolerate a part with missing keys
        ])
        assert [u['path'] for u in merged['updates']] == ['a', 'b']
        assert merged['removals'] == ['x']
        assert merged['renames'] == [{'old_path': 'o', 'new_path': 'n'}]

    def test_apply_merges_multiple_input_files(self, git_repo, tmp_path_factory, capsys, monkeypatch):
        """main() apply with several part files merges them — the parallel sub-agent flow."""
        Path('a.py').write_text('x\n')
        Path('b.py').write_text('y\n')
        # Part files live outside the repo so they aren't seen as untracked files.
        parts_dir = tmp_path_factory.mktemp('ft_parts')
        part1 = parts_dir / 'part1.json'
        part2 = parts_dir / 'part2.json'
        part1.write_text(json.dumps({'updates': [{'path': 'a.py', 'summary': 'a'}]}))
        part2.write_text(json.dumps({'updates': [{'path': 'b.py', 'summary': 'b'}]}))
        monkeypatch.setattr(sys, 'argv', ['filetree.py', 'apply', str(part1), str(part2)])
        filetree.main()
        out = json.loads(capsys.readouterr().out)
        assert out['applied'] == 2
        assert 'missing_from_manifest' not in out
        by_path = {e['path']: e for e in filetree.parse_manifest()}
        assert {by_path['a.py']['summary'], by_path['b.py']['summary']} == {'a', 'b'}

    def test_hash_files_handles_many_paths(self, git_repo):
        """--stdin-paths bypasses ARG_MAX; verify a large batch hashes correctly."""
        paths = []
        for i in range(200):
            p = f'f{i:04d}.txt'
            Path(p).write_text(f'content {i}\n')
            paths.append(p)
        hashes = filetree.hash_files(paths)
        assert len(hashes) == 200
        # Each hash is 8 hex chars.
        for h in hashes.values():
            assert len(h) == 8
            int(h, 16)  # Raises if not hex.

    def test_wire_before_todo_captures_post_wire_hash(self, git_repo):
        """`/filetree:init` invariant: editing CLAUDE.md before `todo` must put
        the post-wire hash in `added`. Locks in the ordering guarantee that
        the init.md flow promises (and that prevents phantom drift on the
        first `/filetree:lint`)."""
        # Pre-wire CLAUDE.md exists with original rules.
        Path('CLAUDE.md').write_text('# Rules\n\n- be terse\n', encoding='utf-8')
        pre_wire_hash = filetree.hash_files(['CLAUDE.md'])['CLAUDE.md']

        # Simulate step 2: append the FILETREE.md reference bullet.
        Path('CLAUDE.md').write_text(
            '# Rules\n\n- be terse\n\n## References\n\n'
            '- `./FILETREE.md` — per-file purpose index. Read before ls/grep.\n',
            encoding='utf-8',
        )
        post_wire_hash = filetree.hash_files(['CLAUDE.md'])['CLAUDE.md']
        assert post_wire_hash != pre_wire_hash

        # Step 3: `todo` must capture the post-wire hash, not the pre-wire one.
        result = filetree.cmd_todo()
        claude_entry = next(a for a in result['added'] if a['path'] == 'CLAUDE.md')
        assert claude_entry['hash'] == post_wire_hash

    def test_gitignored_claude_md_absent_from_manifest(self, git_repo):
        """Gitignored CLAUDE.md never enters the manifest — documents the
        caveat init.md step 3 calls out. If list_current_files() ever stops
        honoring .gitignore, this test catches the change."""
        Path('.gitignore').write_text('CLAUDE.md\n', encoding='utf-8')
        Path('CLAUDE.md').write_text('# private\n', encoding='utf-8')

        result = filetree.cmd_todo()
        added_paths = {a['path'] for a in result['added']}
        assert 'CLAUDE.md' not in added_paths
        # .gitignore itself, on the other hand, IS tracked-by-untracked-unignored.
        assert '.gitignore' in added_paths
