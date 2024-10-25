r"""
github action set output fix pre-commit hook.

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

Invoke this standalone:
python3 pre_commit_hooks/github_action_set_output_fix.py .github/workflows/tag_and_release.yaml
"""

import argparse
import re


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
        if not filename.lower().endswith((".yml", ".yaml")):
            continue

        # should only apply to github actions (files in `.github` folder)
        if not ".github" in filename.lower():
            continue

        # check if file contains: /.+echo +(["']::set-output +name=/
        with open(filename, "r") as f:
            filetext = f.read()

            # check if file has the set-output issue:
            matches = re.findall(
                r"(?m).+echo +([\"']::set-output +name=(\S+?)::(.+?)[\"'])(?:$| \|)",
                filetext,
            )

            # do replacement fix
            for match in matches:
                # if matches, then:
                retval = retval + 1

                print(match)
                fixed_string = f'"{match[1]}={match[2]}" >> $GITHUB_OUTPUT'
                print(fixed_string)
                filetext = filetext.replace(match[0], fixed_string)

            # check if file has the save-state issue:
            matches = re.findall(
                r"(?m).+echo +([\"']::save-state +name=(\S+?)::(.+?)[\"'])(?:$| \|)",
                filetext,
            )

            # do replacement fix
            for match in matches:
                # if matches, then:
                retval = retval + 1

                print(match)
                fixed_string = f'"{match[1]}={match[2]}" >> $GITHUB_STATE'
                print(fixed_string)
                filetext = filetext.replace(match[0], fixed_string)

        # print(filetext)
        with open(filename, "w") as f:
            f.write(filetext)

    return retval


if __name__ == "__main__":
    print(__name__)
    exit_code = main()
    print("Matches:", exit_code)
    exit(exit_code)
