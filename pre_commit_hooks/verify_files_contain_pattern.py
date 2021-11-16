"""pre-commit hook to validate that the file being commited has regex matches or groups that are lines within a reference file."""

import argparse
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
        help="Check for pattern match in file",
    )
    parser.add_argument(
        "--num-matches",
        default="-1",
        help="minimum number of matches to be found, 0 means no matches, -1 means any number of matches",
    )

    return parser


def main(argv=None):
    """Main process."""

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    re_pattern = re.compile(args.re_pattern)

    target_match_count = int(args.num_matches)

    retval = 0
    for filename in args.filenames:
        with open(filename, "r") as f:
            matches = re.findall(re_pattern, "\n".join(f.readlines()))

            if target_match_count == 0 and len(matches) == 0:
                # succeed
                continue
            # need to check this logic:
            if len(matches) == 0 or len(matches) < target_match_count:
                # fail
                retval = retval + 1
                print(f"No match found in {filename}")

    return retval


if __name__ == "__main__":
    exit(main())
