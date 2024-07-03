"""pre-commit hook to validate BigFix BES files."""

import argparse

import validate_plist_xml


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to check.")

    return parser


def main(argv=None):
    """Main process."""

    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    retval = 0
    for filename in args.filenames:
        print(filename)
        if not validate_plist_xml.validate_plist_xml.validate_plist(filename):
            retval = retval + 1

    return retval


if __name__ == "__main__":
    exit(main())
