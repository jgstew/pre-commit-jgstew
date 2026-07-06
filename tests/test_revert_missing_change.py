"""Tests for pre_commit_hooks/revert_missing_change.py."""

from pre_commit_hooks import revert_missing_change as hook


def test_no_files_returns_zero():
    assert hook.main([]) == 0


def test_revert_file_restores_committed_content(git_repo, monkeypatch):
    git_repo.write("f.txt", "original\n")
    git_repo.commit_all()
    git_repo.write("f.txt", "modified\n")
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    hook.revert_file("f.txt")
    assert (git_repo.path / "f.txt").read_text() == "original\n"
