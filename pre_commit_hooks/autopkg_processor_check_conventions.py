#!/usr/bin/env python3
"""Pre-commit hook: check AutoPkg processors for boilerplate and conventions.

This is intentionally PICKY and OPINIONATED. It flags things that are not
strictly required by AutoPkg but are the conventions used throughout this repo,
so every processor looks and behaves consistently.

Usage:
    check_processor_conventions.py [--auto-fix=yes|no] SharedProcessors/Foo.py [...]

--auto-fix (default: yes) corrects the fixable conventions in place. Currently
auto-fixable: E001 (the shebang), E002 (a completely missing module-level
docstring), and E012+E013 together (when a class has no docstring but sets
`description = "<str>"`, the string is promoted to the class docstring and
`description` is set to `__doc__`). Auto-fixed files are rewritten and the hook
still exits non-zero so the changes can be reviewed and re-staged.

A file can opt out of all checks with a top-of-file comment containing:
    # pre-commit-skip: processor-conventions

Exit codes:
    0  all checked files conform and nothing was auto-fixed
    1  one or more violations found, or a file was auto-fixed
"""

import argparse
import ast
import os
import sys

SKIP_MARKER = "pre-commit-skip: processor-conventions"
EXPECTED_SHEBANG = "#!/usr/local/autopkg/python"

# Recognized AutoPkg base classes a processor may subclass (besides anything
# imported directly from autopkglib*, and the repo-local SharedUtilityMethods).
KNOWN_BASES = {
    "Processor",
    "URLGetter",
    "URLDownloader",
    "URLTextSearcher",
    "DmgMounter",
    "Copier",
    "Unarchiver",
    # repo-local base processors other processors subclass:
    "SharedUtilityMethods",
    "BESImport",
}


def base_names(classnode):
    """Return the names of a class's base classes (Name or Attribute bases)."""
    names = []
    for base in classnode.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def has_fstring_docstring(node):
    """True if the node's first statement is an f-string.

    An f-string is NOT a valid docstring: Python leaves __doc__ as None, which
    silently breaks the `description = __doc__` convention.
    """
    body = getattr(node, "body", None)
    return bool(
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.JoinedStr)
    )


def docstring_issue(node, lineno, check_id, what):
    """Return a docstring violation tuple, distinguishing f-string from missing."""
    if has_fstring_docstring(node):
        return (
            lineno,
            check_id,
            f"{what} docstring must be a plain string, not an f-string",
        )
    return (lineno, check_id, f"missing {what} docstring")


def dict_entries(node):
    """Yield (key_name, value_node) for string-keyed entries of an ast.Dict."""
    if not isinstance(node, ast.Dict):
        return
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            yield key.value, value


def dict_has_key(node, name):
    """True if ast.Dict literal has a string key `name`."""
    return any(k == name for k, _ in dict_entries(node))


def apply_shebang_fix(path):
    """Rewrite the file's first line to the expected shebang.

    Replaces an existing `#!...` line, or prepends one if the file has none;
    everything after the first line is preserved unchanged.
    """
    with open(path, encoding="utf-8") as handle:
        content = handle.read()
    if content.startswith("#!"):
        newline = content.find("\n")
        content = EXPECTED_SHEBANG + (content[newline:] if newline != -1 else "\n")
    else:
        content = EXPECTED_SHEBANG + "\n" + content
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def pick_processor_class(tree, stem):
    """Return the processor ClassDef, preferring the one named after the file.

    Falls back to the first class listed in `__all__`, then to the first class
    defined in the module. Returns None if the module defines no classes.
    """
    classes = {}
    all_names = None
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            classes[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        all_names = [
                            e.value
                            for e in node.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        ]
                    else:
                        all_names = []
    proc = classes.get(stem)
    if proc is None and all_names:
        proc = next((classes[n] for n in all_names if n in classes), None)
    if proc is None and classes:
        proc = next(iter(classes.values()))
    return proc


def apply_module_docstring_fix(path, classname):
    """Insert a module docstring after the file's header (shebang + comments).

    Writes `\"\"\"See docstring for <classname> class\"\"\"` as the module's first
    statement, leaving a blank line before the code that follows.
    """
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    # header = the contiguous run of shebang/comment lines at the very top
    i = 0
    while i < len(lines) and lines[i].startswith("#"):
        i += 1
    insert = [f'"""See docstring for {classname} class"""']
    # keep a blank line between the docstring and following code
    if i < len(lines) and lines[i].strip() != "":
        insert.append("")
    lines[i:i] = insert
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def description_string_assign(proc):
    """Return the class's `description = "<str>"` Assign node, if convertible.

    Only returns the node when `description` is the FIRST statement in the class
    body and is assigned a plain string literal -- the case where the string can
    be safely promoted to the class docstring. Returns None otherwise.
    """
    if not proc.body or not isinstance(proc.body[0], ast.Assign):
        return None
    assign = proc.body[0]
    is_description = any(
        isinstance(t, ast.Name) and t.id == "description" for t in assign.targets
    )
    if not is_description:
        return None
    if isinstance(assign.value, ast.Constant) and isinstance(assign.value.value, str):
        return assign
    return None


def apply_class_docstring_from_description_fix(path, assign):
    """Promote a `description = "<str>"` assignment to the class docstring.

    Rewrites the assignment's source line(s) as a triple-quoted docstring
    followed by `description = __doc__`, preserving indentation.
    """
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    start = assign.lineno - 1
    end = (assign.end_lineno or assign.lineno) - 1
    indent = " " * assign.col_offset
    text = assign.value.value
    replacement = [
        f'{indent}"""{text}"""',
        "",
        f"{indent}description = __doc__",
    ]
    lines[start : end + 1] = replacement
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def check_file(path, auto_fix=True):
    """Check one file.

    Returns (issues, fixed), each a list of (lineno, check_id, message). When
    auto_fix is True, fixable issues (E001) are corrected in place and reported
    under `fixed` instead of `issues`.
    """
    issues = []
    fixed = []
    stem = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8", errors="replace") as handle:
        src = handle.read()

    if SKIP_MARKER in src:
        return issues, fixed

    lines = src.splitlines()

    # --- E001: shebang (auto-fixable) ---
    if not lines or lines[0].rstrip() != EXPECTED_SHEBANG:
        if auto_fix:
            apply_shebang_fix(path)
            fixed.append((1, "E001", f"set first line to `{EXPECTED_SHEBANG}`"))
            with open(path, encoding="utf-8", errors="replace") as handle:
                src = handle.read()
            lines = src.splitlines()
        else:
            issues.append((1, "E001", f"first line should be `{EXPECTED_SHEBANG}`"))

    try:
        tree = ast.parse(src)
    except SyntaxError as err:
        return [(err.lineno or 1, "E000", f"syntax error: {err.msg}")], fixed

    # --- E002: module docstring (auto-fixable when completely missing) ---
    if not ast.get_docstring(tree):
        # only auto-fix a genuinely absent docstring; an f-string docstring is a
        # different problem (it needs a human to rewrite it as a plain string)
        if auto_fix and not has_fstring_docstring(tree):
            proc = pick_processor_class(tree, stem)
            classname = proc.name if proc else stem
            apply_module_docstring_fix(path, classname)
            fixed.append(
                (
                    1,
                    "E002",
                    f'set module docstring to """See docstring for {classname} class"""',
                )
            )
            with open(path, encoding="utf-8", errors="replace") as handle:
                src = handle.read()
            lines = src.splitlines()
            tree = ast.parse(src)
        else:
            issues.append(docstring_issue(tree, 1, "E002", "module-level"))

    # --- scan top-level nodes ---
    all_names = None
    imports = set()
    classes = {}
    has_main_guard = False
    calls_execute_shell = False

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        all_names = [
                            e.value
                            for e in node.value.elts
                            if isinstance(e, ast.Constant) and isinstance(e.value, str)
                        ]
                    else:
                        all_names = []
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.startswith("autopkglib")
        ):
            imports |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = node
        elif isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
            ):
                has_main_guard = True
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "execute_shell"
                    ):
                        calls_execute_shell = True

    # --- E003: __all__ ---
    if all_names is None:
        issues.append((1, "E003", "missing `__all__` declaration"))

    # --- E005: ProcessorError import (opinionated; convention even if unused) ---
    if "ProcessorError" not in imports:
        issues.append((1, "E005", "should import `ProcessorError` from autopkglib"))

    # --- E006 / E007: __main__ guard ---
    if not has_main_guard:
        issues.append((1, "E006", 'missing `if __name__ == "__main__":` block'))
    elif not calls_execute_shell:
        issues.append(
            (1, "E007", "`__main__` block should call `PROCESSOR.execute_shell()`")
        )

    # --- pick the processor class: prefer one named after the file ---
    proc = classes.get(stem)
    if proc is None and all_names:
        proc = next((classes[n] for n in all_names if n in classes), None)
    if proc is None and classes:
        proc = next(iter(classes.values()))

    if proc is None:
        issues.append((1, "E010", "no class found in this processor file"))
        return sorted(issues), fixed

    # --- E010 / E011: class naming ---
    if proc.name != stem:
        issues.append(
            (
                proc.lineno,
                "E010",
                f"class `{proc.name}` should be named `{stem}` to match the filename",
            )
        )
    if all_names is not None and proc.name not in all_names:
        issues.append(
            (proc.lineno, "E011", f"`{proc.name}` should be listed in `__all__`")
        )

    # --- E004: must subclass an AutoPkg processor base ---
    recognized = KNOWN_BASES | imports
    if not (set(base_names(proc)) & recognized):
        issues.append(
            (
                proc.lineno,
                "E004",
                f"`{proc.name}` should subclass an AutoPkg Processor base (e.g. Processor)",
            )
        )

    # --- collect class attributes/methods ---
    attrs = {}
    main_func = None
    for item in proc.body:
        if isinstance(item, ast.Assign):
            for target in item.targets:
                if isinstance(target, ast.Name):
                    attrs[target.id] = item.value
        elif (
            isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
            and item.name == "main"
        ):
            main_func = item

    # --- combined auto-fix for E012 + E013: when the class has no docstring but
    # sets `description = "<str>"`, promote that string to the class docstring
    # and set `description = __doc__`, then re-analyze the now-fixed file ---
    if auto_fix and not ast.get_docstring(proc):
        desc_assign = description_string_assign(proc)
        if desc_assign is not None:
            apply_class_docstring_from_description_fix(path, desc_assign)
            fixed.append(
                (
                    proc.lineno,
                    "E012",
                    f"promoted `description` string to the `{proc.name}` class docstring",
                )
            )
            fixed.append((desc_assign.lineno, "E013", "set `description = __doc__`"))
            more_issues, more_fixed = check_file(path, auto_fix=auto_fix)
            return more_issues, fixed + more_fixed

    # --- E012: class docstring ---
    if not ast.get_docstring(proc):
        issues.append(
            docstring_issue(proc, proc.lineno, "E012", f"class `{proc.name}`")
        )

    # --- E013: description = __doc__ (use the class docstring as the description) ---
    desc = attrs.get("description")
    if desc is None:
        issues.append((proc.lineno, "E013", "class should set `description = __doc__`"))
    elif not (isinstance(desc, ast.Name) and desc.id == "__doc__"):
        issues.append(
            (
                getattr(desc, "lineno", proc.lineno),
                "E013",
                "`description` should be `__doc__` (write the description as the class docstring)",
            )
        )

    # --- E014-E017: input_variables / output_variables ---
    for attr_name, miss_id, type_id in (
        ("input_variables", "E014", "E015"),
        ("output_variables", "E016", "E017"),
    ):
        if attr_name not in attrs:
            issues.append((proc.lineno, miss_id, f"class should define `{attr_name}`"))
        elif not isinstance(attrs[attr_name], ast.Dict):
            issues.append(
                (proc.lineno, type_id, f"`{attr_name}` should be a dict literal")
            )

    # --- E020 / E021: input_variables entries ---
    if isinstance(attrs.get("input_variables"), ast.Dict):
        for var_name, spec in dict_entries(attrs["input_variables"]):
            if not isinstance(spec, ast.Dict):
                continue
            if not dict_has_key(spec, "description"):
                issues.append(
                    (
                        spec.lineno,
                        "E020",
                        f"input_variable `{var_name}` missing `description`",
                    )
                )
            if not dict_has_key(spec, "required"):
                issues.append(
                    (
                        spec.lineno,
                        "E021",
                        f"input_variable `{var_name}` should declare `required`",
                    )
                )

    # --- E022: output_variables entries ---
    if isinstance(attrs.get("output_variables"), ast.Dict):
        for var_name, spec in dict_entries(attrs["output_variables"]):
            if isinstance(spec, ast.Dict) and not dict_has_key(spec, "description"):
                issues.append(
                    (
                        spec.lineno,
                        "E022",
                        f"output_variable `{var_name}` missing `description`",
                    )
                )

    # --- E018 / E019: main() method ---
    if main_func is None:
        issues.append(
            (
                proc.lineno,
                "E018",
                f"class `{proc.name}` should define a `main()` method",
            )
        )
    elif not ast.get_docstring(main_func):
        issues.append(docstring_issue(main_func, main_func.lineno, "E019", "`main()`"))

    return sorted(issues), fixed


def main(argv):
    """Execution starts here."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auto-fix",
        choices=["yes", "no"],
        default="yes",
        help="automatically fix fixable issues in place (default: yes)",
    )
    parser.add_argument("files", nargs="*", help="processor .py files to check")
    args = parser.parse_args(argv)
    auto_fix = args.auto_fix == "yes"

    paths = [f for f in args.files if f.endswith(".py")]
    issue_count = 0
    fix_count = 0
    for path in paths:
        issues, fixed = check_file(path, auto_fix=auto_fix)
        for lineno, check_id, message in fixed:
            fix_count += 1
            print(f"{path}:{lineno}: [{check_id}] auto-fixed: {message}")
        for lineno, check_id, message in issues:
            issue_count += 1
            print(f"{path}:{lineno}: [{check_id}] {message}")

    if fix_count:
        print(f"\nauto-fixed {fix_count} issue(s); review and re-stage the changes.")
    if issue_count:
        print(f"{issue_count} remaining processor-convention issue(s).")
    # non-zero if anything was fixed (so the user re-stages) or any issue remains
    return 1 if (issue_count or fix_count) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
