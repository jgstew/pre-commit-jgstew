"""pre-commit hook to validate that the file being commited has regex matches or groups that are lines within a reference file."""

import argparse
import os
import re


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to check.")
    parser.add_argument(
        "--re-pattern",
        # default="(?i)<Title>(.+)</Title>",
        help="Check for pattern match in reference file",
    )
    parser.add_argument(
        "--ref-file",
        # default="test/example.test_file",
        help="reference file to search within",
    )

    return parser


def main(argv=None):
    """Main process."""

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    re_pattern = re.compile(args.re_pattern)

    with open(os.path.abspath(args.ref_file), "r") as f:
        ref_file_array = f.read().splitlines()
    # print(ref_file_array)

    retval = 0
    for filename in args.filenames:
        print(filename)
        with open(filename, "r") as f:
            matches = re.findall(re_pattern, "\n".join(f.readlines()))

        for match in matches:
            if match not in ref_file_array:
                print(f"ERROR: Match `{match}` not found in reference file")
                retval = retval + 1

    return retval


if __name__ == "__main__":
    exit(main())
