"""minimum changes required pre-commit hook."""

import argparse
import re
import subprocess


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to check.")
    parser.add_argument(
        "--min-changes",
        default="2",
        help="Check for at least min-changes in git diff (defaults to 2).",
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

    retval = 0
    for filename in args.filenames:
        output = (
            subprocess.check_output(["git", "diff", "--numstat", "--cached", filename])
            .decode()
            .strip()
        )

        if output != "":
            lines_add, lines_del = re.findall(r"(\d+)\s+(\d+)\s+", output)[0]

            if max(int(lines_add), int(lines_del)) < int(args.min_changes):
                retval = retval + 1
                print(
                    f"INFO: file `{filename}` does not have at least `{args.min_changes}` changes"
                )
                if args.auto_revert:
                    print(f"INFO: auto reverting `{filename}`")
                    print(subprocess.check_output(["git", "checkout", "--", filename]))
    return retval


if __name__ == "__main__":
    exit(main())
