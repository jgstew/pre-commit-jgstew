#!/usr/bin/env python3
"""Pre-commit hook: check AutoPkg recipes for cross-field conventions.

This is the recipe-level companion to autopkg_processor_check_conventions.py.
It is intentionally PICKY and OPINIONATED, but deliberately narrow: it only
checks things the JSON schema (.AutoPkgRecipeOpinionated.schema.json) *cannot*
express. JSON Schema (Draft 7, which check-jsonschema enforces here) validates
each field on its own -- it has no way to compare one field against another.
So the schema already covers structure and per-field patterns (Identifier
prefix, "no spaces", MinimumVersion shape, ...); this tool adds the CROSS-FIELD
checks it can't.

The flagship check (W110) is the one that prompted this tool: the `Identifier`
should refer to the `Input` `NAME`. It is checked with a NORMALIZED comparison
(case-folded, non-alphanumerics removed, a trailing "test"/"example" dropped
from NAME), because that is how this repo actually names recipes -- e.g. NAME
`FolderListFilesTest` pairs with identifier `com.github.jgstew.test.FolderListFiles`,
and NAME `Python3Win64` with `com.github.jgstew.download.Python3-Win64`. An exact
substring rule would misfire on more than half the repo; the normalized rule
leaves only a handful of genuine mismatches. Pass --exact to use the stricter
literal-substring rule instead.

A recipe is any file whose name ends in one of: .recipe.yaml, .recipe.yml, or
.recipe (the last is usually a plist; both plist and YAML are parsed).

A second cross-field check (W111) verifies that every `ParentRecipe` resolves:
its value should be the `Identifier` of some recipe in this repo. (AutoPkg will
also accept a ParentRecipe given as a file path, but only relative to the
working directory it runs from -- fragile and non-portable -- so a path-like
value is flagged with a hint to use the identifier instead.) The check is built
from a repo-wide index of every recipe's Identifier, so it works even when
pre-commit only passes the changed files. W111 is AUTO-FIXABLE: when a
ParentRecipe is a path that points at a recipe whose Identifier can be read, it
is rewritten in place to that Identifier (see --auto-fix).

Usage:
    autopkg_recipe_check_conventions.py [--strict] [--exact]
        [--auto-fix=yes|no] [--disable W110,W111] [Foo.download.recipe.yaml ...]

With no file arguments, all recipe files in the current folder and below are
checked. --disable takes a comma-separated list of check IDs to skip entirely.
--auto-fix rewrites the fixable conventions in place (a path-like ParentRecipe ->
its Identifier, W111; a below-floor MinimumVersion -> the floor, W114; and
Process-step spacing, W120); it defaults to yes when files are given explicitly,
but to no when auto-discovering, so a bare run is read-only. An auto-fixed file
fails the hook so the change is reviewed and re-staged.

A third check (W120, YAML-only, AUTO-FIXABLE) enforces the Process-step spacing
convention: within `Process:`, consecutive `- Processor:` steps are separated by
exactly one blank line. More than one blank line is collapsed to one; a missing
one is inserted. Comment placement is decided by INDENTATION relative to the
`- ` item indent:

  * a comment at the item indent (e.g. `  # note`) is a leading comment for the
    NEXT step, so the blank line goes BEFORE it;
  * a comment indented deeper (e.g. `    # Arguments:` or `      # curl_opts`) is
    commented-out body of the PREVIOUS step, so it stays with that step and the
    blank line goes AFTER it.

A gap that contains a commented-out step (`# - ...`) is left untouched -- that is
a disabled step block whose surrounding spacing is intentional/ambiguous.

Checks:
    W110  Identifier does not appear to reference the Input NAME (flagship)
    W111  ParentRecipe does not resolve to a known recipe Identifier (fixable)
    W112  Identifier is duplicated by another recipe in the repo
    W113  filename type-infix does not match the identifier type-segment
    W114  MinimumVersion is below the repo floor (YAML-only; fixable -> raised)
    W115  ParentRecipe chain is cyclic / self-referential
    W116  http:// URL where https:// is preferred
    W117  re_pattern / asset_regex is not a valid regex
    W118  a com.github.jgstew.Shared*Processors/X step has no matching X.py
    W120  Process-step blank-line spacing (YAML-only, fixable)
    W121  Input NAME segment is not letter-start alphanumeric (dash-separated)
    W122  Input NAME trailing platform/arch suffix is not canonical casing
    W123  Description is identical to another recipe's
    W100  recipe could not be parsed; skipped (advisory -- check-yaml /
          validate-plist are the authorities on file validity)
    W101  PyYAML is not available, so a YAML recipe could not be parsed; skipped
    W102  top-level of the recipe is not a mapping; skipped

Warnings are advisory and do NOT fail the hook, so wire the hook with
`verbose: true` to surface them. Pass --strict to turn every warning into a
failure (non-zero exit) -- useful in CI.

A file can opt out of all checks with a comment anywhere in it:
    # pre-commit-skip: recipe-conventions
out of just the NAME/Identifier check with:
    # identifier-name-ok
out of just the ParentRecipe check (e.g. a parent defined in another repo) with:
    # parent-recipe-ok
out of just the Process-step spacing check with:
    # process-spacing-ok
or out of one of the other single-purpose checks with, respectively:
    # duplicate-identifier-ok   (W112)
    # type-mismatch-ok          (W113)
    # minimum-version-ok        (W114)
    # http-url-ok               (W116)
    # regex-ok                  (W117)
    # processor-ref-ok          (W118)
    # recipe-name-ok            (W121)
    # recipe-name-suffix-ok     (W122)
    # duplicate-description-ok  (W123)

Exit codes:
    0  no failures (warnings alone do not fail unless --strict)
    1  a file was auto-fixed, or a warning was raised while --strict is set
"""

import argparse
import collections
import os
import plistlib
import re
import sys

try:
    import yaml
except ImportError:  # PyYAML may be absent in a bare environment; degrade to W101
    yaml = None

SKIP_MARKER = "pre-commit-skip: recipe-conventions"

# File-level opt-out for the NAME/Identifier check (W110), for a recipe whose
# identifier intentionally does not track its NAME.
IDENTIFIER_NAME_MARKER = "identifier-name-ok"

# File-level opt-out for the ParentRecipe check (W111), for a recipe whose
# parent is legitimately defined outside this repo (so it is not in the index).
PARENT_RECIPE_MARKER = "parent-recipe-ok"

# File-level opt-out for the Process-step spacing check (W120).
PROCESS_SPACING_MARKER = "process-spacing-ok"

# File-level opt-out for the duplicate-Identifier check (W112), for a recipe that
# intentionally shares an identifier with another (e.g. a "Core" test variant).
DUPLICATE_IDENTIFIER_MARKER = "duplicate-identifier-ok"

# File-level opt-out for the duplicate-Description check (W123), for a recipe that
# intentionally shares its Description with another.
DUPLICATE_DESCRIPTION_MARKER = "duplicate-description-ok"

# File-level opt-out for the filename-type vs identifier-type check (W113).
TYPE_MISMATCH_MARKER = "type-mismatch-ok"

# File-level opt-out for the MinimumVersion floor check (W114).
MINIMUM_VERSION_MARKER = "minimum-version-ok"

# File-level opt-out for the http:// URL check (W116), for a host that only
# serves plain http.
HTTP_URL_MARKER = "http-url-ok"

# File-level opt-out for the regex-validity check (W117), for a pattern that is
# only a valid regex after %variable% substitution.
REGEX_MARKER = "regex-ok"

# File-level opt-out for the processor-reference-exists check (W118).
PROCESSOR_REF_MARKER = "processor-ref-ok"

# The repo's shared-processor folders; a com.github.jgstew.<folder>/<Name>
# reference into one of these must correspond to a real <Name>.py file (W118).
PROCESSOR_FOLDERS = ("SharedProcessors", "SharedDangerousProcessors")

# Recipe-NAME style checks. W121: each dash-separated segment of Input NAME must
# be alphanumeric starting with a letter (so `Firefox-Win`, `Python3-Win64`, and
# lowercase vendor names like `log4j2-scan` are all fine; underscores, spaces,
# leading digits, and empty segments are not). W122: if the trailing segment is a
# recognized platform/arch token, it must use the canonical casing below (e.g.
# `-mac` -> `-Mac`); a segment that is NOT a known platform token (e.g. the
# `share` in `tty-share`) is left alone.
RECIPE_NAME_MARKER = "recipe-name-ok"  # W121 opt-out
RECIPE_NAME_SUFFIX_MARKER = "recipe-name-suffix-ok"  # W122 opt-out
RECIPE_NAME_SEGMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
PLATFORM_CANONICAL = {
    "win": "Win",
    "windows": "Win",
    "win32": "Win32",
    "win64": "Win64",
    "mac": "Mac",
    "macos": "Mac",
    "osx": "Mac",
    "linux": "Linux",
    "linux32": "Linux32",
    "linux64": "Linux64",
    "universal": "Universal",
    "arm64": "Arm64",
    "intel": "Intel",
}

# The extensions that make a file an AutoPkg recipe. `.recipe` is usually a
# plist; the `.recipe.y{a,}ml` forms are YAML. Order matters for endswith().
RECIPE_EXTENSIONS = (".recipe.yaml", ".recipe.yml", ".recipe")

# Repo identifier prefix, and the lowest MinimumVersion a recipe should declare
# (W114). A recipe below this floor is auto-fixed up to it.
RECIPE_IDENTIFIER_PREFIX = "com.github.jgstew."
MINIMUM_VERSION_FLOOR = "2.4.1"

# Suffixes stripped from a normalized NAME before comparing to the identifier:
# test/example recipes name their NAME `<Thing>Test` / `<Thing>Example` while
# the identifier carries `<Thing>` in the `.test.`/`.example.` component.
NAME_CORE_SUFFIXES = ("test", "example")

# Every check ID this tool can emit -- used to validate --disable arguments so a
# typo (e.g. "W99") is reported rather than silently ignored.
KNOWN_CODES = frozenset(
    [
        "W100",  # could not parse recipe
        "W101",  # PyYAML unavailable
        "W102",  # top-level is not a mapping
        "W110",  # Identifier does not reference the Input NAME
        "W111",  # ParentRecipe does not resolve to a known Identifier
        "W112",  # duplicate Identifier across the repo
        "W113",  # filename type-infix does not match the identifier type-segment
        "W114",  # MinimumVersion below the repo floor
        "W115",  # cyclic / self ParentRecipe chain
        "W116",  # http:// URL (prefer https://)
        "W117",  # invalid re_pattern / asset_regex
        "W118",  # jgstew processor reference has no matching .py file
        "W120",  # Process-step blank-line spacing
        "W121",  # Input NAME segment is not letter-start alphanumeric
        "W122",  # Input NAME platform suffix is not canonical casing
        "W123",  # Description is identical to another recipe's
    ]
)


def normalize(value):
    """Return `value` lowercased with every non-alphanumeric character removed.

    This collapses the cosmetic differences (case, dots, hyphens, underscores)
    between a NAME and an Identifier so only the meaningful characters remain.
    """
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def name_core(name):
    """Return the normalized NAME with a trailing test/example suffix removed.

    `FolderListFilesTest` -> `folderlistfiles`; `AutoPkgCacheCleanupExample` ->
    `autopkgcachecleanup`. The suffix is only stripped when something remains,
    so a NAME that is literally "Test" is left intact.
    """
    core = normalize(name)
    for suffix in NAME_CORE_SUFFIXES:
        if core.endswith(suffix) and len(core) > len(suffix):
            return core[: -len(suffix)]
    return core


def find_line(source_lines, *patterns):
    """Return the 1-based line of the first line matching any regex, else 1.

    Used only to point the reader at the relevant line (e.g. the `Identifier:`
    line in YAML, or `<key>Identifier</key>` in a plist); a miss falls back to
    line 1 rather than failing.
    """
    for index, line in enumerate(source_lines, start=1):
        if any(re.search(pattern, line) for pattern in patterns):
            return index
    return 1


def parse_recipe(src):
    """Parse recipe source into a Python object.

    Returns (data, warning) where exactly one is set: `data` is the parsed
    object on success, or `warning` is a (check_id, message) tuple describing
    why parsing was skipped. Plist (`<?xml`/`<plist`) is detected by a leading
    `<`; everything else is treated as YAML.
    """
    if src.lstrip().startswith("<"):
        try:
            return plistlib.loads(src.encode("utf-8")), None
        except (ValueError, plistlib.InvalidFileException) as err:
            return None, ("W100", f"could not parse plist recipe: {err}")
    if yaml is None:
        return None, ("W101", "PyYAML not available; YAML recipe skipped")
    try:
        return yaml.safe_load(src), None
    except yaml.YAMLError as err:
        detail = str(err).splitlines()[0] if str(err) else "invalid YAML"
        return None, ("W100", f"could not parse YAML recipe: {detail}")


# --- individual checks -------------------------------------------------------
# Each returns a (possibly empty) list of (lineno, check_id, message) tuples and
# never mutates anything, so they can be read, tested, and reordered in
# isolation. check_file just calls them in order and concatenates the results.


def check_identifier_references_name(recipe, source_lines, exact):
    """W110: the Identifier should reference the Input NAME.

    Skipped when either value is missing (the schema's `required` handles that)
    or when NAME is empty. In the default (normalized) mode the NAME core must
    appear inside the normalized identifier; with `exact` the raw NAME must be a
    literal substring of the raw identifier. A recipe that intentionally breaks
    the convention can carry the `# identifier-name-ok` comment to opt out.
    """
    identifier = recipe.get("Identifier")
    input_block = recipe.get("Input")
    name = input_block.get("NAME") if isinstance(input_block, dict) else None
    if not isinstance(identifier, str) or not name:
        return []

    if exact:
        matches = str(name) in identifier
        how = "as a substring (exact)"
    else:
        matches = bool(name_core(name)) and name_core(name) in normalize(identifier)
        how = "(normalized: case/punctuation-insensitive)"
    if matches:
        return []

    lineno = find_line(source_lines, r"^\s*Identifier\s*:", r"<key>Identifier</key>")
    return [
        (
            lineno,
            "W110",
            f"Identifier `{identifier}` does not reference the Input NAME "
            f"`{name}` {how}; add `# {IDENTIFIER_NAME_MARKER}` if intentional",
        )
    ]


def check_parent_recipe_resolvable(recipe, source_lines, identifier_index):
    """W111: a `ParentRecipe` should resolve to a known recipe Identifier.

    The `ParentRecipe` value should match the `Identifier` of some recipe in
    `identifier_index` (built from the whole repo). Matching is case-sensitive,
    as AutoPkg's is. Skipped when there is no ParentRecipe, when it is not a
    string, or when the index is unavailable.

    Note: AutoPkg's locate_recipe() tries `os.path.isfile(name)` before its
    identifier search, so a ParentRecipe given as a file path can still resolve
    -- but only relative to the working directory autopkg runs from, which is
    fragile and non-portable. So a path-like value is flagged with a hint to use
    the parent's identifier instead, rather than reported as strictly broken.
    """
    parent = recipe.get("ParentRecipe")
    if not isinstance(parent, str) or not parent:
        return []
    if parent in identifier_index:
        return []

    looks_like_path = "/" in parent or parent.endswith(RECIPE_EXTENSIONS)
    hint = (
        " (it looks like a file path -- AutoPkg only resolves that relative to "
        "the working directory; use the parent's Identifier instead)"
        if looks_like_path
        else ""
    )
    lineno = find_line(
        source_lines, r"^\s*ParentRecipe\s*:", r"<key>ParentRecipe</key>"
    )
    return [
        (
            lineno,
            "W111",
            f"ParentRecipe `{parent}` does not match any recipe Identifier "
            f"in this repo{hint}; add `# {PARENT_RECIPE_MARKER}` if the parent is "
            "defined elsewhere",
        )
    ]


def check_duplicate_identifier(recipe, path, source_lines, index):
    """W112: no two recipes in the repo should share an Identifier.

    AutoPkg resolves a parent by identifier, so duplicate identifiers make parent
    resolution ambiguous. Cross-file, so the schema cannot see it. Reports the
    OTHER file(s) that declare the same identifier.
    """
    identifier = recipe.get("Identifier")
    if not isinstance(identifier, str) or index is None:
        return []
    paths = index.by_identifier.get(identifier, [])
    others = [p for p in paths if p != os.path.normpath(path)]
    if not others:
        return []
    lineno = find_line(source_lines, r"^\s*Identifier\s*:", r"<key>Identifier</key>")
    shown = ", ".join(sorted(others))
    return [
        (
            lineno,
            "W112",
            f"Identifier `{identifier}` is also declared by: {shown}; identifiers "
            f"must be unique -- add `# {DUPLICATE_IDENTIFIER_MARKER}` if intentional",
        )
    ]


def normalize_description(desc):
    """Return a Description normalized for duplicate comparison (W123).

    Strips ends and collapses internal whitespace, so descriptions differing only
    in spacing are still treated as identical.
    """
    return " ".join(str(desc).split())


def check_duplicate_description(recipe, path, source_lines, index):
    """W123: each recipe's Description should be distinct across the repo.

    A Description shared verbatim by another recipe is almost always a copy-paste
    that no longer fits (or a variant that should say how it differs). Cross-file,
    so neither the schema nor macadmin can see it. Reports the OTHER file(s) with
    the same Description.
    """
    desc = recipe.get("Description")
    if not isinstance(desc, str) or not desc.strip() or index is None:
        return []
    paths = index.by_description.get(normalize_description(desc), [])
    others = [p for p in paths if p != os.path.normpath(path)]
    if not others:
        return []
    lineno = find_line(source_lines, r"^\s*Description\s*:", r"<key>Description</key>")
    shown = ", ".join(sorted(others))
    return [
        (
            lineno,
            "W123",
            f"Description is identical to: {shown}; make each recipe's Description "
            f"distinct -- add `# {DUPLICATE_DESCRIPTION_MARKER}` if intentional",
        )
    ]


def filename_type(path):
    """Return the `<type>` infix of a recipe filename, or None.

    `Foo.download.recipe.yaml` -> "download"; `QnA.pkg.recipe` -> "pkg".
    """
    match = re.match(
        r"^.+?\.([A-Za-z0-9]+)\.recipe(?:\.ya?ml)?$", os.path.basename(path)
    )
    return match.group(1) if match else None


def identifier_type(identifier):
    """Return the type-segment of a `com.github.jgstew.<type>.<Name>` identifier."""
    if not isinstance(identifier, str):
        return None
    if not identifier.startswith(RECIPE_IDENTIFIER_PREFIX):
        return None
    rest = identifier[len(RECIPE_IDENTIFIER_PREFIX) :]
    return rest.split(".", 1)[0] if "." in rest else None


def check_filename_identifier_type(recipe, path, source_lines):
    """W113: the filename type-infix should match the identifier type-segment.

    `Foo.download.recipe.yaml` should pair with `com.github.jgstew.download.Foo`.
    A mismatch is usually a typo or a mis-scoped identifier. Cross-field (filename
    vs identifier), so the schema cannot see it.
    """
    ftype = filename_type(path)
    itype = identifier_type(recipe.get("Identifier"))
    if ftype is None or itype is None or ftype.lower() == itype.lower():
        return []
    lineno = find_line(source_lines, r"^\s*Identifier\s*:", r"<key>Identifier</key>")
    return [
        (
            lineno,
            "W113",
            f"filename type `{ftype}` does not match identifier type `{itype}` "
            f"(`{recipe.get('Identifier')}`); add `# {TYPE_MISMATCH_MARKER}` if "
            "intentional",
        )
    ]


def version_tuple(value):
    """Return a version string as a tuple of ints, or None if not all-numeric."""
    parts = str(value).split(".")
    try:
        return tuple(int(part) for part in parts)
    except ValueError:
        return None


def check_minimum_version(recipe, source_lines):
    """W114: MinimumVersion should be at least the repo floor (auto-fixable).

    The schema validates MinimumVersion's SHAPE but cannot enforce a floor. An
    unparsable value is left for a human (not flagged here). Auto-fixed up to the
    floor by maybe_fix_minimum_version.
    """
    value = recipe.get("MinimumVersion")
    if value is None:
        return []
    current = version_tuple(value)
    floor = version_tuple(MINIMUM_VERSION_FLOOR)
    if current is None or current >= floor:
        return []
    lineno = find_line(
        source_lines, r"^\s*MinimumVersion\s*:", r"<key>MinimumVersion</key>"
    )
    return [
        (
            lineno,
            "W114",
            f"MinimumVersion `{value}` is below the repo floor "
            f"`{MINIMUM_VERSION_FLOOR}`; add `# {MINIMUM_VERSION_MARKER}` if "
            "intentional",
        )
    ]


def check_parent_cycle(recipe, path, source_lines, index):
    """W115: the ParentRecipe chain must not be cyclic (or self-referential).

    Starts from this recipe's own resolved ParentRecipe, then walks the repo-wide
    parent graph; a revisited identifier means a cycle (a self-parent is the
    length-1 case). Graph-level, so the schema cannot see it. An unresolvable
    parent is W111's concern, not this one.
    """
    identifier = recipe.get("Identifier")
    parent_value = recipe.get("ParentRecipe")
    if (
        not isinstance(identifier, str)
        or not isinstance(parent_value, str)
        or not parent_value
        or index is None
    ):
        return []
    first = resolve_to_identifier(path, parent_value, index.by_path, index.identifiers)
    if first is None:
        if parent_value != identifier:
            return []  # parent not resolvable to a known identifier (W111 covers it)
        first = identifier  # self-reference even when not otherwise indexed
    seen = {identifier}
    chain = [identifier]
    current = first
    while current is not None:
        chain.append(current)
        if current in seen:
            lineno = find_line(
                source_lines, r"^\s*ParentRecipe\s*:", r"<key>ParentRecipe</key>"
            )
            return [
                (
                    lineno,
                    "W115",
                    "ParentRecipe chain is cyclic: " + " -> ".join(chain),
                )
            ]
        seen.add(current)
        current = index.parent_of.get(current)
    return []


def iter_string_values(obj):
    """Yield every string value nested anywhere within `obj` (dicts/lists)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from iter_string_values(value)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            yield from iter_string_values(item)


def find_value_line(source_lines, needle):
    """Return the 1-based line that contains `needle` as a substring, else 1."""
    for index, line in enumerate(source_lines, start=1):
        if needle in line:
            return index
    return 1


def check_http_urls(recipe, source_lines):
    """W116: prefer https:// over http:// in recipe values.

    Per-value semantic check the schema (format: uri) cannot express. Each
    distinct http:// value is reported once.
    """
    issues = []
    seen = set()
    for value in iter_string_values(recipe):
        if "http://" in value and value not in seen:
            seen.add(value)
            issues.append(
                (
                    find_value_line(source_lines, value),
                    "W116",
                    f"prefer https:// over http:// in {value!r}; add "
                    f"`# {HTTP_URL_MARKER}` if the host only serves http",
                )
            )
    return issues


def check_regex_arguments(recipe, source_lines):
    """W117: `re_pattern` / `asset_regex` values must be valid regexes.

    The schema explicitly cannot validate regex; this compiles each one. A pattern
    that is only valid after %variable% substitution can opt out with `# regex-ok`.
    """
    issues = []
    for step in recipe.get("Process") or []:
        if not isinstance(step, dict):
            continue
        for key, value in (step.get("Arguments") or {}).items():
            if key not in ("re_pattern", "asset_regex") or not isinstance(value, str):
                continue
            try:
                re.compile(value)
            except re.error as err:
                issues.append(
                    (
                        find_value_line(source_lines, value),
                        "W117",
                        f"{key} is not a valid regex: {err}; add `# {REGEX_MARKER}` "
                        "if it is only valid after %variable% substitution",
                    )
                )
    return issues


def check_processor_refs_exist(recipe, source_lines, index):
    """W118: a `com.github.jgstew.<folder>/<Name>` step must map to a real file.

    Only references into this repo's shared-processor folders (PROCESSOR_FOLDERS)
    are validated -- a missing one is a typo, a stale name after a rename, or a
    core processor mislabeled with the jgstew namespace. Cross-file, and the
    schema's fallback `com.github.\\S+` pattern accepts anything, so neither the
    schema nor macadmin catches it.
    """
    if index is None:
        return []
    issues = []
    seen = set()
    for step in recipe.get("Process") or []:
        if not isinstance(step, dict):
            continue
        ref = step.get("Processor")
        if not isinstance(ref, str) or not ref.startswith(RECIPE_IDENTIFIER_PREFIX):
            continue
        folder = ref[len(RECIPE_IDENTIFIER_PREFIX) :].split("/", 1)[0]
        if folder not in PROCESSOR_FOLDERS:
            continue  # only validate refs into this repo's shared-processor folders
        if ref in index.processors or ref in seen:
            continue
        seen.add(ref)
        issues.append(
            (
                find_value_line(source_lines, ref),
                "W118",
                f"processor `{ref}` has no matching .py in {folder}/ (typo, rename, "
                f"or a core processor mislabeled with the jgstew namespace); add "
                f"`# {PROCESSOR_REF_MARKER}` if intentional",
            )
        )
    return issues


def _recipe_name(recipe):
    """Return the Input NAME string, or None."""
    inp = recipe.get("Input")
    name = inp.get("NAME") if isinstance(inp, dict) else None
    return name if isinstance(name, str) and name else None


def check_recipe_name_segments(recipe, source_lines):
    """W121: each dash-separated segment of Input NAME is letter-start alphanumeric.

    Allows CamelCase and lowercase vendor names (`Firefox-Win`, `log4j2-scan`);
    rejects underscores, spaces, leading digits, and empty segments. Complements
    the schema's "no spaces" with a stronger word-shape rule the schema can't do.
    """
    name = _recipe_name(recipe)
    if name is None:
        return []
    bad = [s for s in name.split("-") if not RECIPE_NAME_SEGMENT_RE.match(s)]
    if not bad:
        return []
    lineno = find_line(source_lines, r"^\s*NAME\s*:", r"<key>NAME</key>")
    return [
        (
            lineno,
            "W121",
            f"Input NAME `{name}` segment `{bad[0]}` should be alphanumeric and "
            f"start with a letter (dash-separated, e.g. Firefox-Win); add "
            f"`# {RECIPE_NAME_MARKER}` if intentional",
        )
    ]


def check_recipe_name_suffix(recipe, source_lines):
    """W122: a recognized trailing platform/arch suffix must use canonical casing.

    Only the last dash-segment is considered, and only when it is a known platform
    token (see PLATFORM_CANONICAL): `-mac` -> `-Mac`, `-windows` -> `-Win`, etc. A
    trailing segment that is not a known platform token (the `share` in
    `tty-share`, `scan` in `log4j2-scan`) is left alone.
    """
    name = _recipe_name(recipe)
    if name is None or "-" not in name:
        return []
    suffix = name.rsplit("-", 1)[1]
    canonical = PLATFORM_CANONICAL.get(suffix.lower())
    if not canonical or suffix == canonical:
        return []
    lineno = find_line(source_lines, r"^\s*NAME\s*:", r"<key>NAME</key>")
    return [
        (
            lineno,
            "W122",
            f"Input NAME `{name}` platform suffix `{suffix}` should be canonical "
            f"`{canonical}`; add `# {RECIPE_NAME_SUFFIX_MARKER}` if intentional",
        )
    ]


def apply_minimum_version_fix(path):
    """Rewrite the file's MinimumVersion value in place to the repo floor."""
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    floor = f'"{MINIMUM_VERSION_FLOOR}"'
    if src.lstrip().startswith("<"):
        new_src = re.sub(
            r"(<key>MinimumVersion</key>\s*<string>)(.*?)(</string>)",
            lambda m: m.group(1) + MINIMUM_VERSION_FLOOR + m.group(3),
            src,
            count=1,
            flags=re.DOTALL,
        )
    else:
        lines = src.split("\n")
        for i, line in enumerate(lines):
            match = re.match(r"^(\s*MinimumVersion\s*:\s*)(\S+)(.*)$", line)
            if match:
                lines[i] = match.group(1) + floor + match.group(3)
                break
        new_src = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(new_src)


def maybe_fix_minimum_version(path, recipe):
    """Auto-fix W114: raise a below-floor MinimumVersion to the repo floor.

    Returns the list of fixed entries (empty if the value is at/above the floor or
    unparsable).
    """
    value = recipe.get("MinimumVersion")
    current = version_tuple(value) if value is not None else None
    floor = version_tuple(MINIMUM_VERSION_FLOOR)
    if current is None or current >= floor:
        return []
    with open(path, encoding="utf-8", errors="replace") as handle:
        source_lines = handle.read().splitlines()
    lineno = find_line(
        source_lines, r"^\s*MinimumVersion\s*:", r"<key>MinimumVersion</key>"
    )
    apply_minimum_version_fix(path)
    return [
        (
            lineno,
            "W114",
            f"raised MinimumVersion `{value}` to `{MINIMUM_VERSION_FLOOR}`",
        )
    ]


# The repo-wide recipe index, built in one scan:
#   identifiers   set of every recipe Identifier
#   by_path       normalized relative path -> Identifier (resolves a path-like
#                 ParentRecipe back to the identifier it should have been)
#   by_identifier Identifier -> list of paths that declare it (for W112 dupes)
#   parent_of     Identifier -> its ParentRecipe resolved to an Identifier (or
#                 None), the parent graph W115 walks to find cycles
RecipeIndex = collections.namedtuple(
    "RecipeIndex",
    [
        "identifiers",
        "by_path",
        "by_identifier",
        "parent_of",
        "processors",
        "by_description",
    ],
)


def discover_processor_refs(root="."):
    """Return the set of `com.github.jgstew.<folder>/<Name>` refs that exist.

    One entry per `<Name>.py` in each PROCESSOR_FOLDERS directory (skipping
    dunder/underscore-prefixed files). Used by W118 to tell a real shared-
    processor reference from a typo / rename / mislabeled core processor.
    """
    refs = set()
    for folder in PROCESSOR_FOLDERS:
        try:
            names = os.listdir(os.path.join(root, folder))
        except OSError:
            continue
        for name in names:
            if name.endswith(".py") and not name.startswith("_"):
                refs.add(f"{RECIPE_IDENTIFIER_PREFIX}{folder}/{name[:-3]}")
    return refs


def build_recipe_index(root="."):
    """Return a RecipeIndex of every recipe under `root`.

    Scans all recipe files (regardless of which files are being checked) so the
    cross-file checks resolve against the whole repo even when pre-commit passes
    only the changed files. Files that fail to parse or lack a string Identifier
    simply do not contribute; the skip marker does not exclude a file here (it
    still defines an identifier a parent may legitimately point at). Done in two
    passes so parent resolution (which needs `by_path`) sees every recipe.
    """
    identifiers = set()
    by_path = {}
    by_identifier = collections.defaultdict(list)
    by_description = collections.defaultdict(list)
    raw_parents = []  # (norm_path, identifier, ParentRecipe value)
    for path in discover_recipe_files(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                src = handle.read()
        except OSError:
            continue
        data, _ = parse_recipe(src)
        if not isinstance(data, dict):
            continue
        norm = os.path.normpath(path)
        desc = data.get("Description")
        if isinstance(desc, str) and desc.strip():
            by_description[normalize_description(desc)].append(norm)
        if isinstance(data.get("Identifier"), str):
            identifier = data["Identifier"]
            identifiers.add(identifier)
            by_path[norm] = identifier
            by_identifier[identifier].append(norm)
            raw_parents.append((norm, identifier, data.get("ParentRecipe")))

    parent_of = {}
    for norm, identifier, parent_value in raw_parents:
        parent_of[identifier] = resolve_to_identifier(
            norm, parent_value, by_path, identifiers
        )
    return RecipeIndex(
        identifiers,
        by_path,
        dict(by_identifier),
        parent_of,
        discover_processor_refs(root),
        dict(by_description),
    )


def resolve_to_identifier(recipe_path, parent_value, by_path, identifiers):
    """Resolve a ParentRecipe value to an Identifier, or None.

    An exact Identifier match wins; otherwise the value is treated as a path (see
    resolve_parent_path_to_identifier). Returns None for a missing/non-string
    value or one that resolves to nothing.
    """
    if not isinstance(parent_value, str) or not parent_value:
        return None
    if parent_value in identifiers:
        return parent_value
    return resolve_parent_path_to_identifier(recipe_path, parent_value, by_path)


def resolve_parent_path_to_identifier(recipe_path, parent_value, by_path):
    """Return the Identifier the path-like `parent_value` points at, or None.

    Tries the value as a path relative to the working directory and relative to
    the recipe's own folder; failing that, falls back to a unique basename match
    among indexed recipes. Returns None when nothing (or more than one thing)
    matches, so the fixer leaves an unresolvable value alone for a human.
    """
    candidates = [
        os.path.normpath(parent_value),
        os.path.normpath(os.path.join(os.path.dirname(recipe_path), parent_value)),
    ]
    for candidate in candidates:
        if candidate in by_path:
            return by_path[candidate]

    base = os.path.basename(parent_value)
    matches = {
        identifier
        for path, identifier in by_path.items()
        if os.path.basename(path) == base
    }
    if len(matches) == 1:
        return next(iter(matches))
    return None


def apply_parent_recipe_fix(path, new_identifier):
    """Rewrite the file's ParentRecipe value in place to `new_identifier`.

    Handles both YAML (`ParentRecipe: <value>`, preserving indentation and any
    trailing comment) and plist (`<key>ParentRecipe</key><string>...</string>`).
    Only the value is changed; nothing else in the file is touched.
    """
    with open(path, encoding="utf-8") as handle:
        src = handle.read()

    if src.lstrip().startswith("<"):
        new_src = re.sub(
            r"(<key>ParentRecipe</key>\s*<string>)(.*?)(</string>)",
            lambda m: m.group(1) + new_identifier + m.group(3),
            src,
            count=1,
            flags=re.DOTALL,
        )
    else:
        lines = src.split("\n")
        for i, line in enumerate(lines):
            # group1 = key/colon/spaces, group2 = the (space-free) value token,
            # group3 = any trailing whitespace/comment to preserve verbatim
            match = re.match(r"^(\s*ParentRecipe\s*:\s*)(\S+)(.*)$", line)
            if match:
                lines[i] = match.group(1) + new_identifier + match.group(3)
                break
        new_src = "\n".join(lines)

    with open(path, "w", encoding="utf-8") as handle:
        handle.write(new_src)


def maybe_fix_parent_recipe(path, recipe, index):
    """Auto-fix W111: rewrite a path-like ParentRecipe to the parent's Identifier.

    Acts only when the ParentRecipe value is not already a known Identifier AND it
    resolves (as a path) to a recipe whose Identifier we can read. Anything not so
    resolvable is left untouched (and stays reported as W111). Returns the list of
    fixed entries (empty when nothing was fixed).
    """
    parent = recipe.get("ParentRecipe")
    if not isinstance(parent, str) or not parent:
        return []
    if parent in index.identifiers:
        return []  # already a valid identifier; nothing to fix
    new_identifier = resolve_parent_path_to_identifier(path, parent, index.by_path)
    if not new_identifier or new_identifier == parent:
        return []  # cannot resolve to a known identifier -> leave for a human

    with open(path, encoding="utf-8", errors="replace") as handle:
        source_lines = handle.read().splitlines()
    lineno = find_line(
        source_lines, r"^\s*ParentRecipe\s*:", r"<key>ParentRecipe</key>"
    )
    apply_parent_recipe_fix(path, new_identifier)
    return [
        (
            lineno,
            "W111",
            f"rewrote ParentRecipe path `{parent}` to identifier `{new_identifier}`",
        )
    ]


# --- W120: one blank line between Process steps -----------------------------
# Convention: within `Process:`, consecutive `- Processor:` steps are separated
# by exactly one blank line, and that blank line goes BEFORE any comment lines
# that belong to (immediately precede) the next step -- not between those
# comments and their step. More than one blank line collapses to one; a missing
# blank line is inserted. This is purely textual (it does not reparse the YAML),
# so it is YAML-only and left untouched on plist recipes.


def _is_comment_line(line):
    """True if `line` is a comment-only line (first non-space char is `#`)."""
    return line.lstrip().startswith("#")


def _is_blank_line(line):
    """True if `line` is empty or whitespace-only."""
    return line.strip() == ""


def find_process_block(lines):
    """Return (start, end) bounding the `Process:` value, or None.

    `start` is the index of the `Process:` line; `end` is the exclusive index of
    the first line that ends the block -- a non-blank line at column 0 that is
    not a comment (i.e. the next top-level key). Column-0 comments and blank
    lines stay inside the block.
    """
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^Process\s*:", line):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.strip() == "":
            continue
        if not line[0].isspace() and not line.lstrip().startswith("#"):
            end = j
            break
    return start, end


def process_step_indices(lines):
    """Return (step_line_indices, item_indent) for the Process list.

    A step is a list item (`- `) at the Process list's own indentation, so the
    nested `- ` items of an array argument (which are more indented) are not
    mistaken for steps. Returns ([], None) when there is no Process list.
    """
    block = find_process_block(lines)
    if block is None:
        return [], None
    start, end = block
    item_indent = None
    for j in range(start + 1, end):
        match = re.match(r"^(\s+)-\s", lines[j])
        if match:
            item_indent = match.group(1)
            break
    if item_indent is None:
        return [], None
    step_re = re.compile(r"^" + re.escape(item_indent) + r"-\s")
    steps = [j for j in range(start + 1, end) if step_re.match(lines[j])]
    return steps, item_indent


def normalize_process_spacing(lines):
    """Return (new_lines, changed_linenos) with Process-step spacing normalized.

    For each step after the first, the run of blank/comment lines directly above
    it (the "gap") is rewritten to exactly one blank line, with the comment lines
    split by indentation around it:

      * comment lines indented DEEPER than the `- ` item indent are commented-out
        body of the PREVIOUS step (e.g. `    # Arguments:` / `      # curl_opts`),
        so they stay attached to it -- the blank line goes AFTER them;
      * comment lines at (or shallower than) the item indent are leading comments
        for the NEXT step, so the blank line goes BEFORE them.

    A gap that contains a commented-out step (`# - ...`) is left untouched: it is
    a disabled step block whose surrounding spacing is intentional/ambiguous. The
    scan stops at the previous step's last non-blank/non-comment line, so a step's
    own body is never touched. `changed_linenos` are the 1-based line numbers (in
    the input) of the steps whose gap was rewritten. Pairs are processed
    bottom-to-top so earlier indices stay valid as lines shift below them.
    """
    steps, item_indent = process_step_indices(lines)
    if len(steps) < 2:
        return lines, []
    indent_width = len(item_indent)
    commented_step_re = re.compile(r"^\s*#\s*-\s")
    new = list(lines)
    changed = []
    for k in range(len(steps) - 1, 0, -1):
        step = steps[k]
        gap_start = step
        i = step - 1
        while i >= 0 and (_is_blank_line(new[i]) or _is_comment_line(new[i])):
            gap_start = i
            i -= 1
        gap = new[gap_start:step]
        if any(commented_step_re.match(line) for line in gap):
            continue  # disabled step block -> leave its spacing alone
        prev_comments = []  # commented-out body of the previous step
        next_comments = []  # leading comments for the next step
        for line in gap:
            if not _is_comment_line(line):
                continue  # blank lines are dropped and re-inserted as the one
            indent = len(line) - len(line.lstrip())
            if indent > indent_width:
                prev_comments.append(line)
            else:
                next_comments.append(line)
        replacement = prev_comments + [""] + next_comments
        if gap != replacement:
            new[gap_start:step] = replacement
            changed.append(step + 1)
    changed.sort()
    return new, changed


def check_process_spacing(src):
    """W120: report each Process step whose preceding blank-line spacing is off.

    Purely textual and YAML-only (skipped for plist recipes). Reports at the
    step's line; the fix (maybe_fix_process_spacing) does the rewrite.
    """
    if src.lstrip().startswith("<"):
        return []
    _, changed = normalize_process_spacing(src.split("\n"))
    return [
        (
            lineno,
            "W120",
            "expected exactly one blank line before this Processor step (placed "
            "before any comments that belong to it)",
        )
        for lineno in changed
    ]


def maybe_fix_process_spacing(path):
    """Auto-fix W120: rewrite Process-step blank-line spacing in place.

    YAML-only. Preserves the file's trailing-newline state and only rewrites the
    gaps between steps. Returns the list of fixed entries (empty if unchanged).
    """
    with open(path, encoding="utf-8", errors="replace") as handle:
        src = handle.read()
    if src.lstrip().startswith("<"):
        return []
    trailing_nl = src.endswith("\n")
    body = src[:-1] if trailing_nl else src
    new_lines, changed = normalize_process_spacing(body.split("\n"))
    if not changed:
        return []
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(new_lines) + ("\n" if trailing_nl else ""))
    return [
        (
            lineno,
            "W120",
            "normalized blank-line spacing before this Processor step",
        )
        for lineno in changed
    ]


def check_file(
    path,
    strict=False,
    exact=False,
    disabled=frozenset(),
    recipe_index=None,
    auto_fix=False,
):
    """Check one recipe file; return (issues, fixed).

    Each of `issues` and `fixed` is a list of (lineno, check_id, message).
    `strict` and `exact` tune the flagship check (see module docstring).
    `disabled` is a set of check IDs to skip. `recipe_index` is the repo-wide
    RecipeIndex used by the ParentRecipe check/fix (W111); when None, W111 is
    skipped. When `auto_fix` is set, the fixable conventions (a path-like
    ParentRecipe -> its Identifier, W111; and Process-step blank-line spacing,
    W120) are rewritten in place and reported under `fixed` instead of `issues`.
    """
    if not os.path.isfile(path):
        return [(1, "W100", "file not found; skipping")], []

    with open(path, encoding="utf-8", errors="replace") as handle:
        src = handle.read()

    if SKIP_MARKER in src:
        return [], []

    data, warning = parse_recipe(src)
    if warning is not None:
        return [(1, warning[0], warning[1])], []
    if not isinstance(data, dict):
        return [(1, "W102", "top-level of recipe is not a mapping; skipping")], []

    fixed = []
    parent_check_enabled = (
        "W111" not in disabled
        and recipe_index is not None
        and PARENT_RECIPE_MARKER not in src
    )
    is_yaml = not src.lstrip().startswith("<")
    spacing_check_enabled = (
        "W120" not in disabled and is_yaml and PROCESS_SPACING_MARKER not in src
    )
    # W114 is YAML-only: plist recipes are legacy and intentionally kept at their
    # original (below-floor) MinimumVersion, so they are not flagged or bumped.
    minver_check_enabled = (
        "W114" not in disabled and is_yaml and MINIMUM_VERSION_MARKER not in src
    )

    # --- auto-fixes: rewrite the file, then re-read (and re-parse) so the checks
    # below see the corrected content and do not re-report what was just fixed. ---
    if auto_fix and parent_check_enabled:
        fixed += maybe_fix_parent_recipe(path, data, recipe_index)
    if auto_fix and minver_check_enabled:
        fixed += maybe_fix_minimum_version(path, data)
    if auto_fix and spacing_check_enabled:
        fixed += maybe_fix_process_spacing(path)
    if fixed:
        with open(path, encoding="utf-8", errors="replace") as handle:
            src = handle.read()
        data, _ = parse_recipe(src)
        if not isinstance(data, dict):
            return [], fixed

    source_lines = src.splitlines()
    issues = []

    if "W110" not in disabled and IDENTIFIER_NAME_MARKER not in src:
        issues += check_identifier_references_name(data, source_lines, exact)

    if parent_check_enabled:
        issues += check_parent_recipe_resolvable(
            data, source_lines, recipe_index.identifiers
        )

    if (
        "W112" not in disabled
        and recipe_index is not None
        and DUPLICATE_IDENTIFIER_MARKER not in src
    ):
        issues += check_duplicate_identifier(data, path, source_lines, recipe_index)

    if (
        "W123" not in disabled
        and recipe_index is not None
        and DUPLICATE_DESCRIPTION_MARKER not in src
    ):
        issues += check_duplicate_description(data, path, source_lines, recipe_index)

    if "W113" not in disabled and TYPE_MISMATCH_MARKER not in src:
        issues += check_filename_identifier_type(data, path, source_lines)

    if minver_check_enabled:
        issues += check_minimum_version(data, source_lines)

    if (
        "W115" not in disabled
        and recipe_index is not None
        and PARENT_RECIPE_MARKER not in src
    ):
        issues += check_parent_cycle(data, path, source_lines, recipe_index)

    if "W116" not in disabled and HTTP_URL_MARKER not in src:
        issues += check_http_urls(data, source_lines)

    if "W117" not in disabled and REGEX_MARKER not in src:
        issues += check_regex_arguments(data, source_lines)

    if (
        "W118" not in disabled
        and recipe_index is not None
        and PROCESSOR_REF_MARKER not in src
    ):
        issues += check_processor_refs_exist(data, source_lines, recipe_index)

    if "W121" not in disabled and RECIPE_NAME_MARKER not in src:
        issues += check_recipe_name_segments(data, source_lines)

    if "W122" not in disabled and RECIPE_NAME_SUFFIX_MARKER not in src:
        issues += check_recipe_name_suffix(data, source_lines)

    if spacing_check_enabled:
        issues += check_process_spacing(src)

    return sorted(issues), fixed


def is_recipe_file(path):
    """True if `path` has one of the recognized recipe extensions."""
    return path.endswith(RECIPE_EXTENSIONS)


def check_files(
    paths, strict=False, exact=False, disabled=frozenset(), auto_fix=False, root="."
):
    """Check several recipe files; return a list of (path, issues, fixed) tuples.

    Non-recipe paths are skipped. Disabled codes are filtered from the results.
    The repo-wide RecipeIndex (for the cross-file checks W111/W112/W115) is built
    once here from `root`, unless all of those are disabled. This is the
    programmatic entry point: it does no printing, so other code can consume the
    structured results. `main()` wraps it to print and exit.
    """
    recipe_index = None
    if {"W111", "W112", "W115", "W118", "W123"} - disabled:
        recipe_index = build_recipe_index(root)

    results = []
    for path in paths:
        if not is_recipe_file(path):
            continue
        issues, fixed = check_file(
            path,
            strict=strict,
            exact=exact,
            disabled=disabled,
            recipe_index=recipe_index,
            auto_fix=auto_fix,
        )
        issues = [item for item in issues if item[1] not in disabled]
        fixed = [item for item in fixed if item[1] not in disabled]
        results.append((path, issues, fixed))
    return results


def discover_recipe_files(root="."):
    """Return all recipe files under `root`.

    Hidden directories and common noise (__pycache__, node_modules) are pruned.
    Unlike the processor checker there is no depth limit -- recipes live at
    varying depths (e.g. BigFix/QnA.pkg.recipe, Test-Recipes/Foo.test.recipe.yaml).
    """
    skip_dirs = {"__pycache__", "node_modules"}
    root = os.path.normpath(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in skip_dirs
        ]
        for name in filenames:
            if is_recipe_file(name):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def main(argv=None):
    """Execution starts here.

    argv defaults to None so this works both as a console_scripts entry point
    (pre-commit calls it with no arguments; argparse then reads sys.argv) and
    when called directly as `main(sys.argv[1:])`.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as failures (non-zero exit); default: advisory",
    )
    parser.add_argument(
        "--exact",
        action="store_true",
        help="require the Input NAME to be a literal substring of the Identifier "
        "instead of the default normalized comparison",
    )
    parser.add_argument(
        "--auto-fix",
        choices=["yes", "no"],
        default=None,
        help="rewrite a path-like ParentRecipe (W111) to the parent's Identifier "
        "in place (default: yes when files are given, no when auto-discovering)",
    )
    parser.add_argument(
        "--disable",
        default="",
        metavar="CODES",
        help="comma-separated check IDs to skip entirely, e.g. --disable W110",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="recipe files to check; if omitted, all recipe files in the current "
        "folder and below are checked",
    )
    args = parser.parse_args(argv)

    disabled = {
        code.strip().upper() for code in args.disable.split(",") if code.strip()
    }
    unknown = disabled - KNOWN_CODES
    if unknown:
        print(
            f"warning: ignoring unknown --disable code(s): {', '.join(sorted(unknown))}"
        )

    # auto-fix defaults to yes for explicit files, no when auto-discovering; an
    # explicit --auto-fix always wins.
    discovering = not args.files
    if args.auto_fix is not None:
        auto_fix = args.auto_fix == "yes"
    else:
        auto_fix = not discovering
    paths = args.files if args.files else discover_recipe_files(".")

    warning_count = 0
    fix_count = 0
    for path, issues, fixed in check_files(
        paths,
        strict=args.strict,
        exact=args.exact,
        disabled=disabled,
        auto_fix=auto_fix,
    ):
        for lineno, check_id, message in fixed:
            fix_count += 1
            print(f"{path}:{lineno}: [{check_id}] auto-fixed: {message}")
        for lineno, check_id, message in issues:
            warning_count += 1
            print(f"{path}:{lineno}: [{check_id}] warning: {message}")

    if fix_count:
        print(f"\nauto-fixed {fix_count} issue(s); review and re-stage the changes.")
    if warning_count:
        print(f"{warning_count} recipe-convention warning(s).")
    # a fix always fails the hook (so the changes are reviewed and re-staged);
    # warnings fail only under --strict
    return 1 if (fix_count or (warning_count and args.strict)) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
