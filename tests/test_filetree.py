"""Unit + integration tests for filetree.py.

Coverage:
- Pure functions: should_skip, parse_manifest, write_manifest, round-trip.
- Integration: tmpdir + git init, end-to-end cmd_todo → cmd_apply.
"""

import json
import os
import subprocess
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
    """Empty git repo + chdir."""
    monkeypatch.chdir(tmp_path)
    # Isolate user config so missing git config on CI/local does not break tests.
    env = os.environ.copy()
    env['GIT_AUTHOR_NAME'] = 'test'
    env['GIT_AUTHOR_EMAIL'] = 'test@test'
    env['GIT_COMMITTER_NAME'] = 'test'
    env['GIT_COMMITTER_EMAIL'] = 'test@test'
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    subprocess.run(['git', 'init', '-q'], check=True, cwd=tmp_path)
    subprocess.run(['git', 'config', 'commit.gpgsign', 'false'], cwd=tmp_path)
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

    def test_lint_exit_codes(self, git_repo, capsys):
        """lint exits 1 on drift and 0 when clean."""
        Path('a.py').write_text('x\n')
        import sys as _sys
        # Drift present.
        _sys.argv = ['filetree.py', 'lint']
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
        _sys.argv = ['filetree.py', 'lint']
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
        import sys as _sys
        import io as _io
        payload = json.dumps({
            'updates': [], 'removals': [],
            'renames': [{'old_path': 'a.py', 'new_path': 'b.py'}],
        })
        monkeypatch.setattr(_sys, 'stdin', _io.StringIO(payload))
        _sys.argv = ['filetree.py', 'apply']
        filetree.main()
        out = capsys.readouterr().out
        assert '"total_entries": 1' in out

        manifest = filetree.parse_manifest()
        assert manifest[0]['path'] == 'b.py'
        assert manifest[0]['summary'] == '原始文件'  # Summary carried over.


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
