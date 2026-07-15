"""Tests for pre_commit_jgstew/add_main_docstring.py."""

import sys

from pre_commit_jgstew import add_main_docstring as hook

MISSING = """\
def main():
    return 1
"""

HAS_DOCSTRING = '''\
def main():
    """Already documented."""
    return 1
'''

EMPTY_DOCSTRING = """\
def main():
    ""
    return 1
"""

WITH_COMMENT_BEFORE_BODY = """\
def main():
    # a leading comment
    return 1
"""


def write(tmp_path, name, content):
    """Write content to tmp_path/name and return the Path."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# --- find_mains_without_docstring --------------------------------------- #
def test_finds_main_missing_docstring():
    assert len(hook.find_mains_without_docstring(MISSING)) == 1


def test_ignores_main_with_docstring():
    assert hook.find_mains_without_docstring(HAS_DOCSTRING) == []


def test_flags_empty_docstring():
    assert len(hook.find_mains_without_docstring(EMPTY_DOCSTRING)) == 1


def test_syntax_error_returns_empty():
    assert hook.find_mains_without_docstring("def (:\n") == []


def test_non_main_function_ignored():
    assert hook.find_mains_without_docstring("def helper():\n    return 1\n") == []


# --- add_docstring ------------------------------------------------------- #
def test_add_docstring_inserts_and_reports_change(tmp_path):
    path = write(tmp_path, "m.py", MISSING)
    assert hook.add_docstring(path) is True
    text = path.read_text()
    assert f'"""{hook.DOCSTRING}"""' in text
    # result must parse and the docstring is the first body statement
    import ast

    tree = ast.parse(text)
    fn = tree.body[0]
    assert ast.get_docstring(fn) == hook.DOCSTRING


def test_add_docstring_noop_when_present(tmp_path):
    path = write(tmp_path, "m.py", HAS_DOCSTRING)
    assert hook.add_docstring(path) is False
    assert path.read_text() == HAS_DOCSTRING


def test_add_docstring_handles_comment_before_body(tmp_path):
    path = write(tmp_path, "m.py", WITH_COMMENT_BEFORE_BODY)
    assert hook.add_docstring(path) is True
    import ast

    assert ast.get_docstring(ast.parse(path.read_text()).body[0]) == hook.DOCSTRING


# --- main (reads sys.argv) ---------------------------------------------- #
def test_main_updates_file(tmp_path, monkeypatch):
    path = write(tmp_path, "m.py", MISSING)
    monkeypatch.setattr(sys, "argv", ["add-missing-docstrings", str(path)])
    hook.main()
    assert f'"""{hook.DOCSTRING}"""' in path.read_text()


def test_main_no_args_exits(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(sys, "argv", ["add-missing-docstrings"])
    with pytest.raises(SystemExit):
        hook.main()


def test_main_processes_directory(tmp_path, monkeypatch):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "m.py").write_text(MISSING, encoding="utf-8")
    (pkg / "already.py").write_text(HAS_DOCSTRING, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["add-missing-docstrings", str(pkg)])
    hook.main()
    assert f'"""{hook.DOCSTRING}"""' in (pkg / "m.py").read_text()


def test_main_skips_hidden_directories(tmp_path, monkeypatch):
    hidden = tmp_path / ".venv"
    hidden.mkdir()
    (hidden / "m.py").write_text(MISSING, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["add-missing-docstrings", str(tmp_path)])
    hook.main()
    # files under a dotted dir are skipped -> left untouched
    assert (hidden / "m.py").read_text() == MISSING


def test_main_skips_non_python_file(tmp_path, monkeypatch, capsys):
    txt = tmp_path / "notes.txt"
    txt.write_text("not python\n", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["add-missing-docstrings", str(txt)])
    hook.main()
    assert "Skipping non-Python file" in capsys.readouterr().out
