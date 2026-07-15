# pre-commit-jgstew

custom pre-commit hooks by JGStew.

This repository contains hooks for [pre-commit](https://pre-commit.com/hooks.html) that may be useful to devs.

## Requirements

To use these hooks, you first need to install pre-commit using the instructions here:
https://pre-commit.com/#install

## Adding hooks to your pre-commit config

For any hook in this repo you wish to use, add the following to your pre-commit config file `.pre-commit-config.yaml`:

```yaml
---
repos:
  - repo: https://github.com/jgstew/pre-commit-jgstew
    rev: v1.4.1
    hooks:
      - id: minimum-changes
```

After adding a hook to your pre-commit config, it's not a bad idea to run `pre-commit autoupdate` to ensure you have the latest version of the hooks.

## Available hooks

| id                             | description                                                                                                                                                                                                                                                                          |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `minimum-changes`              | Require a minimum number of changed lines against the git diff.                                                                                                                                                                                                                      |
| `validate-bes`                 | Validate BigFix BES XML files.                                                                                                                                                                                                                                                       |
| `validate-plist`               | Validate Apple plist files (`.recipe`, `.plist`).                                                                                                                                                                                                                                    |
| `verify-files-contain-entry`   | Require a file to contain a regex match/group found in a reference file.                                                                                                                                                                                                             |
| `verify-files-contain-pattern` | Require a file to contain a regex pattern.                                                                                                                                                                                                                                           |
| `verify-files-are-ascii`       | Require files to contain only ASCII.                                                                                                                                                                                                                                                 |
| `fix-files-to-ascii`           | Transliterate non-ASCII file contents to ASCII in place via `anyascii` (auto-fixing companion to `verify-files-are-ascii`).                                                                                                                                                          |
| `git-clean`                    | Delete untracked files by running `git clean -f`.                                                                                                                                                                                                                                    |
| `regex-search-filter-replace`  | Replace file contents based on a regex match and filter.                                                                                                                                                                                                                             |
| `revert-missing-change`        | Require a regex match in a file's git history (manual stage).                                                                                                                                                                                                                        |
| `github-action-set-output-fix` | Rewrite GitHub Action lines using the deprecated `set-output` / `save-state`.                                                                                                                                                                                                        |
| `add-missing-docstrings`       | Insert a boilerplate docstring into named functions when absent (default: `main`).                                                                                                                                                                                                   |
| `check-processor-conventions`  | Picky, opinionated checks + auto-fixes for AutoPkg processor `.py` files (boilerplate, docstrings, `input`/`output_variables`, `__main__` guard, naming, ...).                                                                                                                       |
| `check-recipe-conventions`     | Picky, opinionated cross-field checks + auto-fixes for AutoPkg recipe files (YAML or plist): `Identifier`<->`NAME`, `ParentRecipe` resolvability/cycles, duplicate `Identifier`/`Description`, filename<->identifier type, `MinimumVersion` floor, `Process`-step spacing, and more. |
| `check-bes-conventions`        | Picky, opinionated content checks + auto-fixes for BigFix BES files that go beyond the BES.xsd schema (`validate-bes`): `ActionScript` MIMEType allowlist, `SourceReleaseDate`/`x-fixlet-modification-time`/`DownloadSize`/`action-ui-metadata`/CPE-2.3 formats, prefetch-line shape, CDATA usage, blank-line spacing, description-placeholder, and Task/Fixlet release-date/modification-time presence.                        |

The AutoPkg convention hooks (`check-processor-conventions`, `check-recipe-conventions`) auto-fix the fixable issues in place and exit non-zero so the changes are reviewed and re-staged; `check-recipe-conventions` accepts `args: ["--strict"]` to also fail on remaining warnings. `check-bes-conventions` is the content-level companion to `validate-bes` (which only checks XSD validity); its `E`-codes fail the hook, it auto-fixes the fixable conventions (invalid `DownloadSize` -> 0, missing `SourceReleaseDate`/`x-fixlet-modification-time` -> the moment it ran, collapsed `ActionScript` blank lines), and it accepts `args: ["--strict"]` to fail on warnings too and to enable the CDATA-wrap auto-fix.

## Test commands:

- test python import and version: `python -c "import pre_commit_jgstew; print(pre_commit_jgstew.__version__)"`
- test version defined through [setup.py](setup.py): `python setup.py --version`
  - version defined in [setup.cfg](setup.cfg): `version = attr: pre_commit_jgstew.__version__`
- test builds:
  - `python ./setup.py build`
  - `python -m build`
- test pre-commit locally: `pre-commit try-repo .`

## creating a new hook to add to this repo:

create python file in folder [pre_commit_jgstew](pre_commit_jgstew) with name of hook with underscores.py

add entrypoint to the [setup.cfg](setup.cfg) file

add hook definition to the [.pre-commit-jgstew.yaml](.pre-commit-jgstew.yaml) file

add example hook to actually use in the [.pre-commit-config.yaml](.pre-commit-config.yaml) file

## Related:

- https://github.com/homebysix/pre-commit-macadmin
- https://github.com/jumanjihouse/pre-commit-jgstew
- https://github.com/Lucas-C/pre-commit-jgstew
- https://github.com/jumanjihouse/pre-commit-hook-yamlfmt
- https://pre-commit.com/hooks.html
- https://pre-commit.com/#new-hooks
