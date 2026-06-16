"""Unit tests for hooks/commit_guard.py — the PreToolUse commit gate.

hooks/ is not a package, so the module is loaded by path (mirroring how conftest
loads filetree.py). Command-detection and the decision logic are pure functions;
the lint subprocess and repo-root lookup are monkeypatched so tests stay fast and
hermetic.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_PATH = REPO_ROOT / 'hooks' / 'commit_guard.py'

_spec = importlib.util.spec_from_file_location('commit_guard', GUARD_PATH)
commit_guard = importlib.util.module_from_spec(_spec)
sys.modules['commit_guard'] = commit_guard
_spec.loader.exec_module(commit_guard)


class TestIsGitCommit:
    @pytest.mark.parametrize('command', [
        'git commit',
        'git commit -m "msg"',
        "git commit -m 'add; stuff'",        # operator chars inside quotes don't split
        'git commit --amend --no-edit',
        'git add . && git commit -m x',       # compound: 2nd segment matches
        'git -C /repo commit -m x',           # value-taking global opt skipped
        'git -c user.name=x commit',          # -c key=val (space form) skipped
        'git --git-dir=/r/.git commit',       # --opt=value single token skipped
        'env FOO=bar git commit -m x',        # benign wrapper + assignment
        'GIT_AUTHOR_NAME=x git commit',       # leading env assignment
        '/usr/bin/git commit',                # absolute git path (basename match)
    ])
    def test_positive(self, command):
        assert commit_guard.is_git_commit(command) is True

    @pytest.mark.parametrize('command', [
        '',
        'git status',
        'git push',
        'git log --grep=commit',              # subcommand is log, not commit
        'git commitfoo',                       # not the commit subcommand
        'git --version',                       # all options, no subcommand reached
        'echo git commit',                     # echo is the command word, not git
        'ls -la',
        'python3 filetree.py lint',
    ])
    def test_negative(self, command):
        assert commit_guard.is_git_commit(command) is False

    def test_unbalanced_quotes_fall_back_to_loose_split(self):
        # shlex.split raises on the dangling quote; the fallback split still finds it.
        assert commit_guard.is_git_commit('git commit -m "oops') is True


@pytest.fixture
def guarded_repo(tmp_path, monkeypatch):
    """Temp repo with commit_guard enabled and _repo_root pinned to it — the shared
    precondition for the 'guard is on' decide() tests."""
    (tmp_path / '.filetree.json').write_text(json.dumps({'commit_guard': True}))
    monkeypatch.setattr(commit_guard, '_repo_root', lambda: str(tmp_path))
    return tmp_path


class TestDecide:
    """decide() returns a deny dict only when a guarded, git-repo commit drifts;
    every off-ramp returns None (allow), proving the fail-open design."""

    def _stdin(self, command):
        return json.dumps({'tool_name': 'Bash', 'tool_input': {'command': command}})

    def test_non_commit_allows(self):
        assert commit_guard.decide(self._stdin('git status')) is None

    def test_malformed_json_allows(self):
        assert commit_guard.decide('{ not json') is None

    def test_missing_tool_input_allows(self):
        assert commit_guard.decide(json.dumps({'tool_name': 'Bash'})) is None

    def test_non_git_tree_allows(self, monkeypatch):
        monkeypatch.setattr(commit_guard, '_repo_root', lambda: None)
        assert commit_guard.decide(self._stdin('git commit')) is None

    def test_guard_disabled_allows(self, tmp_path, monkeypatch):
        monkeypatch.setattr(commit_guard, '_repo_root', lambda: str(tmp_path))
        # No .filetree.json → commit_guard defaults False.
        assert commit_guard.decide(self._stdin('git commit')) is None

    def test_guard_enabled_clean_allows(self, guarded_repo, monkeypatch):
        monkeypatch.setattr(commit_guard, '_lint_drift', lambda: False)
        assert commit_guard.decide(self._stdin('git commit -m x')) is None

    def test_guard_enabled_drift_denies(self, guarded_repo, monkeypatch):
        monkeypatch.setattr(commit_guard, '_lint_drift', lambda: True)
        decision = commit_guard.decide(self._stdin('git commit -m x'))
        out = decision['hookSpecificOutput']
        assert out['hookEventName'] == 'PreToolUse'
        assert out['permissionDecision'] == 'deny'
        assert 'FILETREE.md' in out['permissionDecisionReason']

    def test_malformed_config_fails_open(self, tmp_path, monkeypatch):
        # A broken .filetree.json makes load_config sys.exit; the guard must catch
        # that (SystemExit) and allow rather than wedge every commit.
        (tmp_path / '.filetree.json').write_text('{ not json')
        monkeypatch.setattr(commit_guard, '_repo_root', lambda: str(tmp_path))
        assert commit_guard.decide(self._stdin('git commit')) is None

    def test_unreadable_config_fails_open(self, tmp_path, monkeypatch):
        # A non-SystemExit failure (e.g. the config can't be read) must also allow,
        # not propagate and exit non-zero.
        def boom():
            raise OSError('unreadable')
        monkeypatch.setattr(commit_guard, '_repo_root', lambda: str(tmp_path))
        monkeypatch.setattr(commit_guard.filetree_config, 'load_config', boom)
        assert commit_guard.decide(self._stdin('git commit')) is None

    def test_lint_crash_fails_open(self, guarded_repo, monkeypatch):
        def boom():
            raise RuntimeError('lint blew up')
        monkeypatch.setattr(commit_guard, '_lint_drift', boom)
        assert commit_guard.decide(self._stdin('git commit')) is None


class TestRepoRoot:
    def test_returns_toplevel_in_git_repo(self, git_repo):
        root = commit_guard._repo_root()
        assert Path(root).resolve() == git_repo.resolve()

    def test_none_outside_git_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert commit_guard._repo_root() is None


class TestLintDrift:
    """_lint_drift maps lint's exit code to a block decision: only exit 1 (drift)
    blocks; 0 (clean) and 2+ (crash) do not, preserving fail-open."""

    @pytest.mark.parametrize('returncode,expected', [(0, False), (1, True), (2, False)])
    def test_exit_code_mapping(self, monkeypatch, returncode, expected):
        monkeypatch.setattr(
            commit_guard.subprocess, 'run',
            lambda *a, **k: subprocess.CompletedProcess(a, returncode, '', ''),
        )
        assert commit_guard._lint_drift() is expected


class TestMain:
    def test_prints_deny_json_on_drift(self, monkeypatch, capsys):
        monkeypatch.setattr(commit_guard, 'decide', lambda raw: {'x': 1})
        monkeypatch.setattr('sys.stdin', __import__('io').StringIO('{}'))
        commit_guard.main()
        assert json.loads(capsys.readouterr().out) == {'x': 1}

    def test_prints_nothing_on_allow(self, monkeypatch, capsys):
        monkeypatch.setattr(commit_guard, 'decide', lambda raw: None)
        monkeypatch.setattr('sys.stdin', __import__('io').StringIO('{}'))
        commit_guard.main()
        assert capsys.readouterr().out == ''

    def test_broken_stdin_fails_open(self, monkeypatch, capsys):
        # A broken stdin pipe must not crash the hook (which would exit non-zero).
        class BrokenStdin:
            def read(self):
                raise OSError('broken pipe')
        monkeypatch.setattr('sys.stdin', BrokenStdin())
        commit_guard.main()  # must not raise
        assert capsys.readouterr().out == ''
