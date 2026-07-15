"""Tests for pre_commit_jgstew/shared_utils.py."""

import subprocess

import pytest

from pre_commit_jgstew import shared_utils


def test_validate_filepath_ok(tmp_path):
    path = tmp_path / "real.txt"
    path.write_text("x", encoding="utf-8")
    assert shared_utils.validate_filepath(str(path)) == str(path)


def test_validate_filepath_missing_raises(tmp_path):
    with pytest.raises(ValueError):
        shared_utils.validate_filepath(str(tmp_path / "nope.txt"))


def test_validate_filepath_or_url_accepts_url():
    url = "https://example.com/thing"
    assert shared_utils.validate_filepath_or_url(url) == url


def test_validate_filepath_or_url_accepts_existing_file(tmp_path):
    path = tmp_path / "real.txt"
    path.write_text("x", encoding="utf-8")
    assert shared_utils.validate_filepath_or_url(str(path)) == str(path)


def test_validate_filepath_or_url_rejects_missing(tmp_path):
    with pytest.raises(ValueError):
        shared_utils.validate_filepath_or_url(str(tmp_path / "nope.txt"))


def test_revert_file_restores_committed_content(git_repo, monkeypatch):
    git_repo.write("f.txt", "original\n")
    git_repo.commit_all()
    git_repo.write("f.txt", "changed\n")
    git_repo.stage("f.txt")
    monkeypatch.chdir(git_repo.path)
    shared_utils.revert_file("f.txt")
    assert (git_repo.path / "f.txt").read_text() == "original\n"


def test_revert_file_raises_outside_git_repo(tmp_path, monkeypatch):
    # negative counterpart: git fails outside a repo -> the error propagates
    monkeypatch.chdir(tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        shared_utils.revert_file("whatever.txt")
