"""pre-commit hook to search, filter, and replace strings in files.

The default functionality is to remove extra newlines between XML or HTML tags"""

import argparse
import re

from shared_utils import validate_filepath


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "filenames", nargs="*", type=validate_filepath, help="Filenames to check."
    )
    parser.add_argument(
        "--search",
        default=r"<\/[\w\d]+>\n\n+\s*<[\w\d]+>",
        help="search pattern to replace in file",
    )
    parser.add_argument(
        "--filter",
        default="\n\n+",
        help="filter result string to just part you wish to replace",
    )
    parser.add_argument(
        "--replace",
        default="\n",
        help="string to replace the result to",
    )
    parser.add_argument(
        # NOTE: this has no effect if --num-matches=0
        "--overwrite",
        default=False,
        action="store_true",
        help="overwrite file with changes",
    )
    return parser


def main(argv=None):
    """Main process."""

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    retval = 0
    for filename in args.filenames:
        print(filename)
        with open(filename, "r") as f:
            filetext = f.read()
            # get matches of search RegEx
            matches = re.findall(args.search, filetext)

        # print(matches)

        for match in matches:
            retval = retval + 1
            # get only the filtered part of the match
            filtered_match = re.findall(args.filter, match)[0]
            # print(filtered_match)
            # replace the filtered part of the match with the replacement value
            replaced_match = match.replace(filtered_match, args.replace)
            # print(replaced_match)
            # save result to file representation
            filetext = filetext.replace(match, replaced_match)
            # print(filetext)

        # if configured, overwrite the original file
        if args.overwrite:
            with open(filename, "w") as f:
                f.write(filetext)

    return retval


if __name__ == "__main__":
    print(__name__)
    exit_code = main()
    print("Matches:", exit_code)
    exit(exit_code)
