"""git clean pre-commit hook."""

import subprocess


def git_clean():
    """do the git clean"""
    run_cmd = ["git", "clean", "-f"]
    run_output = ""
    try:
        run_output = subprocess.check_output(run_cmd).decode()
    except subprocess.CalledProcessError as err:
        print(err)

    print(run_output)


def main(argv=None):
    """execution starts here"""
    git_clean()
