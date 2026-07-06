"""Tests for pre_commit_hooks/verify_files_are_ascii.py."""

from pre_commit_hooks import verify_files_are_ascii as hook


def write(tmp_path, name, content):
    """Write content (text) to tmp_path/name and return the path str."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_plain_ascii_passes(tmp_path):
    path = write(tmp_path, "ok.txt", "hello world\nsecond line\n")
    assert hook.main([path]) == 0


def test_non_ascii_char_counts_twice(tmp_path):
    # a non-ascii char (e-acute) fails BOTH the ascii and printable checks
    # -> +2. Written as a \u escape so this test file stays pure ASCII.
    path = write(tmp_path, "bad.txt", "caf\u00e9\n")
    assert hook.main([path]) == 2


def test_non_printable_ascii_control_char(tmp_path):
    # a NUL is ascii (passes ascii check) but not printable -> +1
    path = write(tmp_path, "ctrl.txt", "abc\x00def")
    assert hook.main([path]) == 1


def test_multiple_files_sum(tmp_path):
    good = write(tmp_path, "good.txt", "fine\n")
    bad = write(tmp_path, "bad.txt", "\u00e9")
    assert hook.main([good, bad]) == 2


def test_no_files_is_zero():
    assert hook.main([]) == 0
