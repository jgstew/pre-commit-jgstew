"""pre-commit hook to search, filter, and replace strings in files.

The default functionality is to remove extra newlines between XML or HTML tags"""

import argparse
import os
import re


def validate_filepath(filepath=""):
    """validate string is filepath or URL"""
    if os.path.isfile(filepath) and os.access(filepath, os.R_OK):
        return filepath
    else:
        raise ValueError(filepath)


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


def first_changed_line(before, after):
    """Return the 1-based number of the first line that differs.

    Used to print a clickable `path:line` reference. If the common prefix is
    identical and the change is only an added/removed tail, the first line past
    the shorter version is returned.
    """
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    for lineno, (old, new) in enumerate(zip(before_lines, after_lines), start=1):
        if old != new:
            return lineno
    return min(len(before_lines), len(after_lines)) + 1


def main(argv=None):
    """Main process.

    Apply the search/filter/replace to each file. A file is only rewritten when
    its contents actually change; when that happens it is reported as `path:line`
    (the first changed line, so editors/terminals can jump straight to it).
    Returns the number of files that changed, so the hook exits non-zero -- and
    pre-commit asks for a re-stage -- only when it actually modified something.
    """

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    changed = 0
    for filename in args.filenames:
        with open(filename, "r") as f:
            original = f.read()

        # get matches of search RegEx
        filetext = original
        for match in re.findall(args.search, filetext):
            # get only the filtered part of the match
            filtered_match = re.findall(args.filter, match)[0]
            # replace the filtered part of the match with the replacement value
            replaced_match = match.replace(filtered_match, args.replace)
            # save result to file representation
            filetext = filetext.replace(match, replaced_match)

        # only act when the content actually changed
        if filetext != original:
            changed = changed + 1
            # report as path:line so the location is clickable
            print(f"{filename}:{first_changed_line(original, filetext)}")
            # if configured, overwrite the original file with the changes
            if args.overwrite:
                with open(filename, "w") as f:
                    f.write(filetext)

    return changed


if __name__ == "__main__":
    exit(main())
