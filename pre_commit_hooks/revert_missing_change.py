"""minimum changes required pre-commit hook."""

import argparse
import re
import subprocess


def revert_file(filename):
    """automatically revert file"""
    print(f"INFO: auto reverting `{filename}`")
    # https://docs.gitlab.com/ee/topics/git/numerous_undo_possibilities_in_git/
    # git reset HEAD filename
    subprocess.check_output(["git", "reset", "HEAD", filename])
    # git checkout -- filename
    subprocess.check_output(["git", "checkout", "--", filename])


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to check.")
    parser.add_argument(
        "--change_regex",
        default="v[0-9]+",
        help="RegEx string to check for in git history.",
    )
    parser.add_argument(
        "--auto-revert",
        action="store_true",
        help="DANGER! will revert the file automatically",
    )
    return parser


def main(argv=None):
    """Main process."""

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    return_value = 0
    for filename in args.filenames:
        exit_code = 0
        # git diff --cached --ignore-all-space --diff-filter=M -G 'v[0-9]' test/example.test_file
        # git diff --cached --exit-code --ignore-all-space --diff-filter=M -G "<SourceReleaseDate>.+</SourceReleaseDate>" "$FILEPATH"
        sub_command = [
            "git",
            "diff",
            "--cached",
            "--exit-code",
            "--ignore-all-space",
            "--diff-filter=M",
            "-G",
            args.change_regex,
            filename,
        ]
        try:
            output = subprocess.check_output(sub_command, shell=True)
            if output != "":
                print(output.decode().strip())
        except subprocess.CalledProcessError as subprocerr:
            exit_code = subprocerr.returncode
        print(exit_code)
        if exit_code == 0:
            return_value = return_value + 1
            print(
                f"INFO: file `{filename}` does not contain `{args.change_regex}` in git diff"
            )
            if args.auto_revert:
                revert_file(filename)
    return return_value


if __name__ == "__main__":
    exit(main())
