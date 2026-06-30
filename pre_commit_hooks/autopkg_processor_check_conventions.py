#!/usr/bin/env python3
"""Pre-commit hook: check AutoPkg processors for boilerplate and conventions.

This is intentionally PICKY and OPINIONATED. It flags things that are not
strictly required by AutoPkg but are the conventions used throughout this repo,
so every processor looks and behaves consistently.

Usage:
    check_processor_conventions.py [--auto-fix=yes|no] [--disable E005,E020]
        SharedProcessors/Foo.py [...]

--disable takes a comma-separated list of check IDs to skip entirely. A disabled
fixable check is neither auto-fixed nor reported.

--auto-fix (default: yes) corrects the fixable conventions in place. Currently
auto-fixable: E001 (the shebang), E002 (a completely missing module-level
docstring), E003 (a missing `__all__`, added after the imports as
`__all__ = ["<Class>"]` -- only when the file has a class), E010 (a file with no
class at all gets a minimal processor class stub named after the file, which then
chains E003/E006/etc.), E006/E007 (the `__main__` guard -- added when missing,
rewritten to
the canonical `PROCESSOR = <Class>()` / `PROCESSOR.execute_shell()` form when
not), E012+E013 together (when a class has no docstring but sets
`description = "<str>"`, the string is promoted to the class docstring and
`description` is set to `__doc__`), E023 (a redundant `__doc__ = description`
left over once `description = __doc__` is set), E025 (a missing author/created
header comment is added after the shebang as `# Created <year> by <author>`,
using the file's original git author/year, or the current git user/year for a
new file), E028 (a simple `print(x)` inside a processor method is rewritten to
`self.output(x, 3)`), and E030 (an undeclared
env key is added to input_variables with a blank description -- which then trips
E020 so a human fills it in). Auto-fixed files are rewritten and the hook still
exits non-zero so the changes can be reviewed and re-staged.

Non-fixable opinionated checks include: E020 (every input_variable needs a
non-empty `description`), E024 (the class docstring is the processor's primary
documentation -- a trivial one-liner or generated stub is rejected so it gets
expanded and maintained), and E029 (every declared output_variable must be set via
self.env, unless it is also an input_variable -- declaring an input as an output
too is a deliberate way to have AutoPkg re-display it in verbose runs). E030
allows AutoPkg built-ins, ALL_CAPS external config/credential keys (e.g.
BES_PASSWORD), keys the processor writes itself, and get() fallback defaults.
W003 warns when the `__main__` guard is not the last statement in the file. W004
warns when the processor writes a `self.env[...]` key that is not declared in
output_variables (exempting keys it also reads, ALL_CAPS/built-in control vars,
and any write carrying the `# output-undeclared-ok` marker). A `print(...)` call
that E028 cannot safely rewrite (multiple args, file=/sep=/end= kwargs, or a
staticmethod) stays reported for a human to fix.

Only AutoPkg processors are validated. A .py file that does not import
autopkglib (`import autopkglib` or `from autopkglib import ...`) is not a
processor, so it is skipped with a non-failing W001 warning rather than flagged
with conventions that do not apply to it.

A file can also opt out of all checks explicitly with a top-of-file comment:
    # pre-commit-skip: processor-conventions

Exit codes:
    0  all checked files conform (warnings alone do not fail) and nothing fixed
    1  one or more violations found, or a file was auto-fixed
"""

import argparse
import ast
import collections
import datetime
import os
import subprocess
import sys

SKIP_MARKER = "pre-commit-skip: processor-conventions"
EXPECTED_SHEBANG = "#!/usr/local/autopkg/python"

# Inline marker (a trailing comment) that exempts a single `self.env[...] = ...`
# write from W004 -- for values intentionally not declared as outputs, e.g. very
# large strings (file_base64, content_string).
OUTPUT_UNDECLARED_MARKER = "output-undeclared-ok"

# A class docstring is the processor's primary documentation (AutoPkg surfaces it
# as the processor description), so a trivial one-liner is rejected by E024. This
# is the minimum stripped length; the shortest real docstring in the repo is ~65
# characters, so 40 cleanly separates genuine descriptions from stub placeholders.
MIN_CLASS_DOCSTRING_LEN = 40

# Every check ID this tool can emit -- used to validate --disable arguments so a
# typo (e.g. "E99") is reported rather than silently ignored.
KNOWN_CODES = frozenset(
    [
        "E000",  # syntax error
        "E001",  # shebang
        "E002",  # module docstring
        "E003",  # __all__
        "E004",  # subclass an AutoPkg base
        "E005",  # import ProcessorError
        "E006",  # __main__ guard
        "E007",  # __main__ calls execute_shell()
        "E010",  # class name matches filename / no class found
        "E011",  # class listed in __all__
        "E012",  # class docstring
        "E013",  # description = __doc__
        "E014",  # input_variables defined
        "E015",  # input_variables is a dict literal
        "E016",  # output_variables defined
        "E017",  # output_variables is a dict literal
        "E018",  # main() defined
        "E019",  # main() docstring
        "E020",  # input_variable description
        "E021",  # input_variable required
        "E022",  # output_variable description
        "E023",  # redundant __doc__ = description
        "E024",  # class docstring too brief (insufficient documentation)
        "E025",  # missing author/created header comment after the shebang
        "E028",  # print() used instead of self.output()
        "E029",  # output_variable declared but never set
        "E030",  # reads an undeclared env key
        "W001",  # not an AutoPkg processor
        "W002",  # file not found
        "W003",  # __main__ guard not at end of file
        "W004",  # writes an env key not declared in output_variables
    ]
)

# Env keys a processor may read without declaring them in input_variables:
# AutoPkg core variables plus the ubiquitous URLDownloader download-chain keys
# that flow between processors. Reads of anything else should be declared.
AUTOPKG_BUILTINS = frozenset(
    [
        "RECIPE_DIR",
        "RECIPE_CACHE_DIR",
        "RECIPE_PATH",
        "PARENT_RECIPE",
        "PARENT_RECIPES",
        "RECIPE_OVERRIDE_DIRS",
        "RECIPE_SEARCH_DIRS",
        "AUTOPKG_VERSION",
        "CACHE_DIR",
        "verbose",
        "NAME",
        "version",
        "pathname",
        "PKG",
        "pkg_path",
        "dmg_path",
        "url",
        "download_changed",
        "last_modified",
        "etag",
        "download_info",
        "stop_processing_recipe",
        "MUNKI_REPO",
        "munki_repo",
        "pkg_repo_dir",
    ]
)

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


def dict_get_value(node, name):
    """Return the value node for string key `name` in an ast.Dict, or None."""
    for key, value in dict_entries(node):
        if key == name:
            return value
    return None


def is_blank_string(node):
    """True if `node` is a string constant that is empty or only whitespace."""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and not node.value.strip()
    )


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


def module_docstring_lineno(tree):
    """Return the 1-based line of the module docstring, or None if absent."""
    body = getattr(tree, "body", None)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[0].lineno
    return None


def has_header_comment(lines, doc_lineno):
    """True if a comment line sits between the shebang and the module docstring."""
    return any(line.lstrip().startswith("#") for line in lines[1 : doc_lineno - 1])


def git_config(key):
    """Return `git config <key>`, or "" if unavailable."""
    try:
        result = subprocess.run(
            ["git", "config", key],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def git_config_user_name():
    """Return `git config user.name`, or "" if unavailable."""
    return git_config("user.name")


def git_created_by(path):
    """Return (author, year) for `path`'s creation.

    Uses the original commit that added the file (author name + author-date year).
    If that commit's author email matches the current git config email but the
    name differs, the current config name is used instead -- i.e. the same person
    committing under a different name spelling (jgstew vs JGStew) is normalized to
    their current canonical name, while the original creation year is kept.

    Falls back to the current git user and current year for a new or untracked
    file that has no creating commit yet. (`--follow` is intentionally omitted: it
    is incompatible with `--reverse --diff-filter=A` and yields no output.)
    """
    try:
        result = subprocess.run(
            [
                "git",
                "log",
                "--reverse",
                "--diff-filter=A",
                "--format=%an%x09%ae%x09%ad",
                "--date=format:%Y",
                "--",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first = next(
            (ln for ln in result.stdout.splitlines() if ln.count("\t") >= 2), ""
        )
        if first:
            name, email, year = (part.strip() for part in first.split("\t", 2))
            if name and year:
                current_email = git_config("user.email")
                current_name = git_config_user_name()
                same_person = (
                    current_email and email and email.lower() == current_email.lower()
                )
                if same_person and current_name and name != current_name:
                    name = current_name
                return name, year
    except (OSError, subprocess.SubprocessError):
        pass
    return (git_config_user_name() or "Unknown", str(datetime.date.today().year))


def apply_header_comment_fix(path, author, year):
    """Insert `# Created by <author> <year>` directly after the shebang line."""
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    lines[1:1] = [f"# Created {year} by {author}"]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def imports_autopkglib(tree):
    """True if the module imports autopkglib -- the marker of an AutoPkg processor.

    Matches `import autopkglib`, `import autopkglib.something`, and
    `from autopkglib[.sub] import ...`, anywhere in the file (including inside a
    try/except) so guarded imports still count. This is the essential property
    every processor must have; files without it are not processors.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "autopkglib" or alias.name.startswith("autopkglib."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "autopkglib" or module.startswith("autopkglib."):
                return True
    return False


# The top-level facts the module-level checks operate on, gathered in one pass.
ModuleInfo = collections.namedtuple(
    "ModuleInfo",
    ["all_names", "imports", "classes", "main_guard", "guard_is_last"],
)


def scan_module(tree):
    """Walk the module's top-level nodes once and collect the facts checks need.

    Returns a ModuleInfo with:
      all_names     list from `__all__`, or None if it is not declared
      imports       names imported from any `autopkglib*` module
      classes       {name: ClassDef} for every top-level class
      main_guard    the `if __name__ == "__main__":` ast.If node, or None
      guard_is_last True if that guard is the last top-level statement
    """
    all_names = None
    imports = set()
    classes = {}
    main_guard = None

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
                main_guard = node

    guard_is_last = bool(tree.body) and tree.body[-1] is main_guard
    return ModuleInfo(all_names, imports, classes, main_guard, guard_is_last)


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


def apply_add_input_vars_fix(path, base_indent, dict_node, keys):
    """Add `keys` to an input_variables dict, each with a blank description.

    Each entry is `"<key>": {"required": False, "description": ""}`. The blank
    description is intentional: it is reported by E020 on the next run so a human
    fills it in. New entries are inserted right after the opening `{` (so no
    dependency on the existing last entry's trailing comma); an empty `{}` dict is
    expanded to a multi-line one. Returns True if applied, False if the dict shape
    is one this fixer will not touch (e.g. a single-line non-empty dict).
    """
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    entry_indent = " " * (base_indent + 4)
    val_indent = " " * (base_indent + 8)
    block = []
    for key in keys:
        block += [
            f'{entry_indent}"{key}": {{',
            f'{val_indent}"required": False,',
            f'{val_indent}"description": "",',
            f"{entry_indent}}},",
        ]

    lines = src.split("\n")
    if dict_node.keys:
        open_idx = dict_node.lineno - 1
        if not lines[open_idx].rstrip().endswith("{"):
            return False  # single-line non-empty dict; leave it for a human
        lines[open_idx + 1 : open_idx + 1] = block
        new_src = "\n".join(lines)
    else:
        line_starts = [0]
        for line in src.splitlines(keepends=True):
            line_starts.append(line_starts[-1] + len(line))
        open_off = line_starts[dict_node.lineno - 1] + dict_node.col_offset
        close_off = line_starts[dict_node.end_lineno - 1] + dict_node.end_col_offset
        replacement = "{\n" + "\n".join(block) + "\n" + " " * base_indent + "}"
        new_src = src[:open_off] + replacement + src[close_off:]

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(new_src)
    return True


def convertible_print_calls(proc):
    """Return print() Call nodes that can be safely rewritten to self.output().

    Restricted to calls with exactly one positional argument (not a `*splat`)
    and no keywords, located inside an instance method (first parameter `self`)
    so `self.output` is in scope. Other print() calls remain E028 errors for a
    human to handle (multiple args, file=/sep=/end= kwargs, staticmethods, ...).
    """
    calls = []
    for item in proc.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not (item.args.args and item.args.args[0].arg == "self"):
            continue
        for node in ast.walk(item):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "print"
                and len(node.args) == 1
                and not node.keywords
                and not isinstance(node.args[0], ast.Starred)
            ):
                calls.append(node)
    return calls


def apply_print_to_output_fix(path, calls):
    """Rewrite each `print(arg)` to `self.output(arg, 3)`, preserving arg source.

    Verbosity 3 keeps the message quiet unless AutoPkg is run with -vvv. Edits
    are spliced by absolute source offset, back-to-front, so multi-line calls
    and repeated text are handled correctly.
    """
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    line_starts = [0]
    for line in src.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))

    def offset(lineno, col):
        return line_starts[lineno - 1] + col

    spans = []
    for node in calls:
        call_start = offset(node.lineno, node.col_offset)
        call_end = offset(node.end_lineno, node.end_col_offset)
        arg = node.args[0]
        arg_src = src[
            offset(arg.lineno, arg.col_offset) : offset(
                arg.end_lineno, arg.end_col_offset
            )
        ]
        spans.append((call_start, call_end, arg_src))
    for call_start, call_end, arg_src in sorted(spans, reverse=True):
        src = src[:call_start] + f"self.output({arg_src}, 3)" + src[call_end:]

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(src)


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


def canonical_guard_lines(classname):
    """The exact source lines a `__main__` guard must have for `classname`."""
    return [
        'if __name__ == "__main__":',
        f"    PROCESSOR = {classname}()",
        "    PROCESSOR.execute_shell()",
    ]


def guard_is_canonical(guard, classname):
    """True if the guard body is exactly the canonical two statements.

    Canonical form: `PROCESSOR = <classname>()` then `PROCESSOR.execute_shell()`.
    """
    body = guard.body
    if len(body) != 2:
        return False
    assign, call = body
    ok_assign = (
        isinstance(assign, ast.Assign)
        and len(assign.targets) == 1
        and isinstance(assign.targets[0], ast.Name)
        and assign.targets[0].id == "PROCESSOR"
        and isinstance(assign.value, ast.Call)
        and isinstance(assign.value.func, ast.Name)
        and assign.value.func.id == classname
        and not assign.value.args
        and not assign.value.keywords
    )
    ok_call = (
        isinstance(call, ast.Expr)
        and isinstance(call.value, ast.Call)
        and isinstance(call.value.func, ast.Attribute)
        and call.value.func.attr == "execute_shell"
        and isinstance(call.value.func.value, ast.Name)
        and call.value.func.value.id == "PROCESSOR"
        and not call.value.args
    )
    return ok_assign and ok_call


def check_main_guard(proc, info):
    """E006/E007/W003: enforce the canonical `__main__` guard at the file end."""
    guard = info.main_guard
    if guard is None:
        return [(1, "E006", 'missing `if __name__ == "__main__":` block')]
    issues = []
    if not guard_is_canonical(guard, proc.name):
        issues.append(
            (
                guard.lineno,
                "E007",
                f"`__main__` block must be exactly "
                f"`PROCESSOR = {proc.name}()` then `PROCESSOR.execute_shell()`",
            )
        )
    if not info.guard_is_last:
        issues.append(
            (
                guard.lineno,
                "W003",
                "`__main__` guard should be the last statement in the file",
            )
        )
    return issues


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


def check_class_docstring_sufficient(proc):
    """E024: the class docstring must actually document the processor.

    The class docstring is a processor's primary documentation -- AutoPkg shows
    it as the processor description (via `description = __doc__`). A trivial
    one-liner or the generated `"<Class> processor."` stub is rejected so it gets
    expanded and kept current. (E012 handles a missing docstring.)
    """
    doc = ast.get_docstring(proc)
    if doc is None:
        return []
    stripped = doc.strip()
    is_stub = stripped.rstrip(".").strip().lower() == f"{proc.name.lower()} processor"
    if len(stripped) < MIN_CLASS_DOCSTRING_LEN or is_stub:
        return [
            (
                proc.lineno,
                "E024",
                f"class `{proc.name}` docstring is too brief; it is this "
                "processor's primary documentation (AutoPkg shows it as the "
                "description) -- expand it to describe what the processor does, "
                "its input_variables, and its output_variables",
            )
        ]
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
        desc = dict_get_value(spec, "description")
        if desc is None:
            issues.append(
                (
                    spec.lineno,
                    "E020",
                    f"input_variable `{var_name}` missing `description`",
                )
            )
        elif is_blank_string(desc):
            issues.append(
                (
                    desc.lineno,
                    "E020",
                    f"input_variable `{var_name}` has an empty `description`",
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


def check_no_print(proc):
    """E028: processors must log via `self.output(...)`, never `print(...)`."""
    issues = []
    for node in ast.walk(proc):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            issues.append(
                (
                    node.lineno,
                    "E028",
                    "use `self.output(...)` instead of `print(...)` in a processor",
                )
            )
    return issues


def _is_self_env(node):
    """True if `node` is the `self.env` attribute access."""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "env"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def class_env_usage(proc):
    """Inspect every `self.env` access in the class.

    Returns (writes_static, writes_dynamic, reads):
      writes_static   set of string keys assigned (`self.env["k"] = ...`)
      writes_dynamic  True if any write uses a non-constant key, or .update()/
                      .setdefault() is called (so keys cannot be enumerated)
      reads           list of (key, lineno) for constant-key reads
                      (`self.env["k"]` in load context or `self.env.get("k")`)

    Reads nested inside the default argument(s) of a `self.env.get(...)` call are
    excluded -- in `self.env.get("primary", self.env.get("fallback"))` only the
    primary key is a real input; the fallback is just a default value.
    """
    # Identify env reads that are fallback defaults of a self.env.get() call, so
    # they can be skipped: everything inside any get()'s args[1:] subtree.
    fallback_node_ids = set()
    for node in ast.walk(proc):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_self_env(node.func.value)
        ):
            for default in node.args[1:]:
                fallback_node_ids.update(id(sub) for sub in ast.walk(default))

    writes_static = set()
    writes_dynamic = False
    reads = []
    for node in ast.walk(proc):
        if isinstance(node, ast.Subscript) and _is_self_env(node.value):
            is_const = isinstance(node.slice, ast.Constant) and isinstance(
                node.slice.value, str
            )
            if isinstance(node.ctx, ast.Store):
                if is_const:
                    writes_static.add(node.slice.value)
                else:
                    writes_dynamic = True
            elif is_const and id(node) not in fallback_node_ids:
                reads.append((node.slice.value, node.lineno))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _is_self_env(node.func.value)
        ):
            if node.func.attr in ("update", "setdefault"):
                writes_dynamic = True
            elif (
                node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and id(node) not in fallback_node_ids
            ):
                reads.append((node.args[0].value, node.lineno))
    return writes_static, writes_dynamic, reads


def check_outputs_assigned(proc, attrs):
    """E029: every declared output_variable must actually be set via self.env.

    Suppressed entirely when the class writes self.env with a dynamic key (or
    .update()/.setdefault()), since the set of keys then cannot be proven.

    A key that is also an input_variable is exempt: declaring it as an output as
    well is a deliberate convention -- it makes AutoPkg re-display the value in
    verbose runs (showing whether the processor changed it or left it as-is),
    even though the processor never reassigns it via self.env.
    """
    node = attrs.get("output_variables")
    if not isinstance(node, ast.Dict):
        return []  # missing/non-dict is E016/E017's job
    writes_static, writes_dynamic, _ = class_env_usage(proc)
    if writes_dynamic:
        return []
    inp = attrs.get("input_variables")
    input_keys = (
        {key for key, _ in dict_entries(inp)} if isinstance(inp, ast.Dict) else set()
    )
    issues = []
    for var_name, spec in dict_entries(node):
        if var_name in writes_static or var_name in input_keys:
            continue
        issues.append(
            (
                getattr(spec, "lineno", proc.lineno),
                "E029",
                f"output_variable `{var_name}` is declared but never set via self.env",
            )
        )
    return issues


def check_outputs_declared(proc, attrs, source_lines):
    """W004 (warning): a `self.env["k"] = ...` write whose key is not an output.

    A value written to self.env but not declared in output_variables is invisible
    to recipe authors. This is a warning, not an error, because some writes are
    intentionally undeclared. Exempt: keys in output_variables or input_variables,
    ALL_CAPS config/credentials, AUTOPKG_BUILTINS control vars (e.g.
    stop_processing_recipe), keys the processor also reads (internal state such as
    a cached requests_session), and any write whose line carries the
    `# output-undeclared-ok` marker (for deliberately undeclared large values).
    """
    out = attrs.get("output_variables")
    inp = attrs.get("input_variables")
    out_keys = (
        {key for key, _ in dict_entries(out)} if isinstance(out, ast.Dict) else set()
    )
    inp_keys = (
        {key for key, _ in dict_entries(inp)} if isinstance(inp, ast.Dict) else set()
    )
    _, _, reads = class_env_usage(proc)
    read_keys = {key for key, _ in reads}
    exempt = out_keys | inp_keys | read_keys | AUTOPKG_BUILTINS

    issues = []
    seen = set()
    for node in ast.walk(proc):
        if not (
            isinstance(node, ast.Subscript)
            and _is_self_env(node.value)
            and isinstance(node.ctx, ast.Store)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            continue
        key = node.slice.value
        if key in seen or key in exempt or key.isupper():
            continue
        # the marker may sit on the write's own line or the line directly above it
        context = source_lines[max(0, node.lineno - 2) : node.lineno]
        if any(OUTPUT_UNDECLARED_MARKER in line for line in context):
            continue
        seen.add(key)
        issues.append(
            (
                node.lineno,
                "W004",
                f"writes env key `{key}` not declared in output_variables",
            )
        )
    return issues


def undeclared_env_reads(proc, attrs):
    """Return [(key, lineno), ...] for env reads that should be declared inputs.

    Allowed without declaration: keys in input_variables or output_variables, the
    AUTOPKG_BUILTINS download-chain/core variables, ALL_CAPS keys (external
    configuration/credentials, e.g. BES_PASSWORD, supplied via the environment),
    and any key the class also writes to self.env (its own cached/internal state,
    not an input -- e.g. a pickled requests_session round-tripped across runs).
    One entry per key, at its first read; this backs both E030 and its auto-fix.
    """
    inp = attrs.get("input_variables")
    out = attrs.get("output_variables")
    declared = set()
    for dict_node in (inp, out):
        if isinstance(dict_node, ast.Dict):
            declared |= {key for key, _ in dict_entries(dict_node)}
    writes_static, _, reads = class_env_usage(proc)
    allowed = declared | AUTOPKG_BUILTINS | writes_static
    result = []
    seen = set()
    for key, lineno in reads:
        # key.isupper() is True only when every cased char is uppercase and there
        # is at least one letter -- i.e. ALL_CAPS config/constant style.
        if key in allowed or key.isupper() or key in seen:
            continue
        seen.add(key)
        result.append((key, lineno))
    return result


def check_inputs_declared(proc, attrs):
    """E030: env keys read by the class must be declared (see undeclared_env_reads)."""
    return [
        (lineno, "E030", f"reads undeclared env key `{key}`; add it to input_variables")
        for key, lineno in undeclared_env_reads(proc, attrs)
    ]


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


def maybe_fix_class_docstring(path, proc, info):
    """Auto-fix E012+E013 together when convertible.

    When the class has no docstring but sets `description = "<str>"`, promote the
    string to the class docstring and set `description = __doc__`. Returns the
    list of fixed entries (empty if nothing was fixed). `info` is unused.
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


def maybe_fix_redundant_doc_assign(path, proc, info):
    """Auto-fix E023: remove a redundant `__doc__ = description` assignment.

    Returns the list of fixed entries (empty if nothing was fixed). `info` is
    unused.
    """
    assign = redundant_doc_assign(proc)
    if assign is None:
        return []
    apply_remove_doc_assign_fix(path, assign)
    return [(assign.lineno, "E023", "removed redundant `__doc__ = description`")]


def apply_main_guard_fix(path, info, classname):
    """Write the canonical `__main__` guard; return its new 1-based start line.

    Appends the guard (after two blank lines) when none exists, or replaces an
    existing non-canonical guard's source lines in place.
    """
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    guard_lines = canonical_guard_lines(classname)
    if info.main_guard is None:
        while lines and lines[-1].strip() == "":
            lines.pop()
        start = len(lines) + 3  # two blank lines, then the guard's `if` line
        lines += ["", ""] + guard_lines
        content = "\n".join(lines) + "\n"
    else:
        guard = info.main_guard
        start = guard.lineno
        begin = guard.lineno - 1
        end = (guard.end_lineno or guard.lineno) - 1
        lines[begin : end + 1] = guard_lines
        content = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return start


def maybe_fix_inputs_declared(path, proc, info):
    """Auto-fix E030: declare each undeclared read key in input_variables.

    Adds `"<key>": {"required": False, "description": ""}`; the blank description
    then surfaces as E020 for a human to complete. Returns the list of fixed
    entries (empty if nothing was fixed). `info` is unused.
    """
    attrs, _ = class_members(proc)
    undeclared = undeclared_env_reads(proc, attrs)
    if not undeclared:
        return []
    dict_node = attrs.get("input_variables")
    if not isinstance(dict_node, ast.Dict):
        return []  # E014/E015 must be resolved first
    base_indent = next(
        (
            item.col_offset
            for item in proc.body
            if isinstance(item, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "input_variables"
                for t in item.targets
            )
        ),
        None,
    )
    if base_indent is None:
        return []
    keys = [key for key, _ in undeclared]
    if not apply_add_input_vars_fix(path, base_indent, dict_node, keys):
        return []
    return [
        (
            dict_node.lineno,
            "E030",
            f"declared `{key}` in input_variables (blank description)",
        )
        for key in keys
    ]


def maybe_fix_print(path, proc, info):
    """Auto-fix E028: rewrite simple `print(x)` calls to `self.output(x, 3)`.

    Only the safely-convertible calls are rewritten (see convertible_print_calls);
    any others remain reported as E028. Returns the list of fixed entries.
    `info` is unused.
    """
    calls = convertible_print_calls(proc)
    if not calls:
        return []
    apply_print_to_output_fix(path, calls)
    return [
        (node.lineno, "E028", "converted `print(...)` to `self.output(..., 3)`")
        for node in calls
    ]


def maybe_fix_all_declaration(path, proc, info):
    """Auto-fix E003: add `__all__ = ["<Class>"]` after the imports.

    Inserted one blank line below the last top-level import, matching the repo
    convention. Requires a processor class (proc), so a file with no class is not
    fixable and stays reported. Returns the list of fixed entries.
    """
    if info.all_names is not None:
        return []
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    last_import = None
    for node in ast.parse(src).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import = node
    if last_import is None:
        return []
    insert_at = last_import.end_lineno or last_import.lineno
    lines = src.split("\n")
    lines[insert_at:insert_at] = ["", f'__all__ = ["{proc.name}"]']
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return [(insert_at + 2, "E003", f'added `__all__ = ["{proc.name}"]`')]


def variable_example_lines(kind, indent):
    """Lines for a documented-but-empty input/output_variables assignment.

    `kind` is "input" or "output"; `indent` is the class-body leading whitespace.
    A multi-line comment above the empty `{}` shows the expected entry shape so an
    author knows what to fill in.
    """
    if kind == "input":
        return [
            f"{indent}# input_variables: every value this processor reads from the",
            f"{indent}# environment. Document each one. Example entry:",
            f'{indent}#     "example_input": {{',
            f'{indent}#         "required": False,',
            f'{indent}#         "default": "",',
            f'{indent}#         "description": "What this input controls.",',
            f"{indent}#     }},",
            f"{indent}input_variables = {{}}",
        ]
    return [
        f"{indent}# output_variables: every value this processor writes back to",
        f"{indent}# the environment. Document each one. Example entry:",
        f'{indent}#     "example_output": {{',
        f'{indent}#         "description": "What this output contains.",',
        f"{indent}#     }},",
        f"{indent}output_variables = {{}}",
    ]


def maybe_fix_create_class(path, stem):
    """Auto-fix E010 (no class found): append a minimal processor class stub.

    The class is named after the file's basename and is a complete, valid
    processor skeleton, so the re-analysis pass can chain the remaining fixes
    (E003 `__all__`, E006 `__main__` guard). The empty input/output_variables get
    an example comment above them so the author knows the shape to fill in.
    Returns the fixed entry, or None when the basename is not a valid Python
    identifier (so it stays reported).
    """
    if not stem.isidentifier():
        return None
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().split("\n")
    while lines and lines[-1].strip() == "":
        lines.pop()
    class_line = len(lines) + 3  # after the two blank separator lines
    lines += [
        "",
        "",
        f"class {stem}(Processor):",
        f'    """{stem} processor."""',
        "",
        "    description = __doc__",
    ]
    lines += variable_example_lines("input", "    ")
    lines += variable_example_lines("output", "    ")
    lines += [
        "",
        "    def main(self):",
        '        """Execution starts here."""',
    ]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return (class_line, "E010", f"created processor class `{stem}`")


def maybe_fix_main_guard(path, proc, info):
    """Auto-fix E006/E007: ensure the canonical `__main__` guard exists.

    Adds the guard when missing (E006) or rewrites a non-canonical one (E007).
    The W003 "not at end" condition is a warning only and is never auto-fixed.
    Returns the list of fixed entries (empty if nothing was fixed).
    """
    if info.main_guard is None:
        line = apply_main_guard_fix(path, info, proc.name)
        return [(line, "E006", f"added canonical `__main__` guard for {proc.name}")]
    if not guard_is_canonical(info.main_guard, proc.name):
        line = apply_main_guard_fix(path, info, proc.name)
        return [(line, "E007", "normalized `__main__` guard to canonical form")]
    return []


def check_file(path, auto_fix=True, disabled=frozenset()):
    """Check one file.

    Returns (issues, fixed), each a list of (lineno, check_id, message). When
    auto_fix is True, fixable issues (E001, E002, and E012+E013 together) are
    corrected in place and reported under `fixed` instead of `issues`.

    `disabled` is a set of check IDs (e.g. {"E005", "E020"}) to skip entirely.
    A disabled fixable check is neither auto-fixed (the file is not mutated) nor
    reported; reporting of disabled codes is filtered out by check_files().

    The body is just orchestration: run/apply the auto-fixes (which mutate the
    file and re-parse), then concatenate the results of the pure check_* helpers.
    """
    fixed = []
    stem = os.path.splitext(os.path.basename(path))[0]

    # --- W002: a path that does not exist (or is not a file) is skipped with a
    # warning rather than crashing -- pre-commit may pass a just-deleted file ---
    if not os.path.isfile(path):
        return [(1, "W002", "file not found; skipping")], fixed

    with open(path, encoding="utf-8", errors="replace") as handle:
        src = handle.read()

    if SKIP_MARKER in src:
        return [], fixed

    try:
        tree = ast.parse(src)
    except SyntaxError as err:
        return [(err.lineno or 1, "E000", f"syntax error: {err.msg}")], fixed

    # --- W001: only AutoPkg processors are subject to these conventions ---
    # A .py file that doesn't import autopkglib is not a processor (a helper
    # script, shared util, etc.). Warn and skip rather than flag it with dozens
    # of irrelevant violations. W001 is a warning: it does not fail the hook.
    if not imports_autopkglib(tree):
        return [
            (
                1,
                "W001",
                "no `autopkglib` import found; skipping (not an AutoPkg processor)",
            )
        ], fixed

    issues = []

    # --- E001: shebang (auto-fixable) ---
    lines = src.splitlines()
    if not lines or lines[0].rstrip() != EXPECTED_SHEBANG:
        if auto_fix and "E001" not in disabled:
            apply_shebang_fix(path)
            fixed.append((1, "E001", f"set first line to `{EXPECTED_SHEBANG}`"))
            with open(path, encoding="utf-8", errors="replace") as handle:
                src = handle.read()
            tree = ast.parse(src)
        else:
            issues.append((1, "E001", f"first line should be `{EXPECTED_SHEBANG}`"))

    # --- E002: module docstring (auto-fixable when completely missing) ---
    if auto_fix and "E002" not in disabled:
        fixed_entry, new_src = maybe_fix_module_docstring(path, tree, stem)
        if fixed_entry:
            fixed.append(fixed_entry)
            tree = ast.parse(new_src)
    issues += check_module_docstring(tree)

    # --- E025: author/created header comment between shebang and docstring ---
    # Needs both a shebang and a module docstring to be present (E001/E002 add
    # those first). The auto-fix stamps the file's original git author + year, or
    # the current user + year for a brand-new/untracked file.
    doc_lineno = module_docstring_lineno(tree)
    lines = src.splitlines()
    if (
        "E025" not in disabled
        and doc_lineno is not None
        and lines
        and lines[0].rstrip() == EXPECTED_SHEBANG
        and not has_header_comment(lines, doc_lineno)
    ):
        if auto_fix:
            author, year = git_created_by(path)
            apply_header_comment_fix(path, author, year)
            fixed.append((2, "E025", f"added `# Created {year} by {author}`"))
            with open(path, encoding="utf-8", errors="replace") as handle:
                src = handle.read()
            tree = ast.parse(src)
        else:
            issues.append(
                (
                    2,
                    "E025",
                    "missing author/created comment between the shebang and the module docstring",
                )
            )

    # --- module-level checks ---
    info = scan_module(tree)
    issues += check_all_declared(info)  # E003
    issues += check_processor_error_import(info)  # E005

    proc = pick_processor_class(info, stem)
    if proc is None:
        # E010 (no class): auto-fixable by creating a stub class named for the
        # file; the re-analysis then chains the remaining fixes (E003, E006, ...).
        if auto_fix and "E010" not in disabled:
            class_fixed = maybe_fix_create_class(path, stem)
            if class_fixed:
                fixed.append(class_fixed)
                more_issues, more_fixed = check_file(
                    path, auto_fix=auto_fix, disabled=disabled
                )
                return more_issues, fixed + more_fixed
        issues.append((1, "E010", "no class found in this processor file"))
        return sorted(issues), fixed

    # --- class-level auto-fixes; the first one that applies re-analyzes the
    # now-fixed file, which lets fixes chain (e.g. E012/E013 then E023). A fixer
    # is skipped (no mutation) when any code it would emit is disabled. ---
    if auto_fix:
        for fixer, codes in (
            (maybe_fix_all_declaration, {"E003"}),
            (maybe_fix_class_docstring, {"E012", "E013"}),
            (maybe_fix_redundant_doc_assign, {"E023"}),
            (maybe_fix_main_guard, {"E006", "E007"}),
            (maybe_fix_print, {"E028"}),
            (maybe_fix_inputs_declared, {"E030"}),
        ):
            if codes & disabled:
                continue
            class_fixed = fixer(path, proc, info)
            if class_fixed:
                fixed += class_fixed
                more_issues, more_fixed = check_file(
                    path, auto_fix=auto_fix, disabled=disabled
                )
                return more_issues, fixed + more_fixed

    # --- class-level checks ---
    attrs, main_func = class_members(proc)
    issues += check_class_naming(proc, stem, info)  # E010 / E011
    issues += check_base_class(proc, info)  # E004
    issues += check_class_docstring(proc)  # E012
    issues += check_class_docstring_sufficient(proc)  # E024
    issues += check_description(proc, attrs)  # E013
    issues += check_variable_attrs(proc, attrs)  # E014-E017
    issues += check_input_variable_entries(attrs)  # E020 / E021
    issues += check_output_variable_entries(attrs)  # E022
    issues += check_main_method(proc, main_func)  # E018 / E019
    issues += check_redundant_doc_assign(proc)  # E023
    issues += check_main_guard(proc, info)  # E006 / E007 / W003
    issues += check_no_print(proc)  # E028
    issues += check_outputs_assigned(proc, attrs)  # E029
    issues += check_inputs_declared(proc, attrs)  # E030
    issues += check_outputs_declared(proc, attrs, src.splitlines())  # W004

    return sorted(issues), fixed


def check_files(paths, auto_fix=True, disabled=frozenset()):
    """Check several files and return a list of (path, issues, fixed) tuples.

    Only `.py` paths are checked; anything else is skipped. `disabled` is a set
    of check IDs to skip entirely -- disabled codes are filtered out of both the
    issues and fixed lists here, and check_file() avoids mutating files for
    disabled fixable checks. This is the programmatic entry point: it does no
    printing, so other Python code can call it and consume the structured
    results directly. `main()` wraps it to print and to compute an exit code.
    """
    results = []
    for path in paths:
        if not path.endswith(".py"):
            continue
        issues, fixed = check_file(path, auto_fix=auto_fix, disabled=disabled)
        issues = [item for item in issues if item[1] not in disabled]
        fixed = [item for item in fixed if item[1] not in disabled]
        results.append((path, issues, fixed))
    return results


def main(argv):
    """Execution starts here."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--auto-fix",
        choices=["yes", "no"],
        default="yes",
        help="automatically fix fixable issues in place (default: yes)",
    )
    parser.add_argument(
        "--disable",
        default="",
        metavar="CODES",
        help="comma-separated check IDs to skip entirely, e.g. --disable E005,E020",
    )
    parser.add_argument("files", nargs="*", help="processor .py files to check")
    args = parser.parse_args(argv)
    auto_fix = args.auto_fix == "yes"

    disabled = {
        code.strip().upper() for code in args.disable.split(",") if code.strip()
    }
    unknown = disabled - KNOWN_CODES
    if unknown:
        print(
            f"warning: ignoring unknown --disable code(s): {', '.join(sorted(unknown))}"
        )

    issue_count = 0
    fix_count = 0
    warning_count = 0
    for path, issues, fixed in check_files(
        args.files, auto_fix=auto_fix, disabled=disabled
    ):
        for lineno, check_id, message in fixed:
            fix_count += 1
            print(f"{path}:{lineno}: [{check_id}] auto-fixed: {message}")
        for lineno, check_id, message in issues:
            # W-codes are advisory (e.g. "not a processor") and never fail the hook
            if check_id.startswith("W"):
                warning_count += 1
                print(f"{path}:{lineno}: [{check_id}] warning: {message}")
            else:
                issue_count += 1
                print(f"{path}:{lineno}: [{check_id}] {message}")

    if fix_count:
        print(f"\nauto-fixed {fix_count} issue(s); review and re-stage the changes.")
    if warning_count:
        print(f"{warning_count} file(s) skipped (see warnings above).")
    if issue_count:
        print(f"{issue_count} remaining processor-convention issue(s).")
    # non-zero if anything was fixed (so the user re-stages) or any real issue
    # remains; warnings alone do not fail the hook
    return 1 if (issue_count or fix_count) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
