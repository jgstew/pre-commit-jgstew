"""git clean pre-commit hook."""

import argparse
import subprocess


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to check.")
    parser.add_argument(
        "--auto-clean",
        action="store_true",
        help="DANGER! will delete untracked files automatically",
    )
    return parser


def git_clean(auto_clean=False):
    """do git clean -f"""
    cmd_flag = "-n"
    if auto_clean:
        cmd_flag = "-f"
    run_cmd = ["git", "clean", cmd_flag]
    run_output = ""
    print(f"Running: {run_cmd}")
    try:
        run_output = subprocess.check_output(run_cmd).decode()
    except subprocess.CalledProcessError as err:
        print(err)

    print(run_output)
    return run_output.count("Removing ") + run_output.count("Would remove ")


def main(argv=None):
    """execution starts here"""
    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)
    return git_clean(args.auto_clean)


if __name__ == "__main__":
    exit(main())
