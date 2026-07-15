"""Tests for pre_commit_jgstew/verify_files_contain_entry.py."""

from pathlib import Path

from pre_commit_jgstew import verify_files_contain_entry as hook

EXAMPLES = Path(__file__).parent / "examples"
REF = str(EXAMPLES / "example.test_file")


def write(tmp_path, name, content):
    """Write content to tmp_path/name and return the path str."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_match_present_in_reference_passes(tmp_path):
    # "Example Title" is a line in tests/examples/example.test_file
    path = write(tmp_path, "a.xml", "<Title>Example Title</Title>\n")
    assert hook.main(["--ref-file", REF, path]) == 0


def test_match_absent_from_reference_fails(tmp_path):
    path = write(tmp_path, "a.xml", "<Title>Not In The Reference</Title>\n")
    assert hook.main(["--ref-file", REF, path]) == 1


def test_no_matches_is_zero(tmp_path):
    # nothing matches the pattern -> nothing to check against the reference
    path = write(tmp_path, "a.xml", "plain text, no title tags\n")
    assert hook.main(["--ref-file", REF, path]) == 0
