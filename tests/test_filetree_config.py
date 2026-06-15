"""Unit tests for filetree_config.py — the file-indexability + project-config module.

Tested directly (not through filetree.py's re-exports): built-in skip rules, the
.filetree.json schema/validation, gitignore-style matching, and the layered
filter_indexable. filetree.py's own behavior lives in test_filetree.py.
"""

import json
from pathlib import Path

import pytest

import filetree_config


class TestShouldSkip:
    def test_binary_extensions(self):
        assert filetree_config.should_skip('logo.png')
        assert filetree_config.should_skip('font.woff2')
        assert filetree_config.should_skip('demo.MP4')  # Case-insensitive.

    def test_lock_files(self):
        assert filetree_config.should_skip('package-lock.json')
        assert filetree_config.should_skip('poetry.lock')
        assert filetree_config.should_skip('a/b/yarn.lock')  # Skipped in subdirectories too.

    def test_manifest_itself_excluded_by_filter_not_should_skip(self):
        # The manifest is no longer a built-in SKIP_FILENAME (its path is configurable);
        # filter_indexable drops it via config.manifest_path instead.
        assert not filetree_config.should_skip('FILETREE.md')
        assert filetree_config.filter_indexable(
            ['FILETREE.md', 'a.py'], filetree_config.Config()) == ['a.py']

    def test_normal_code_files(self):
        assert not filetree_config.should_skip('src/auth.py')
        assert not filetree_config.should_skip('README.md')
        assert not filetree_config.should_skip('Makefile')


class TestLoadConfig:
    """load_config parses .filetree.json strictly; absent file = all defaults."""

    def test_absent_file_yields_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = filetree_config.load_config()
        assert cfg.manifest_path == 'FILETREE.md'
        assert cfg.exclude == [] and cfg.include == [] and cfg.language is None

    def test_full_valid_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path('.filetree.json').write_text(json.dumps({
            'manifest_path': 'docs/TREE.md',
            'exclude': ['migrations/*'],
            'include': ['*.svg'],
            'language': 'zh',
        }), encoding='utf-8')
        cfg = filetree_config.load_config()
        assert cfg.manifest_path == 'docs/TREE.md'
        assert cfg.exclude == ['migrations/*']
        assert cfg.include == ['*.svg']
        assert cfg.language == 'zh'

    def test_language_null_is_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path('.filetree.json').write_text(json.dumps({'language': None}), encoding='utf-8')
        assert filetree_config.load_config().language is None

    def test_manifest_path_normalized_to_posix(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        Path('.filetree.json').write_text(json.dumps({'manifest_path': 'a/b/TREE.md'}),
                                          encoding='utf-8')
        assert filetree_config.load_config().manifest_path == 'a/b/TREE.md'

    @pytest.mark.parametrize('payload,needle', [
        ('{ not json', 'not valid JSON'),
        ('[]', 'must be a JSON object'),
        ('{"excludes": []}', 'unknown key'),
        ('{"manifest_path": "/abs/x.md"}', 'relative path'),
        ('{"manifest_path": "../escape.md"}', 'relative path'),
        ('{"manifest_path": ""}', 'non-empty string'),
        ('{"manifest_path": 5}', 'non-empty string'),
        ('{"exclude": "not-a-list"}', 'list of strings'),
        ('{"include": [1, 2]}', 'list of strings'),
        ('{"language": ""}', 'non-empty string or null'),
    ])
    def test_invalid_config_exits_with_context(self, tmp_path, monkeypatch, payload, needle):
        monkeypatch.chdir(tmp_path)
        Path('.filetree.json').write_text(payload, encoding='utf-8')
        with pytest.raises(SystemExit) as ei:
            filetree_config.load_config()
        assert needle in str(ei.value.code)

    def test_manifest_path_existing_dir_rejected(self, tmp_path, monkeypatch):
        # An existing dir would crash write_manifest's tmp.replace with IsADirectoryError;
        # reject it at load time with a clear message instead.
        monkeypatch.chdir(tmp_path)
        Path('docs').mkdir()
        Path('.filetree.json').write_text(json.dumps({'manifest_path': 'docs'}),
                                          encoding='utf-8')
        with pytest.raises(SystemExit) as ei:
            filetree_config.load_config()
        assert 'existing directory' in str(ei.value.code)

    def test_annotations_lazy_for_py39_compat(self):
        # from __future__ import annotations must keep the PEP 604 union a string;
        # otherwise `str | None` evaluates at class-body time and breaks import on 3.9.
        assert filetree_config.Config.__annotations__['language'] == 'str | None'

    def test_init_scaffold_equals_all_defaults(self, tmp_path, monkeypatch):
        # The .filetree.json scaffold /filetree:init offers to write must parse to
        # all-defaults, so dropping it changes nothing until the user edits it. If a
        # Config default ever changes, this fails — a reminder to update init.md too.
        monkeypatch.chdir(tmp_path)
        Path('.filetree.json').write_text(json.dumps({
            'manifest_path': 'FILETREE.md', 'exclude': [], 'include': [], 'language': None,
        }), encoding='utf-8')
        cfg, default = filetree_config.load_config(), filetree_config.Config()
        assert (cfg.manifest_path, cfg.exclude, cfg.include, cfg.language) == (
            default.manifest_path, default.exclude, default.include, default.language)


class TestMatchGitignore:
    """match_gitignore delegates to git, so it honors full gitignore semantics
    while staying isolated from the repo's own .gitignore."""

    def test_empty_patterns_or_paths(self, git_repo):
        assert filetree_config.match_gitignore(['a.py'], []) == set()
        assert filetree_config.match_gitignore([], ['*.py']) == set()

    def test_glob_matches_any_depth(self, git_repo):
        matched = filetree_config.match_gitignore(
            ['a.gen.ts', 'src/b.gen.ts', 'c.ts'], ['*.gen.ts'])
        assert matched == {'a.gen.ts', 'src/b.gen.ts'}

    def test_directory_pattern(self, git_repo):
        matched = filetree_config.match_gitignore(
            ['migrations/001.sql', 'src/app.py'], ['migrations/'])
        assert matched == {'migrations/001.sql'}

    def test_root_anchoring(self, git_repo):
        matched = filetree_config.match_gitignore(
            ['build/x.o', 'sub/build/y.o'], ['/build'])
        assert 'build/x.o' in matched
        assert 'sub/build/y.o' not in matched

    def test_double_star(self, git_repo):
        matched = filetree_config.match_gitignore(
            ['a/c.py', 'a/b/c.py', 'a/b/d/c.py', 'x/c.py'], ['a/**/c.py'])
        assert matched == {'a/c.py', 'a/b/c.py', 'a/b/d/c.py'}

    def test_negation(self, git_repo):
        matched = filetree_config.match_gitignore(
            ['a.gen.ts', 'keep.gen.ts'], ['*.gen.ts', '!keep.gen.ts'])
        assert matched == {'a.gen.ts'}

    def test_isolated_from_repo_gitignore(self, git_repo):
        """A repo .gitignore hit must NOT leak into our match set (would over-exclude)."""
        Path('.gitignore').write_text('*.log\n', encoding='utf-8')
        matched = filetree_config.match_gitignore(['a.log', 'b.py'], ['*.py'])
        assert matched == {'b.py'}  # a.log matches repo .gitignore, not our pattern

    def test_not_shadowed_by_repo_gitignore(self, git_repo):
        """Regression: a repo .gitignore matching the SAME path must NOT defeat our pattern.

        core.excludesFile is git's lowest-precedence ignore source, so the old
        source-filter approach silently dropped our match when the host repo ignored
        the same path. Evaluating in an isolated repo fixes it.
        """
        Path('.gitignore').write_text('build/\n', encoding='utf-8')
        matched = filetree_config.match_gitignore(['build/x.o', 'a.py'], ['build/'])
        assert matched == {'build/x.o'}


class TestFilterIndexable:
    """filter_indexable layers config exclude/include over the built-in skip."""

    def test_exclude_removes_tracked_file(self, git_repo):
        cfg = filetree_config.Config(exclude=['*.gen.ts'])
        assert filetree_config.filter_indexable(['a.py', 'b.gen.ts'], cfg) == ['a.py']

    def test_include_rescues_builtin_skip(self, git_repo):
        cfg = filetree_config.Config(include=['*.svg'])
        # Order is preserved from the input (not re-sorted).
        assert filetree_config.filter_indexable(['logo.svg', 'a.py'], cfg) == ['logo.svg', 'a.py']

    def test_exclude_wins_over_include(self, git_repo):
        cfg = filetree_config.Config(exclude=['*.svg'], include=['*.svg'])
        assert filetree_config.filter_indexable(['logo.svg', 'a.py'], cfg) == ['a.py']

    def test_custom_manifest_path_excluded(self, git_repo):
        cfg = filetree_config.Config(manifest_path='docs/TREE.md')
        assert filetree_config.filter_indexable(['docs/TREE.md', 'a.py'], cfg) == ['a.py']
