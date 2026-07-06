"""Tests for pre_commit_hooks/minimum_changes.py."""

from pre_commit_hooks import minimum_changes as hook


def test_small_staged_change_fails(git_repo, monkeypatch):
    git_repo.write("f.txt", "line1\n")
    git_repo.commit_all()
    git_repo.write("f.txt", "line1\nline2\n")  # +1 line
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    assert hook.main(["--min-changes=2", "f.txt"]) == 1


def test_large_staged_change_passes(git_repo, monkeypatch):
    git_repo.write("f.txt", "line1\n")
    git_repo.commit_all()
    git_repo.write("f.txt", "line1\na\nb\nc\nd\n")  # +4 lines
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    assert hook.main(["--min-changes=2", "f.txt"]) == 0


def test_no_staged_change_passes(git_repo, monkeypatch):
    git_repo.write("f.txt", "line1\n")
    git_repo.commit_all()
    monkeypatch.chdir(git_repo.path)
    # nothing staged -> empty numstat -> no failure
    assert hook.main(["--min-changes=2", "f.txt"]) == 0


def test_auto_revert_unstages_small_change(git_repo, monkeypatch):
    git_repo.write("f.txt", "line1\n")
    git_repo.commit_all()
    git_repo.write("f.txt", "line1\nsmall\n")
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    assert hook.main(["--min-changes=2", "--auto-revert", "f.txt"]) == 1
    # after auto-revert the working copy is back to the committed content
    assert (git_repo.path / "f.txt").read_text() == "line1\n"
