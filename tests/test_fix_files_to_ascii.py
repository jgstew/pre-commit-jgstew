"""Tests for pre_commit_jgstew/fix_files_to_ascii.py.

Requires the `anyascii` package (installed via install_requires); skipped where
it is not available.
"""

import pytest

pytest.importorskip("anyascii")

from pre_commit_jgstew import fix_files_to_ascii as hook  # noqa: E402


def write(tmp_path, name, text):
    """Write `text` to tmp_path/name as UTF-8; return the path as a str."""
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def read(path):
    """Read a file back as UTF-8 text."""
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def test_ascii_file_unchanged(tmp_path):
    path = write(tmp_path, "a.txt", "plain ascii\n")
    assert hook.main([path]) == 0
    assert read(path) == "plain ascii\n"


def test_accents_transliterated(tmp_path):
    path = write(tmp_path, "b.txt", "caf\u00e9\n")
    assert hook.main([path]) == 1
    assert read(path) == "cafe\n"


def test_smart_quotes_and_dashes(tmp_path):
    path = write(tmp_path, "c.txt", "\u201csmart\u201d \u2013 dash\n")
    assert hook.main([path]) == 1
    assert read(path) == '"smart" - dash\n'


@pytest.mark.parametrize(
    "arrow, expected",
    [
        ("\u2190", "<-"),  # left
        ("\u2192", "->"),  # right
        ("\u2194", "<->"),  # left-right
        ("\u21d2", "->"),  # right double
        ("\u27f5", "<-"),  # long left
        ("\u27a1", "->"),  # black right
    ],
)
def test_arrows_keep_direction(tmp_path, arrow, expected):
    path = write(tmp_path, "d.txt", f"a {arrow} b\n")
    assert hook.main([path]) == 1
    assert read(path) == f"a {expected} b\n"


def test_arrow_with_other_nonascii(tmp_path):
    # pre-substitution (arrow) and anyascii (accent) both apply
    path = write(tmp_path, "d2.txt", "caf\u00e9 \u2192 done\n")
    assert hook.main([path]) == 1
    assert read(path) == "cafe -> done\n"


def test_result_is_ascii(tmp_path):
    path = write(tmp_path, "e.txt", "na\u00efve 50\u00b5m \U0001f600\n")
    hook.main([path])
    assert read(path).isascii()


def test_idempotent(tmp_path):
    path = write(tmp_path, "f.txt", "r\u00e9sum\u00e9\n")
    assert hook.main([path]) == 1  # first run fixes
    assert hook.main([path]) == 0  # second run is a no-op


def test_multiple_files_mixed(tmp_path):
    good = write(tmp_path, "good.txt", "ascii\n")
    bad = write(tmp_path, "bad.txt", "\u00fcber\n")
    assert hook.main([good, bad]) == 1
    assert read(good) == "ascii\n"
    assert read(bad) == "uber\n"


def test_no_files_is_zero():
    assert hook.main([]) == 0


def test_unreadable_binary_is_skipped(tmp_path):
    path = tmp_path / "bin.dat"
    path.write_bytes(b"\xff\xfe\x00\x01not utf-8")
    # invalid UTF-8 -> skipped with a note, does not fail the hook
    assert hook.main([str(path)]) == 0
    assert path.read_bytes() == b"\xff\xfe\x00\x01not utf-8"


def test_fix_text_helper():
    assert hook.fix_text("plain") == "plain"
    assert hook.fix_text("\u00e9").isascii()


def test_crlf_preserved(tmp_path):
    path = tmp_path / "crlf.txt"
    path.write_bytes("caf\u00e9\r\nna\u00efve\r\n".encode())
    assert hook.main([str(path)]) == 1
    assert path.read_bytes() == b"cafe\r\nnaive\r\n"


def test_lf_preserved(tmp_path):
    path = tmp_path / "lf.txt"
    path.write_bytes("caf\u00e9\nna\u00efve\n".encode())
    assert hook.main([str(path)]) == 1
    assert path.read_bytes() == b"cafe\nnaive\n"


def test_mixed_endings_preserved(tmp_path):
    # each line's ending is left exactly as-is; only the content is fixed
    path = tmp_path / "mixed.txt"
    path.write_bytes("\u00e9\r\n\u00fc\n".encode())
    assert hook.main([str(path)]) == 1
    assert path.read_bytes() == b"e\r\nu\n"


def test_ascii_crlf_untouched(tmp_path):
    path = tmp_path / "plain.txt"
    path.write_bytes(b"a\r\nb\r\n")
    assert hook.main([str(path)]) == 0
    assert path.read_bytes() == b"a\r\nb\r\n"
