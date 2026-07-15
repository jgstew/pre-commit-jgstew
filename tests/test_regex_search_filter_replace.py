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


# --- line-ending preservation -------------------------------------------- #
def test_crlf_preserved_on_change(tmp_path):
    path = tmp_path / "crlf.xml"
    path.write_bytes(b"<a>x</a>\r\n\r\n\r\n<b>y</b>\r\n")  # CRLF with blank lines
    assert hook.main(["--overwrite", str(path)]) == 1
    # blank lines collapsed AND endings stay CRLF
    assert path.read_bytes() == b"<a>x</a>\r\n<b>y</b>\r\n"


def test_lf_preserved_on_change(tmp_path):
    path = tmp_path / "lf.xml"
    path.write_bytes(b"<a>x</a>\n\n\n<b>y</b>\n")
    assert hook.main(["--overwrite", str(path)]) == 1
    assert path.read_bytes() == b"<a>x</a>\n<b>y</b>\n"


def test_crlf_preserved_with_newline_in_pattern(tmp_path):
    # a search pattern containing \n (as the jgstew-recipes URLDownloader hook
    # uses) still matches on a CRLF file, and the result stays CRLF
    path = tmp_path / "crlf.yaml"
    path.write_bytes(b"  Processor: URLDownloaderPython\r\nnext\r\n")
    assert (
        hook.main(
            [
                "--search= Processor: URLDownloaderPython\n",
                "--filter= URLDownloaderPython",
                "--replace= com.github.jgstew.SharedProcessors/URLDownloaderPython",
                "--overwrite",
                str(path),
            ]
        )
        == 1
    )
    assert path.read_bytes() == (
        b"  Processor: com.github.jgstew.SharedProcessors/URLDownloaderPython\r\n"
        b"next\r\n"
    )


def test_crlf_no_change_not_rewritten(tmp_path):
    path = tmp_path / "crlf.xml"
    before = b"<a>x</a>\r\n<b>y</b>\r\n"
    path.write_bytes(before)
    assert hook.main(["--overwrite", str(path)]) == 0
    assert path.read_bytes() == before


# --- non-UTF-8 files fail (not silently skipped) ------------------------- #
def test_non_utf8_file_is_reported_as_failure(tmp_path, capsys):
    path = tmp_path / "latin1.xml"
    # 0xe9 is a valid Latin-1 'e-acute' but invalid as a lone UTF-8 byte
    path.write_bytes(b"<a>caf\xe9</a>\n")
    assert hook.main(["--overwrite", str(path)]) == 1  # counts as a failure
    out = capsys.readouterr().out
    assert "ERROR" in out and "latin1.xml" in out
    # the file is left untouched
    assert path.read_bytes() == b"<a>caf\xe9</a>\n"


def test_non_utf8_failure_alongside_a_change(tmp_path):
    bad = tmp_path / "bad.xml"
    bad.write_bytes(b"<a>\xe9</a>\n")
    good = tmp_path / "good.xml"
    good.write_bytes(b"<a>x</a>\n\n\n<b>y</b>\n")
    # 1 changed + 1 failed -> return 2 (non-zero)
    assert hook.main(["--overwrite", str(good), str(bad)]) == 2
    assert good.read_bytes() == b"<a>x</a>\n<b>y</b>\n"


# --- --encoding argument ------------------------------------------------- #
def test_encoding_latin1_roundtrips(tmp_path):
    # a Latin-1 file processed with --encoding=latin-1 keeps its bytes/encoding
    path = tmp_path / "latin1.txt"
    path.write_bytes(b"caf\xe9 FOO\n")  # 'cafe'+e-acute (0xe9) then FOO
    assert (
        hook.main(
            [
                "--encoding=latin-1",
                "--search=FOO",
                "--filter=FOO",
                "--replace=BAR",
                "--overwrite",
                str(path),
            ]
        )
        == 1
    )
    # content changed, but the e-acute is still a single 0xe9 Latin-1 byte
    assert path.read_bytes() == b"caf\xe9 BAR\n"


def test_default_utf8_fails_on_latin1(tmp_path):
    path = tmp_path / "latin1.txt"
    path.write_bytes(b"caf\xe9 FOO\n")
    # without --encoding, the default utf-8 cannot decode 0xe9 -> reported failure
    assert hook.main(["--search=FOO", "--filter=FOO", "--replace=BAR", str(path)]) == 1
    assert path.read_bytes() == b"caf\xe9 FOO\n"  # untouched


def test_invalid_encoding_is_argparse_error(tmp_path):
    path = write(tmp_path, "a.txt", "x")
    with pytest.raises(SystemExit):
        hook.main(["--encoding=not-a-real-codec", path])


# --- write-encode failure is a reported hook failure --------------------- #
def test_replacement_unencodable_in_target_encoding_fails(tmp_path, capsys):
    # a Latin-1 file, but the replacement contains a char Latin-1 cannot encode
    # (a rightwards arrow). The write must fail as a reported hook failure, not
    # an uncaught traceback.
    path = tmp_path / "latin1.txt"
    path.write_bytes(b"caf\xe9 FOO\n")
    ret = hook.main(
        [
            "--encoding=latin-1",
            "--search=FOO",
            "--filter=FOO",
            "--replace=\u2192",  # rightwards arrow, not in Latin-1
            "--overwrite",
            str(path),
        ]
    )
    assert ret == 1  # counted as a failure
    out = capsys.readouterr().out
    assert "ERROR" in out and "replace" in out.lower()
    # the file must be left untouched (no partial/garbage write)
    assert path.read_bytes() == b"caf\xe9 FOO\n"
