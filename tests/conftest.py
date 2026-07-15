"""Shared pytest fixtures for the pre_commit_hooks test suite."""

import subprocess
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parent / "examples"


class GitRepo:
    """A throwaway git repository for exercising the git-backed hooks."""

    def __init__(self, path) -> None:
        self.path = path

    def run(self, *args):
        """Run a git subcommand in the repo and return its stdout."""
        return subprocess.run(
            ["git", *args],
            cwd=self.path,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def write(self, relpath, content):
        """Write content to relpath inside the repo, creating parents."""
        target = self.path / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def stage(self, relpath):
        """Git add a single path."""
        self.run("add", relpath)

    def commit_all(self, message="commit"):
        """Stage everything and commit."""
        self.run("add", "-A")
        self.run("commit", "-q", "-m", message)


@pytest.fixture
def git_repo(tmp_path):
    """Create an initialized git repo with identity/signing configured."""
    repo = tmp_path / "repo"
    repo.mkdir()
    obj = GitRepo(repo)
    obj.run("init", "-q")
    obj.run("config", "user.email", "test@example.com")
    obj.run("config", "user.name", "Test User")
    obj.run("config", "commit.gpgsign", "false")
    return obj
