"""Load skills/filetree/scripts/filetree.py as the `filetree` module.

scripts/ is not a Python package, so use importlib.spec_from_file_location.
"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / 'skills' / 'filetree' / 'scripts' / 'filetree.py'

spec = importlib.util.spec_from_file_location('filetree', SCRIPT_PATH)
filetree = importlib.util.module_from_spec(spec)
sys.modules['filetree'] = filetree
spec.loader.exec_module(filetree)
