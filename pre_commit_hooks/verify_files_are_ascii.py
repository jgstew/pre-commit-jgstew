"""pre-commit hook to validate that the file being committed is ascii."""

import argparse
import string


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to check.")
    parser.add_argument(
        "--printable",
        default=True,
        action="store_true",
        help="check that file contains only printable characters",
    )
    parser.add_argument(
        # NOTE: this has no effect if --num-matches=0
        "--ascii",
        default=True,
        action="store_true",
        help="check that file contains only ascii",
    )

    return parser


def main(argv=None):
    """Main process."""

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    is_printable = bool(args.printable)

    is_ascii = bool(args.ascii)

    printableset = set(string.printable)

    retval = 0
    for filename in args.filenames:
        with open(filename, "r") as f:
            file_contents = f.read()

            if is_ascii and not file_contents.isascii():
                print(f"file: {filename} contains non-ascii chars")
                retval += 1

            if is_printable and not set(file_contents).issubset(printableset):
                print(f"file: {filename} contains non-pritable characters")
                retval += 1

    return retval


if __name__ == "__main__":
    exit(main())
