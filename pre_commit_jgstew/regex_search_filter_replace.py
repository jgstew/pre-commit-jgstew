"""A pre-commit hook to search, filter, and replace strings in files.

The default functionality is to remove extra newlines between XML or HTML tags
"""

import argparse
import codecs
import os
import re


def validate_filepath(filepath=""):
    """Validate string is filepath or URL."""
    if os.path.isfile(filepath) and os.access(filepath, os.R_OK):
        return filepath
    else:
        raise ValueError(filepath)


def validate_encoding(name=""):
    """Validate that `name` is a known codec, so a typo fails at parse time."""
    try:
        codecs.lookup(name)
    except LookupError as err:
        raise argparse.ArgumentTypeError(str(err))
    return name


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
    parser.add_argument(
        "--encoding",
        default="utf-8",
        type=validate_encoding,
        help="text encoding used to read and write the files (default: utf-8)",
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
    A file in scope that is not valid UTF-8 cannot be processed and is reported
    as an error (rather than silently skipped). Returns the count of files that
    changed or failed, so the hook exits non-zero -- and pre-commit asks for a
    re-stage or surfaces the failure -- whenever it modified or could not process
    something.
    """

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    changed = 0
    failed = 0
    for filename in args.filenames:
        # newline="" reads the bytes without universal-newline translation, so
        # the file's real line endings are known. The search/filter/replace runs
        # on an LF-normalized copy (the patterns -- the default and the usual
        # custom ones -- are written with `\n`), then the file's original ending
        # style is restored on write so a CRLF file stays CRLF.
        try:
            with open(filename, encoding=args.encoding, newline="") as f:
                raw = f.read()
        except UnicodeDecodeError as err:
            # The file is in the hook's scope but does not decode with the chosen
            # encoding, so it cannot be processed. Treat that as a FAILURE, not a
            # silent skip: if the file was meant to be out of scope, the user
            # should exclude it from the hook -- otherwise they would expect it to
            # be processed, and a quiet skip would hide that it was not.
            failed += 1
            print(
                f"{filename}: ERROR: could not decode as {args.encoding}, "
                f"cannot process ({err})"
            )
            continue
        uses_crlf = "\r\n" in raw
        original = raw.replace("\r\n", "\n").replace("\r", "\n")

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
            # if configured, overwrite the original file with the changes,
            # restoring the file's original line-ending style (CRLF -> CRLF)
            if args.overwrite:
                out = filetext.replace("\n", "\r\n") if uses_crlf else filetext
                try:
                    # encode BEFORE opening: open("w") truncates on open, so
                    # encoding first means a failure leaves the file untouched.
                    data = out.encode(args.encoding)
                except UnicodeEncodeError as err:
                    failed += 1
                    print(
                        f"{filename}: ERROR: could not encode the result as "
                        f"{args.encoding}, cannot write (likely a character in "
                        f"the --replace string): {err}"
                    )
                    continue
                with open(filename, "wb") as f:
                    f.write(data)
            changed = changed + 1
            # report as path:line so the location is clickable
            print(f"{filename}:{first_changed_line(original, filetext)}")

    # non-zero exit when files were changed (re-stage) or could not be processed
    return changed + failed


if __name__ == "__main__":
    exit(main())
