# pre-commit-hooks
custom pre-commit hooks

Work in progress - intend to add custom pre-commit hooks here in the future.
- use `git diff --stat` to check for files with too many or too few changes?

## Requirements

To use these hooks, you first need to install pre-commit using the instructions here:
https://pre-commit.com/#install

## Test commands:

- test python import and version: `python -c "import pre_commit_hooks; print(pre_commit_hooks.__version__)"`
- test version defined through setup.py: `python setup.py --version`
  - version defined in setup.cfg: `version = attr: pre_commit_hooks.__version__`
- test builds:
  - `python .\setup.py build`
  - `python -m build`
- test pre-commit locally: `pre-commit try-repo .`

## Related:

- https://github.com/homebysix/pre-commit-macadmin
- https://github.com/jumanjihouse/pre-commit-hooks
- https://github.com/Lucas-C/pre-commit-hooks
- https://github.com/jumanjihouse/pre-commit-hook-yamlfmt
- https://pre-commit.com/hooks.html
- https://pre-commit.com/#new-hooks
