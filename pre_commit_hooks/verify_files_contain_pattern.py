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
        default="(?i)<Title>(.+)</Title>",
        help="Check for pattern match in file",
    )
    parser.add_argument(
        "--num-matches",
        default="-1",
        help="minimum number of matches to be found, 0 means no matches, -1 means any number of matches",
    )
    parser.add_argument(
        # NOTE: this has no effect if --num-matches=0
        "--allow-none",
        default=False,
        action="store_true",
        help="pass files that have 0 matches found",
    )

    return parser


def main(argv=None):
    """Main process."""

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    re_pattern = re.compile(args.re_pattern)

    target_match_count = int(args.num_matches)

    allow_none = bool(args.allow_none)

    retval = 0
    for filename in args.filenames:
        with open(filename, "r") as f:
            matches = re.findall(re_pattern, "\n".join(f.readlines()))

            if target_match_count == 0 and len(matches) == 0:
                # success
                continue
            if target_match_count == 0 and len(matches) != 0:
                # fail
                retval = retval + 1
                print(f"Found unwanted match in {filename}, expected 0")
                continue
            if len(matches) == 0:
                if allow_none:
                    # success
                    continue
                # fail
                retval = retval + 1
                print(f"No match found in {filename}")
                continue
            if len(matches) < target_match_count:
                retval = retval + 1
                print(f"Less than {target_match_count} matches in {filename}")
                continue

    return retval


if __name__ == "__main__":
    exit(main())
