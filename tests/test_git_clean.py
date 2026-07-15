"""Tests for pre_commit_jgstew/git_clean.py."""

from pre_commit_jgstew import git_clean as hook


def test_clean_repo_reports_zero(git_repo, monkeypatch):
    git_repo.write("tracked.txt", "content\n")
    git_repo.commit_all()
    monkeypatch.chdir(git_repo.path)
    assert hook.git_clean(auto_clean=False) == 0
    assert hook.main([]) == 0


def test_untracked_files_are_counted_dry_run(git_repo, monkeypatch):
    git_repo.write("tracked.txt", "content\n")
    git_repo.commit_all()
    git_repo.write("junk1.tmp", "x\n")
    git_repo.write("junk2.tmp", "y\n")
    monkeypatch.chdir(git_repo.path)
    # dry run: files are only "would remove", not deleted
    assert hook.git_clean(auto_clean=False) == 2
    assert (git_repo.path / "junk1.tmp").exists()


def test_auto_clean_removes_untracked(git_repo, monkeypatch):
    git_repo.write("tracked.txt", "content\n")
    git_repo.commit_all()
    junk = git_repo.write("junk.tmp", "x\n")
    monkeypatch.chdir(git_repo.path)
    assert hook.git_clean(auto_clean=True) == 1
    assert not junk.exists()


def test_outside_git_repo_is_handled(tmp_path, monkeypatch):
    # `git clean` fails outside a repo; the error is caught and 0 is returned
    monkeypatch.chdir(tmp_path)
    assert hook.git_clean(auto_clean=False) == 0
