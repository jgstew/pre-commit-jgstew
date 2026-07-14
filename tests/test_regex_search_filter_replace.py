"""Tests for pre_commit_hooks/regex_search_filter_replace.py."""

import pytest

from pre_commit_hooks import regex_search_filter_replace as hook


def write(tmp_path, name, content):
    """Write content to tmp_path/name and return the path str."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


# --- first_changed_line -------------------------------------------------- #
def test_first_changed_line_middle():
    assert hook.first_changed_line("a\nb\nc\n", "a\nX\nc\n") == 2


def test_first_changed_line_appended_tail():
    assert hook.first_changed_line("a\nb\n", "a\nb\nc\n") == 3


def test_first_changed_line_identical():
    # no differing line within the common prefix -> one past the shorter
    assert hook.first_changed_line("a\nb\n", "a\nb\n") == 3


# --- validate_filepath --------------------------------------------------- #
def test_validate_filepath_ok(tmp_path):
    path = write(tmp_path, "real.txt", "x")
    assert hook.validate_filepath(path) == path


def test_validate_filepath_missing_raises(tmp_path):
    with pytest.raises(ValueError):
        hook.validate_filepath(str(tmp_path / "missing.txt"))


# --- main (default: collapse blank lines between tags) ------------------- #
def test_default_collapses_blank_lines_with_overwrite(tmp_path):
    path = write(tmp_path, "a.xml", "<a>x</a>\n\n\n<b>y</b>\n")
    assert hook.main(["--overwrite", path]) == 1
    assert open(path).read() == "<a>x</a>\n<b>y</b>\n"


def test_no_change_returns_zero(tmp_path):
    path = write(tmp_path, "a.xml", "<a>x</a>\n<b>y</b>\n")
    assert hook.main(["--overwrite", path]) == 0


def test_reports_but_does_not_write_without_overwrite(tmp_path):
    original = "<a>x</a>\n\n\n<b>y</b>\n"
    path = write(tmp_path, "a.xml", original)
    assert hook.main([path]) == 1
    # not overwritten
    assert open(path).read() == original


def test_custom_search_filter_replace(tmp_path):
    path = write(tmp_path, "a.txt", "color: FOO;\n")
    assert (
        hook.main(
            [
                "--search=color: FOO;",
                "--filter=FOO",
                "--replace=red",
                "--overwrite",
                path,
            ]
        )
        == 1
    )
    assert open(path).read() == "color: red;\n"


def test_nonexistent_file_is_rejected_by_argparse(tmp_path):
    # filenames use type=validate_filepath, so a missing path is an argparse error
    with pytest.raises(SystemExit):
        hook.main([str(tmp_path / "missing.xml")])


def test_filter_not_within_search_match_raises(tmp_path):
    # the search matches but the filter finds nothing inside it -> IndexError
    path = write(tmp_path, "a.xml", "<a>x</a>\n\n\n<b>y</b>\n")
    with pytest.raises(IndexError):
        hook.main(["--search=<a>x</a>", "--filter=ZZZ", "--replace=q", path])
