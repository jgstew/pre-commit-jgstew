"""Tests guarding the pre_commit_hooks -> pre_commit_jgstew package rename.

These verify the package directory, the distribution name in setup.cfg, and
that the old name is no longer referenced anywhere in the packaging metadata.
"""

import configparser
import importlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "pre_commit_jgstew"
DIST_NAME = "pre-commit-jgstew"


def test_new_package_is_importable():
    """The renamed package imports and exposes a version."""
    module = importlib.import_module(PACKAGE)
    assert hasattr(module, "__version__")
    assert module.__version__


def test_old_package_directory_is_gone():
    """The old package directory no longer exists."""
    assert not (REPO_ROOT / "pre_commit_hooks").exists()


def test_new_package_directory_exists():
    """The renamed package directory exists with an __init__.py."""
    pkg_dir = REPO_ROOT / PACKAGE
    assert pkg_dir.is_dir()
    assert (pkg_dir / "__init__.py").is_file()


def _read_setup_cfg():
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "setup.cfg", encoding="utf-8")
    return parser


def test_setup_cfg_distribution_name():
    """The setup.cfg declares the new distribution name."""
    parser = _read_setup_cfg()
    assert parser["metadata"]["name"] == DIST_NAME


def test_setup_cfg_packages_reference_new_name():
    """The setup.cfg packages/version attr point at the new package."""
    parser = _read_setup_cfg()
    assert parser["options"]["packages"].strip() == PACKAGE
    assert PACKAGE in parser["metadata"]["version"]


def test_setup_cfg_entry_points_reference_new_package():
    """Every console_scripts entry point targets the new package."""
    parser = _read_setup_cfg()
    entry_points = parser["options.entry_points"]["console_scripts"]
    assert entry_points.strip()
    assert "pre_commit_hooks." not in entry_points
    for line in entry_points.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # each mapping looks like "name = pre_commit_jgstew.module:main"
        assert line.split("=", 1)[1].strip().startswith(PACKAGE + ".")


def test_setup_cfg_has_no_old_name():
    """No stale references to the old dashed or underscored name remain."""
    text = (REPO_ROOT / "setup.cfg").read_text(encoding="utf-8")
    assert "pre-commit-hooks" not in text
    assert "pre_commit_hooks" not in text
