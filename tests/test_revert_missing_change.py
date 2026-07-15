"""Tests for pre_commit_jgstew/revert_missing_change.py."""

import subprocess

import pytest

from pre_commit_jgstew import revert_missing_change as hook


def test_no_files_returns_zero():
    assert hook.main([]) == 0


def test_staged_change_missing_the_required_pattern_is_flagged(git_repo, monkeypatch):
    # committed file contains a version line
    git_repo.write("f.txt", "title\nv1.0.0\n")
    git_repo.commit_all()
    # stage a change that does NOT touch the version line -> the required
    # `v[0-9]` change is "missing" and should be flagged (return 1)
    git_repo.write("f.txt", "new title\nv1.0.0\n")
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    assert hook.main(["--change_regex=v[0-9]", "f.txt"]) == 1


def test_staged_change_containing_the_pattern_passes(git_repo, monkeypatch):
    git_repo.write("f.txt", "title\nv1.0.0\n")
    git_repo.commit_all()
    # the staged change DOES touch a `v[0-9]` line -> present -> not flagged
    git_repo.write("f.txt", "title\nv2.0.0\n")
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    assert hook.main(["--change_regex=v[0-9]", "f.txt"]) == 0


def test_auto_revert_restores_when_change_missing(git_repo, monkeypatch):
    git_repo.write("f.txt", "title\nv1.0.0\n")
    git_repo.commit_all()
    git_repo.write("f.txt", "changed title\nv1.0.0\n")
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    assert hook.main(["--change_regex=v[0-9]", "--auto-revert", "f.txt"]) == 1
    assert (git_repo.path / "f.txt").read_text() == "title\nv1.0.0\n"


def test_revert_file_restores_committed_content(git_repo, monkeypatch):
    git_repo.write("f.txt", "original\n")
    git_repo.commit_all()
    git_repo.write("f.txt", "modified\n")
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    hook.revert_file("f.txt")
    assert (git_repo.path / "f.txt").read_text() == "original\n"


def test_revert_file_raises_outside_git_repo(tmp_path, monkeypatch):
    # negative counterpart: git fails outside a repo -> the error propagates
    monkeypatch.chdir(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        hook.revert_file("whatever.txt")
