# pre-commit-hooks

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
    rev: v1.2.2
    hooks:
      - id: minimum-changes
```

After adding a hook to your pre-commit config, it's not a bad idea to run `pre-commit autoupdate` to ensure you have the latest version of the hooks.

## Test commands:

- test python import and version: `python -c "import pre_commit_hooks; print(pre_commit_hooks.__version__)"`
- test version defined through [setup.py](setup.py): `python setup.py --version`
  - version defined in [setup.cfg](setup.cfg): `version = attr: pre_commit_hooks.__version__`
- test builds:
  - `python ./setup.py build`
  - `python -m build`
- test pre-commit locally: `pre-commit try-repo .`

## creating a new hook to add to this repo:

create python file in folder [pre_commit_hooks](pre_commit_hooks) with name of hook with underscores.py

add entrypoint to the [setup.cfg](setup.cfg) file

add hook definition to the [.pre-commit-hooks.yaml](.pre-commit-hooks.yaml) file

add example hook to actually use in the [.pre-commit-config.yaml](.pre-commit-config.yaml) file

## Related:

- https://github.com/homebysix/pre-commit-macadmin
- https://github.com/jumanjihouse/pre-commit-hooks
- https://github.com/Lucas-C/pre-commit-hooks
- https://github.com/jumanjihouse/pre-commit-hook-yamlfmt
- https://pre-commit.com/hooks.html
- https://pre-commit.com/#new-hooks
