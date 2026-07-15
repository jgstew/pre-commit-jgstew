"""Validate packaging metadata stays internally consistent.

Three files describe this project's hooks and can silently drift apart:

* ``setup.cfg``            -- ``console_scripts`` (name = module:function)
* ``.pre-commit-hooks.yaml`` -- pre-commit hook definitions (id, entry, ...)
* the ``pre_commit_jgstew`` package -- the modules/functions behind them

These tests catch the common regressions: a renamed/deleted module, a typo in
an entry-point target, or a hook whose ``entry`` no longer maps to a real
console script.
"""

import configparser
import importlib
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "pre_commit_jgstew"

_HAS_YAML = importlib.util.find_spec("yaml") is not None
_HAS_TOMLLIB = importlib.util.find_spec("tomllib") is not None


def _setup_cfg():
    parser = configparser.ConfigParser()
    parser.read(REPO_ROOT / "setup.cfg", encoding="utf-8")
    return parser


def _console_scripts():
    """Return {script-name: "module:function"} from setup.cfg."""
    raw = _setup_cfg()["options.entry_points"]["console_scripts"]
    mapping = {}
    for line in raw.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        name, target = line.split("=", 1)
        mapping[name.strip()] = target.strip()
    return mapping


CONSOLE_SCRIPTS = _console_scripts()


def _hooks():
    import yaml

    return yaml.safe_load(
        (REPO_ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    )


# --------------------------------------------------------------------------- #
# setup.cfg console_scripts <-> package modules
# --------------------------------------------------------------------------- #
def test_console_scripts_present():
    """Setup.cfg actually declares console scripts (guards a broken parse)."""
    assert CONSOLE_SCRIPTS


@pytest.mark.parametrize("name, target", sorted(CONSOLE_SCRIPTS.items()))
def test_entry_point_target_is_importable(name, target):
    """Each console_scripts target resolves to a callable in the package."""
    assert ":" in target, f"{name}: entry point {target!r} lacks ':function'"
    module_path, func_name = target.split(":", 1)
    assert module_path.startswith(
        PACKAGE + "."
    ), f"{name}: {module_path} is not in the {PACKAGE} package"
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as exc:
        # A missing *third-party* dependency is an environment gap, not a
        # metadata bug -- skip like the per-hook tests do. A missing module
        # inside our own package IS a bug, so let that fail.
        missing = exc.name or ""
        if missing.split(".")[0] == PACKAGE:
            raise
        pytest.skip(f"{name}: optional dependency {missing!r} not installed")
    func = getattr(module, func_name, None)
    assert callable(func), f"{name}: {target} is not callable"


@pytest.mark.parametrize("name, target", sorted(CONSOLE_SCRIPTS.items()))
def test_entry_point_module_file_exists(name, target):
    """The module file backing each console script exists on disk."""
    module_path = target.split(":", 1)[0]
    relative = Path(*module_path.split(".")).with_suffix(".py")
    assert (REPO_ROOT / relative).is_file(), f"{name}: missing {relative}"


def test_console_script_names_are_unique():
    """No duplicate console script names (configparser would hide dupes)."""
    raw = _setup_cfg()["options.entry_points"]["console_scripts"]
    names = [
        line.split("=", 1)[0].strip()
        for line in raw.strip().splitlines()
        if line.strip()
    ]
    assert len(names) == len(set(names)), "duplicate console_scripts names"


# --------------------------------------------------------------------------- #
# .pre-commit-hooks.yaml
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not available")
def test_hook_ids_are_unique():
    """Every hook id in .pre-commit-hooks.yaml is unique."""
    ids = [hook["id"] for hook in _hooks()]
    assert len(ids) == len(set(ids)), "duplicate hook ids"


@pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not available")
def test_hooks_have_required_fields():
    """Each hook declares the fields pre-commit needs to run it."""
    for hook in _hooks():
        for field in ("id", "name", "description", "entry", "language"):
            assert hook.get(field), f"hook {hook.get('id')!r} missing {field}"


@pytest.mark.skipif(not _HAS_YAML, reason="PyYAML not available")
def test_hook_entries_map_to_console_scripts():
    """Every hook entry is backed by a real console script in setup.cfg."""
    for hook in _hooks():
        entry = hook["entry"].split()[0]  # entry may carry trailing args
        assert (
            entry in CONSOLE_SCRIPTS
        ), f"hook {hook['id']!r} entry {entry!r} has no console_scripts target"


# --------------------------------------------------------------------------- #
# pyproject.toml
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not _HAS_TOMLLIB, reason="tomllib not available (<3.11)")
def test_pyproject_is_valid_and_declares_build_backend():
    """Pyproject.toml parses and names a build backend."""
    import tomllib

    with open(REPO_ROOT / "pyproject.toml", "rb") as handle:
        data = tomllib.load(handle)
    assert data["build-system"]["build-backend"]
    assert data["build-system"]["requires"]
