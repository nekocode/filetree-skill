"""Load skills/filetree/scripts/filetree.py as the `filetree` module.

scripts/ is not a Python package, so use importlib.spec_from_file_location.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / 'skills' / 'filetree' / 'scripts' / 'filetree.py'

# filetree.py imports its sibling filetree_config.py, and tests import filetree_config
# directly. When run as a CLI the script dir is on sys.path automatically; under
# importlib loading it is not, so add it for both modules.
sys.path.insert(0, str(SCRIPT_PATH.parent))

spec = importlib.util.spec_from_file_location('filetree', SCRIPT_PATH)
filetree = importlib.util.module_from_spec(spec)
sys.modules['filetree'] = filetree
spec.loader.exec_module(filetree)


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
