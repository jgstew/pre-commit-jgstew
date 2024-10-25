"""
github action set output fix pre-commit hook. WORK IN PROGRESS

This hook is meant to run once manually to fix github actions

This is not meant to be run every commit

https://github.blog/changelog/2022-10-11-github-actions-deprecating-save-state-and-set-output-commands/

Bad Example:
run: echo "::set-output name=version::$(python ./setup.py --version)"
Good Example:
run: echo "version=$(python ./setup.py --version)" >> $GITHUB_OUTPUT

Bad Example:
run: echo "::set-output name={name}::{value}"
Good Example:
run: echo "{name}={value}" >> $GITHUB_OUTPUT

RegEx to match:
echo +(["']::set-output +name=(\S+?)::(.+?)["'])(?:$| \|)
"""

import argparse


def build_argument_parser():
    """Build and return the argument parser."""

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("filenames", nargs="*", help="Filenames to check.")

    return parser


def main(argv=None):
    """execution starts here"""
    # Parse command line arguments.
    argparser = build_argument_parser()
    args = argparser.parse_args(argv)

    retval = 0
    for filename in args.filenames:
        print(filename)
        # check if file ends with ".yml" or ".yaml"
        # should only apply to github actions (files in `.github` folder)

        # check if file contains: /.+echo +(["']::set-output +name=/

        # if not, continue

        # if so, check that line does not start with # (if so continue)

        # get results from /echo +(["']::set-output +name=(\S+?)::(.+?)["'])(?:$| \|)/

        # if results, then:
        # retval = retval + 1
        # do replacement fix

    return retval


if __name__ == "__main__":
    exit(main())
