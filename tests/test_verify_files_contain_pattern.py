"""Tests for pre_commit_hooks/verify_files_contain_pattern.py."""

from pre_commit_hooks import verify_files_contain_pattern as hook


def write(tmp_path, name, content):
    """Write content to tmp_path/name and return the path str."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_default_pattern_match_passes(tmp_path):
    path = write(tmp_path, "a.xml", "<Title>Hello</Title>\n")
    assert hook.main([path]) == 0


def test_no_match_fails_by_default(tmp_path):
    # default: num-matches=-1, allow-none False -> a file with 0 matches fails
    path = write(tmp_path, "a.xml", "no title here\n")
    assert hook.main([path]) == 1


def test_no_match_allowed_with_allow_none(tmp_path):
    path = write(tmp_path, "a.xml", "no title here\n")
    assert hook.main(["--allow-none", path]) == 0


def test_num_matches_zero_forbids_match(tmp_path):
    path = write(tmp_path, "a.xml", "<Title>Nope</Title>\n")
    assert hook.main(["--num-matches=0", path]) == 1


def test_num_matches_zero_passes_when_absent(tmp_path):
    path = write(tmp_path, "a.xml", "clean file\n")
    assert hook.main(["--num-matches=0", path]) == 0


def test_custom_pattern(tmp_path):
    path = write(tmp_path, "a.txt", "VERSION=1.2.3\n")
    assert hook.main([r"--re-pattern=VERSION=(\d+\.\d+\.\d+)", path]) == 0
    assert hook.main([r"--re-pattern=NOPE=(\d+)", path]) == 1


def test_fewer_than_required_matches_fails(tmp_path):
    # only one <Title> but two required
    path = write(tmp_path, "a.xml", "<Title>Only One</Title>\n")
    assert hook.main(["--num-matches=2", path]) == 1


def test_enough_matches_passes(tmp_path):
    path = write(tmp_path, "a.xml", "<Title>One</Title>\n<Title>Two</Title>\n")
    assert hook.main(["--num-matches=2", path]) == 0


def test_no_allow_extra_fails_on_too_many_matches(tmp_path):
    # two matches but only one required, and extras are now forbidden
    path = write(tmp_path, "a.xml", "<Title>One</Title>\n<Title>Two</Title>\n")
    assert hook.main(["--num-matches=1", "--no-allow-extra", path]) == 1
    # default (extras allowed) still passes
    assert hook.main(["--num-matches=1", path]) == 0


def test_append_filepath_lets_path_satisfy_the_match(tmp_path):
    # the version lives in the filename, not the contents
    path = write(tmp_path, "app-1.2.3.txt", "no version in here\n")
    pattern = r"--re-pattern=(\d+\.\d+\.\d+)"
    # without --append-filepath: no match in contents -> fail
    assert hook.main([pattern, path]) == 1
    # with --append-filepath: the path is searched too -> match found
    assert hook.main([pattern, "--append-filepath", path]) == 0
