"""A pre-commit hook to transliterate non-ASCII file contents to ASCII.

This is the auto-fixing companion to `verify-files-are-ascii`: instead of just
failing when a file contains non-ASCII characters, it rewrites the file in place,
transliterating every non-ASCII character to a close ASCII equivalent using the
`anyascii` library (e.g. `cafe` <- cafe with an accent, `"` <- smart quotes,
`-` <- en/em dashes, `:grinning:` <- an emoji).

A small pre-substitution table runs first, before anyascii, for characters
whose default anyascii mapping loses information: directional arrows become
`->` / `<-` (and `<->` for a left-right arrow) rather than a bare `>` / `<`.

Like other auto-fixing hooks it exits non-zero when it changes a file, so the
change is reviewed and re-staged. Files that cannot be read as UTF-8 text (e.g.
binaries that slipped past the `types: [text]` filter) are skipped with a note
and do not fail the hook.

Existing line endings are preserved exactly (CRLF stays CRLF, LF stays LF): the
file is read and written with newline translation disabled, and only non-ASCII
content is transliterated -- the `\r` and `\n` bytes are ASCII and pass through
untouched.
"""

import argparse

from anyascii import anyascii

# characters to substitute BEFORE anyascii runs, because anyascii's default
# mapping drops the direction (a rightwards arrow -> ">"). Keys must be single
# characters (applied with str.translate).
PRE_SUBSTITUTIONS = {
    "\u2190": "<-",  # LEFTWARDS ARROW
    "\u2192": "->",  # RIGHTWARDS ARROW
    "\u2194": "<->",  # LEFT RIGHT ARROW
    "\u21d0": "<-",  # LEFTWARDS DOUBLE ARROW
    "\u21d2": "->",  # RIGHTWARDS DOUBLE ARROW
    "\u21d4": "<->",  # LEFT RIGHT DOUBLE ARROW
    "\u27f5": "<-",  # LONG LEFTWARDS ARROW
    "\u27f6": "->",  # LONG RIGHTWARDS ARROW
    "\u27f7": "<->",  # LONG LEFT RIGHT ARROW
    "\u2b05": "<-",  # LEFTWARDS BLACK ARROW
    "\u27a1": "->",  # BLACK RIGHTWARDS ARROW
}
PRE_SUBSTITUTION_TABLE = {ord(char): repl for char, repl in PRE_SUBSTITUTIONS.items()}


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to fix.")

    return parser


def fix_text(text):
    """Return `text` with every non-ASCII character transliterated to ASCII.

    Text that is already ASCII is returned unchanged. Otherwise the
    pre-substitution table (directional arrows) is applied first, then anyascii
    transliterates whatever remains.
    """
    if text.isascii():
        return text
    return anyascii(text.translate(PRE_SUBSTITUTION_TABLE))


def main(argv=None):
    """Main process."""

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    retval = 0
    for filename in args.filenames:
        try:
            # newline="" disables universal-newline translation, so existing
            # CRLF / CR / LF line endings are preserved exactly (anyascii and the
            # pre-substitution table leave ASCII control chars, including \r and
            # \n, untouched -- only non-ASCII content is transliterated).
            with open(filename, encoding="utf-8", newline="") as f:
                original = f.read()
        except (UnicodeDecodeError, OSError) as err:
            print(f"file: {filename} could not be read as UTF-8 text; skipping ({err})")
            continue

        fixed = fix_text(original)
        if fixed != original:
            with open(filename, "w", encoding="utf-8", newline="") as f:
                f.write(fixed)
            print(f"file: {filename} transliterated non-ascii chars to ascii")
            retval = 1

    return retval


if __name__ == "__main__":
    exit(main())
