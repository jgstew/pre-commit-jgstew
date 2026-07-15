"""Test that `setup.py --version` emits a well-formed version string.

Mirrors running `python ./setup.py --version` at the command line. It only
checks the shape (int.int.int), not the exact value, so it does not need
updating on every release.
"""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# `setup.py` needs setuptools in the interpreter running it. Some minimal test
# environments (e.g. an isolated pytest install) lack it; skip rather than fail
# spuriously there. CI installs the build deps, so the check still runs.
_HAS_SETUPTOOLS = importlib.util.find_spec("setuptools") is not None

# Leading anchor only: setuptools may append pre/post/dev segments (e.g.
# "1.6.3.dev0"), but the core release must be MAJOR.MINOR.PATCH.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+")


@pytest.mark.skipif(not _HAS_SETUPTOOLS, reason="setuptools not available")
def test_setup_py_emits_semver():
    """`setup.py --version` prints an int.int.int version on stdout."""
    result = subprocess.run(
        [sys.executable, "setup.py", "--version"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    version = result.stdout.strip()
    assert VERSION_RE.match(version), f"unexpected version output: {version!r}"
