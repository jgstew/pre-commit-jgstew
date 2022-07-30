"""shared utility scripts"""

import os
import subprocess


def revert_file(filename):
    """automatically revert file"""
    print(f"INFO: auto reverting `{filename}`")
    # https://docs.gitlab.com/ee/topics/git/numerous_undo_possibilities_in_git/
    # git reset HEAD filename
    subprocess.check_output(["git", "reset", "HEAD", filename])
    # git checkout -- filename
    subprocess.check_output(["git", "checkout", "--", filename])


def validate_filepath_or_url(filepath_or_url=""):
    """validate string is filepath or URL"""
    if ("://" in filepath_or_url) or (
        os.path.isfile(filepath_or_url) and os.access(filepath_or_url, os.R_OK)
    ):
        return filepath_or_url
    else:
        raise ValueError(filepath_or_url)


def validate_filepath(filepath=""):
    """validate string is filepath or URL"""
    if os.path.isfile(filepath) and os.access(filepath, os.R_OK):
        return filepath
    else:
        raise ValueError(filepath)


if __name__ == "__main__":
    print("WARNING: This does nothing, it is shared utility functions")
