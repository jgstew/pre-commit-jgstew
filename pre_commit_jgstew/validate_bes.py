"""A pre-commit hook to validate BigFix BES files."""

import argparse
import sys

import validate_bes_xml

# This hook has moved to https://github.com/jgstew/pre-commit-bigfix and will
# be removed from pre-commit-jgstew in the next release. main() prints this on
# stderr every run (the hook entry sets `verbose: true` so pre-commit shows it
# even on success).
DEPRECATION_BANNER = """\
****************************************************************************
* DEPRECATED: `validate-bes` has MOVED to a new repo:                      *
*     https://github.com/jgstew/pre-commit-bigfix                          *
* It will be REMOVED from pre-commit-jgstew in the next release.           *
* Update your .pre-commit-config.yaml:                                     *
*   - repo: https://github.com/jgstew/pre-commit-bigfix                    *
*     rev: v0.2.0                                                          *
*     hooks:                                                               *
*       - id: validate-bes                                                 *
****************************************************************************"""


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to check.")

    return parser


def main(argv=None):
    """Main process."""

    print(DEPRECATION_BANNER, file=sys.stderr)

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    retval = 0
    for filename in args.filenames:
        print(filename)
        if not validate_bes_xml.validate_xml(filename):
            retval = retval + 1

    return retval


if __name__ == "__main__":
    exit(main())
