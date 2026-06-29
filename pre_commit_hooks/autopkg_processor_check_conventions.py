#!/usr/bin/env python3
"""Pre-commit hook: check AutoPkg processors for boilerplate and conventions.

This is intentionally PICKY and OPINIONATED. It flags things that are not
strictly required by AutoPkg but are the conventions used throughout this repo,
so every processor looks and behaves consistently.

Usage:
    check_processor_conventions.py [--auto-fix=yes|no] SharedProcessors/Foo.py [...]

--auto-fix (default: yes) corrects the fixable conventions in place. Currently
auto-fixable: E001 (the shebang), E002 (a completely missing module-level
docstring), E012+E013 together (when a class has no docstring but sets
`description = "<str>"`, the string is promoted to the class docstring and
`description` is set to `__doc__`), and E023 (a redundant `__doc__ = description`
left over once `description = __doc__` is set). Auto-fixed files are rewritten
and the hook still exits non-zero so the changes can be reviewed and re-staged.

A file can opt out of all checks with a top-of-file comment containing:
    # pre-commit-skip: processor-conventions

Exit codes:
    0  all checked files conform and nothing was auto-fixed
    1  one or more violations found, or a file was auto-fixed
"""

import argparse
import ast
import collections
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


# The top-level facts the module-level checks operate on, gathered in one pass.
ModuleInfo = collections.namedtuple(
    "ModuleInfo",
    ["all_names", "imports", "classes", "has_main_guard", "calls_execute_shell"],
)


def scan_module(tree):
    """Walk the module's top-level nodes once and collect the facts checks need.

    Returns a ModuleInfo with:
      all_names           list from `__all__`, or None if it is not declared
      imports             names imported from any `autopkglib*` module
      classes             {name: ClassDef} for every top-level class
      has_main_guard      True if an `if __name__ == "__main__":` block exists
      calls_execute_shell True if that block calls `.execute_shell()`
    """
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

    return ModuleInfo(all_names, imports, classes, has_main_guard, calls_execute_shell)


def pick_processor_class(info, stem):
    """Return the processor ClassDef, preferring the one named after the file.

    Falls back to the first class listed in `__all__`, then to the first class
    defined in the module. Returns None if the module defines no classes.
    """
    proc = info.classes.get(stem)
    if proc is None and info.all_names:
        proc = next(
            (info.classes[n] for n in info.all_names if n in info.classes), None
        )
    if proc is None and info.classes:
        proc = next(iter(info.classes.values()))
    return proc


def class_members(proc):
    """Return (attrs, main_func) for a class.

    attrs maps each simple-name class attribute to its assigned value node;
    main_func is the `main` method's FunctionDef (or None).
    """
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
    return attrs, main_func


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


def redundant_doc_assign(proc):
    """Return a redundant `__doc__ = description` Assign node, if present.

    It is redundant only when the class also sets `description = __doc__` (our
    convention): then `__doc__ = description` reduces to `__doc__ = __doc__`, a
    no-op. Returns None when either statement is missing -- e.g. a class that
    sets `description` to a plain string still needs `__doc__ = description` to
    populate its docstring, so that case is left alone.
    """
    has_description_from_doc = False
    doc_assign = None
    for item in proc.body:
        if not isinstance(item, ast.Assign):
            continue
        targets = [t.id for t in item.targets if isinstance(t, ast.Name)]
        value_name = item.value.id if isinstance(item.value, ast.Name) else None
        if "description" in targets and value_name == "__doc__":
            has_description_from_doc = True
        elif "__doc__" in targets and value_name == "description":
            doc_assign = item
    return doc_assign if (has_description_from_doc and doc_assign) else None


def apply_remove_doc_assign_fix(path, assign):
    """Delete a redundant `__doc__ = description` assignment's source line(s).

    Also collapses a doubled blank line left where the statement used to be, so
    the class body keeps a single blank separator.
    """
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    start = assign.lineno - 1
    end = (assign.end_lineno or assign.lineno) - 1
    del lines[start : end + 1]
    if (
        0 < start < len(lines)
        and not lines[start - 1].strip()
        and not lines[start].strip()
    ):
        del lines[start]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


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


# --- individual checks -------------------------------------------------------
# Each returns a (possibly empty) list of (lineno, check_id, message) tuples and
# never mutates anything, so they can be read, tested, and reordered in
# isolation. check_file just calls them in order and concatenates the results.


def check_module_docstring(tree):
    """E002: the module must have a plain-string docstring."""
    if not ast.get_docstring(tree):
        return [docstring_issue(tree, 1, "E002", "module-level")]
    return []


def check_all_declared(info):
    """E003: the module must declare `__all__`."""
    if info.all_names is None:
        return [(1, "E003", "missing `__all__` declaration")]
    return []


def check_processor_error_import(info):
    """E005: `ProcessorError` should be imported from autopkglib (convention)."""
    if "ProcessorError" not in info.imports:
        return [(1, "E005", "should import `ProcessorError` from autopkglib")]
    return []


def check_main_guard(info):
    """E006/E007: a `__main__` guard must exist and call `.execute_shell()`."""
    if not info.has_main_guard:
        return [(1, "E006", 'missing `if __name__ == "__main__":` block')]
    if not info.calls_execute_shell:
        return [(1, "E007", "`__main__` block should call `PROCESSOR.execute_shell()`")]
    return []


def check_class_naming(proc, stem, info):
    """E010/E011: class name should match the filename and be listed in `__all__`."""
    issues = []
    if proc.name != stem:
        issues.append(
            (
                proc.lineno,
                "E010",
                f"class `{proc.name}` should be named `{stem}` to match the filename",
            )
        )
    if info.all_names is not None and proc.name not in info.all_names:
        issues.append(
            (proc.lineno, "E011", f"`{proc.name}` should be listed in `__all__`")
        )
    return issues


def check_base_class(proc, info):
    """E004: the class must subclass a recognized AutoPkg Processor base."""
    recognized = KNOWN_BASES | info.imports
    if not (set(base_names(proc)) & recognized):
        return [
            (
                proc.lineno,
                "E004",
                f"`{proc.name}` should subclass an AutoPkg Processor base (e.g. Processor)",
            )
        ]
    return []


def check_class_docstring(proc):
    """E012: the class must have a plain-string docstring."""
    if not ast.get_docstring(proc):
        return [docstring_issue(proc, proc.lineno, "E012", f"class `{proc.name}`")]
    return []


def check_description(proc, attrs):
    """E013: `description` should be `__doc__` (the class docstring is the source)."""
    desc = attrs.get("description")
    if desc is None:
        return [(proc.lineno, "E013", "class should set `description = __doc__`")]
    if not (isinstance(desc, ast.Name) and desc.id == "__doc__"):
        return [
            (
                getattr(desc, "lineno", proc.lineno),
                "E013",
                "`description` should be `__doc__` (write the description as the class docstring)",
            )
        ]
    return []


def check_variable_attrs(proc, attrs):
    """E014-E017: input_variables/output_variables must exist as dict literals."""
    issues = []
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
    return issues


def check_input_variable_entries(attrs):
    """E020/E021: each input_variable entry needs `description` and `required`."""
    issues = []
    node = attrs.get("input_variables")
    if not isinstance(node, ast.Dict):
        return issues
    for var_name, spec in dict_entries(node):
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
    return issues


def check_output_variable_entries(attrs):
    """E022: each output_variable entry needs a `description`."""
    issues = []
    node = attrs.get("output_variables")
    if not isinstance(node, ast.Dict):
        return issues
    for var_name, spec in dict_entries(node):
        if isinstance(spec, ast.Dict) and not dict_has_key(spec, "description"):
            issues.append(
                (
                    spec.lineno,
                    "E022",
                    f"output_variable `{var_name}` missing `description`",
                )
            )
    return issues


def check_main_method(proc, main_func):
    """E018/E019: the class must define `main()` and it must have a docstring."""
    if main_func is None:
        return [
            (
                proc.lineno,
                "E018",
                f"class `{proc.name}` should define a `main()` method",
            )
        ]
    if not ast.get_docstring(main_func):
        return [docstring_issue(main_func, main_func.lineno, "E019", "`main()`")]
    return []


def check_redundant_doc_assign(proc):
    """E023: drop `__doc__ = description` when `description = __doc__` is set."""
    assign = redundant_doc_assign(proc)
    if assign is not None:
        return [
            (
                assign.lineno,
                "E023",
                "redundant `__doc__ = description` (already set via `description = __doc__`)",
            )
        ]
    return []


# --- multi-step auto-fixes ---------------------------------------------------
# These mutate the file on disk, so they are kept apart from the pure checks.


def maybe_fix_module_docstring(path, tree, stem):
    """Auto-fix a completely missing module docstring (E002).

    Returns (fixed_entry, new_src), or (None, None) when no fix applies. An
    f-string "docstring" is intentionally left for a human to rewrite.
    """
    if ast.get_docstring(tree) or has_fstring_docstring(tree):
        return None, None
    proc = pick_processor_class(scan_module(tree), stem)
    classname = proc.name if proc else stem
    apply_module_docstring_fix(path, classname)
    fixed_entry = (
        1,
        "E002",
        f'set module docstring to """See docstring for {classname} class"""',
    )
    with open(path, encoding="utf-8", errors="replace") as handle:
        return fixed_entry, handle.read()


def maybe_fix_class_docstring(path, proc):
    """Auto-fix E012+E013 together when convertible.

    When the class has no docstring but sets `description = "<str>"`, promote the
    string to the class docstring and set `description = __doc__`. Returns the
    list of fixed entries (empty if nothing was fixed).
    """
    if ast.get_docstring(proc):
        return []
    desc_assign = description_string_assign(proc)
    if desc_assign is None:
        return []
    apply_class_docstring_from_description_fix(path, desc_assign)
    return [
        (
            proc.lineno,
            "E012",
            f"promoted `description` string to the `{proc.name}` class docstring",
        ),
        (desc_assign.lineno, "E013", "set `description = __doc__`"),
    ]


def maybe_fix_redundant_doc_assign(path, proc):
    """Auto-fix E023: remove a redundant `__doc__ = description` assignment.

    Returns the list of fixed entries (empty if nothing was fixed).
    """
    assign = redundant_doc_assign(proc)
    if assign is None:
        return []
    apply_remove_doc_assign_fix(path, assign)
    return [(assign.lineno, "E023", "removed redundant `__doc__ = description`")]


def check_file(path, auto_fix=True):
    """Check one file.

    Returns (issues, fixed), each a list of (lineno, check_id, message). When
    auto_fix is True, fixable issues (E001, E002, and E012+E013 together) are
    corrected in place and reported under `fixed` instead of `issues`.

    The body is just orchestration: run/apply the auto-fixes (which mutate the
    file and re-parse), then concatenate the results of the pure check_* helpers.
    """
    fixed = []
    stem = os.path.splitext(os.path.basename(path))[0]
    with open(path, encoding="utf-8", errors="replace") as handle:
        src = handle.read()

    if SKIP_MARKER in src:
        return [], fixed

    issues = []

    # --- E001: shebang (auto-fixable) ---
    lines = src.splitlines()
    if not lines or lines[0].rstrip() != EXPECTED_SHEBANG:
        if auto_fix:
            apply_shebang_fix(path)
            fixed.append((1, "E001", f"set first line to `{EXPECTED_SHEBANG}`"))
            with open(path, encoding="utf-8", errors="replace") as handle:
                src = handle.read()
        else:
            issues.append((1, "E001", f"first line should be `{EXPECTED_SHEBANG}`"))

    try:
        tree = ast.parse(src)
    except SyntaxError as err:
        return [(err.lineno or 1, "E000", f"syntax error: {err.msg}")], fixed

    # --- E002: module docstring (auto-fixable when completely missing) ---
    if auto_fix:
        fixed_entry, new_src = maybe_fix_module_docstring(path, tree, stem)
        if fixed_entry:
            fixed.append(fixed_entry)
            tree = ast.parse(new_src)
    issues += check_module_docstring(tree)

    # --- module-level checks ---
    info = scan_module(tree)
    issues += check_all_declared(info)  # E003
    issues += check_processor_error_import(info)  # E005
    issues += check_main_guard(info)  # E006 / E007

    proc = pick_processor_class(info, stem)
    if proc is None:
        issues.append((1, "E010", "no class found in this processor file"))
        return sorted(issues), fixed

    # --- class-level auto-fixes; the first one that applies re-analyzes the
    # now-fixed file, which lets fixes chain (e.g. E012/E013 then E023) ---
    if auto_fix:
        for fixer in (maybe_fix_class_docstring, maybe_fix_redundant_doc_assign):
            class_fixed = fixer(path, proc)
            if class_fixed:
                fixed += class_fixed
                more_issues, more_fixed = check_file(path, auto_fix=auto_fix)
                return more_issues, fixed + more_fixed

    # --- class-level checks ---
    attrs, main_func = class_members(proc)
    issues += check_class_naming(proc, stem, info)  # E010 / E011
    issues += check_base_class(proc, info)  # E004
    issues += check_class_docstring(proc)  # E012
    issues += check_description(proc, attrs)  # E013
    issues += check_variable_attrs(proc, attrs)  # E014-E017
    issues += check_input_variable_entries(attrs)  # E020 / E021
    issues += check_output_variable_entries(attrs)  # E022
    issues += check_main_method(proc, main_func)  # E018 / E019
    issues += check_redundant_doc_assign(proc)  # E023

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
