#!/usr/bin/env python3
"""Tests for pre_commit_hooks/autopkg_recipe_check_conventions.py.

These exercise the recipe convention checks (W100-W123), their auto-fixers, the
file-level opt-out markers, and main()'s exit codes. Cross-file checks (duplicate
Identifier/Description, ParentRecipe resolvability/cycles, shared-processor
references) are driven through check_files(..., root=tmp_path), which builds the
repo-wide index from that root; main() builds the index from the cwd, so the few
main() tests chdir into tmp_path.
"""

import pytest

from pre_commit_hooks import autopkg_recipe_check_conventions as checker

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Description</key><string>Downloads QnA</string>
  <key>Identifier</key><string>com.github.jgstew.pkg.QnA</string>
  <key>Input</key><dict><key>NAME</key><string>QnA</string></dict>
  <key>MinimumVersion</key><string>1.0</string>
</dict></plist>
"""


def rc(
    name="Foo",
    identifier=None,
    description=None,
    minver="2.4.1",
    parent=None,
    process=None,
    marker=None,
):
    """Return YAML recipe text. `marker` inserts a `# <marker>` opt-out comment."""
    if identifier is None:
        identifier = f"com.github.jgstew.download.{name}"
    if description is None:
        description = f"Downloads the latest version of {name}"
    lines = ["---"]
    if marker:
        lines.append(f"# {marker}")
    lines += [f"Description: {description}", f"Identifier: {identifier}"]
    if parent is not None:
        lines.append(f"ParentRecipe: {parent}")
    lines += ["Input:", f"  NAME: {name}", f'MinimumVersion: "{minver}"']
    lines += (
        process
        if process is not None
        else ["Process:", "  - Processor: URLDownloaderPython"]
    )
    return "\n".join(lines) + "\n"


def write(tmp_path, relpath, content):
    """Write `content` to tmp_path/relpath (creating parents); return the path str."""
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def codes(issues):
    """Return the set of check-id codes from a list of (lineno, code, msg)."""
    return {code for _lineno, code, _msg in issues}


def check(tmp_path, target, **kw):
    """Run check_files on `target` (an abs path) with root=tmp_path; (issues, fixed)."""
    kw.setdefault("root", str(tmp_path))
    results = checker.check_files([target], **kw)
    return (results[0][1], results[0][2]) if results else ([], [])


# --------------------------------------------------------------------------- #
# helpers: is_recipe_file / parse_recipe
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name,expected",
    [
        ("Foo.download.recipe.yaml", True),
        ("Foo.download.recipe.yml", True),
        ("QnA.pkg.recipe", True),
        ("notes.txt", False),
        ("Foo.py", False),
    ],
)
def test_is_recipe_file(name, expected):
    assert checker.is_recipe_file(name) is expected


def test_parse_recipe_yaml():
    data, warning = checker.parse_recipe(rc(name="Foo"))
    assert warning is None
    assert data["Identifier"] == "com.github.jgstew.download.Foo"


def test_parse_recipe_plist():
    data, warning = checker.parse_recipe(PLIST)
    assert warning is None
    assert data["Identifier"] == "com.github.jgstew.pkg.QnA"


def test_parse_recipe_bad_yaml_returns_w100():
    data, warning = checker.parse_recipe("Description: [unclosed\n")
    assert data is None
    assert warning[0] == "W100"


def test_non_mapping_reports_w102(tmp_path):
    path = write(tmp_path, "List.download.recipe.yaml", "- a\n- b\n")
    issues, _fixed = check(tmp_path, path)
    assert "W102" in codes(issues)


# --------------------------------------------------------------------------- #
# W110: Identifier should reference the Input NAME
# --------------------------------------------------------------------------- #
def test_w110_normalized_pass(tmp_path):
    path = write(tmp_path, "Foo.download.recipe.yaml", rc(name="Foo"))
    issues, _ = check(tmp_path, path)
    assert "W110" not in codes(issues)


def test_w110_mismatch_flagged(tmp_path):
    src = rc(name="Foo", identifier="com.github.jgstew.download.Totally-Different")
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    issues, _ = check(tmp_path, path)
    assert "W110" in codes(issues)


def test_w110_exact_mode_flags_punctuation(tmp_path):
    # normalized passes, but --exact requires a literal substring
    src = rc(name="FirefoxWin", identifier="com.github.jgstew.download.Firefox-Win")
    path = write(tmp_path, "Firefox-Win.download.recipe.yaml", src)
    assert "W110" not in codes(check(tmp_path, path)[0])
    assert "W110" in codes(check(tmp_path, path, exact=True)[0])


def test_w110_marker_suppresses(tmp_path):
    src = rc(
        name="Foo",
        identifier="com.github.jgstew.download.Totally-Different",
        marker="identifier-name-ok",
    )
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W110" not in codes(check(tmp_path, path)[0])


# --------------------------------------------------------------------------- #
# W111: ParentRecipe must resolve to a known Identifier (auto-fixable)
# --------------------------------------------------------------------------- #
def test_w111_resolvable_parent_ok(tmp_path):
    write(tmp_path, "Bar.download.recipe.yaml", rc(name="Bar"))
    child = write(
        tmp_path,
        "Bar.pkg.recipe.yaml",
        rc(
            name="Bar",
            identifier="com.github.jgstew.pkg.Bar",
            parent="com.github.jgstew.download.Bar",
        ),
    )
    assert "W111" not in codes(check(tmp_path, child)[0])


def test_w111_unknown_parent_flagged(tmp_path):
    child = write(
        tmp_path,
        "Bar.pkg.recipe.yaml",
        rc(
            name="Bar",
            identifier="com.github.jgstew.pkg.Bar",
            parent="com.github.jgstew.download.DoesNotExist",
        ),
    )
    assert "W111" in codes(check(tmp_path, child)[0])


def test_w111_path_parent_autofixed_to_identifier(tmp_path):
    write(tmp_path, "Bar.download.recipe.yaml", rc(name="Bar"))
    child = write(
        tmp_path,
        "Bar.pkg.recipe.yaml",
        rc(
            name="Bar",
            identifier="com.github.jgstew.pkg.Bar",
            parent="Bar.download.recipe.yaml",
        ),  # a path, not an identifier
    )
    _issues, fixed = check(tmp_path, child, auto_fix=True)
    assert "W111" in codes(fixed)
    assert "ParentRecipe: com.github.jgstew.download.Bar" in open(child).read()


def test_w111_marker_suppresses(tmp_path):
    child = write(
        tmp_path,
        "Bar.pkg.recipe.yaml",
        rc(
            name="Bar",
            identifier="com.github.jgstew.pkg.Bar",
            parent="com.github.jgstew.download.Elsewhere",
            marker="parent-recipe-ok",
        ),
    )
    assert "W111" not in codes(check(tmp_path, child)[0])


# --------------------------------------------------------------------------- #
# W112: duplicate Identifier across the repo
# --------------------------------------------------------------------------- #
def test_w112_duplicate_identifier_flagged(tmp_path):
    ident = "com.github.jgstew.download.Dup"
    write(tmp_path, "A.download.recipe.yaml", rc(name="Dup", identifier=ident))
    b = write(tmp_path, "B.download.recipe.yaml", rc(name="Dup", identifier=ident))
    assert "W112" in codes(check(tmp_path, b)[0])


def test_w112_marker_suppresses(tmp_path):
    ident = "com.github.jgstew.download.Dup"
    write(tmp_path, "A.download.recipe.yaml", rc(name="Dup", identifier=ident))
    b = write(
        tmp_path,
        "B.download.recipe.yaml",
        rc(name="Dup", identifier=ident, marker="duplicate-identifier-ok"),
    )
    assert "W112" not in codes(check(tmp_path, b)[0])


# --------------------------------------------------------------------------- #
# W113: filename type-infix must match the identifier type-segment
# --------------------------------------------------------------------------- #
def test_w113_type_mismatch_flagged(tmp_path):
    # filename type "download" vs identifier type "pkg"
    src = rc(name="Foo", identifier="com.github.jgstew.pkg.Foo")
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W113" in codes(check(tmp_path, path)[0])


def test_w113_match_ok(tmp_path):
    path = write(tmp_path, "Foo.download.recipe.yaml", rc(name="Foo"))
    assert "W113" not in codes(check(tmp_path, path)[0])


def test_w113_marker_suppresses(tmp_path):
    src = rc(
        name="Foo", identifier="com.github.jgstew.pkg.Foo", marker="type-mismatch-ok"
    )
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W113" not in codes(check(tmp_path, path)[0])


# --------------------------------------------------------------------------- #
# W114: MinimumVersion floor (YAML-only; auto-fixable)
# --------------------------------------------------------------------------- #
def test_w114_below_floor_flagged(tmp_path):
    path = write(tmp_path, "Foo.download.recipe.yaml", rc(name="Foo", minver="1.0"))
    assert "W114" in codes(check(tmp_path, path, auto_fix=False)[0])


def test_w114_autofix_raises_to_floor(tmp_path):
    path = write(tmp_path, "Foo.download.recipe.yaml", rc(name="Foo", minver="1.0"))
    _issues, fixed = check(tmp_path, path, auto_fix=True)
    assert "W114" in codes(fixed)
    assert 'MinimumVersion: "2.4.1"' in open(path).read()


def test_w114_plist_skipped(tmp_path):
    # plist recipe with MinimumVersion 1.0 -> W114 is YAML-only, must not fire
    path = write(tmp_path, "QnA.pkg.recipe", PLIST)
    assert "W114" not in codes(check(tmp_path, path)[0])


def test_w114_marker_suppresses(tmp_path):
    src = rc(name="Foo", minver="1.0", marker="minimum-version-ok")
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W114" not in codes(check(tmp_path, path, auto_fix=False)[0])


# --------------------------------------------------------------------------- #
# W115: cyclic / self-referential ParentRecipe
# --------------------------------------------------------------------------- #
def test_w115_self_cycle_flagged(tmp_path):
    ident = "com.github.jgstew.download.Self"
    path = write(
        tmp_path,
        "Self.download.recipe.yaml",
        rc(name="Self", identifier=ident, parent=ident),
    )
    assert "W115" in codes(check(tmp_path, path)[0])


def test_w115_two_node_cycle_flagged(tmp_path):
    write(
        tmp_path,
        "A.download.recipe.yaml",
        rc(
            name="A",
            identifier="com.github.jgstew.download.A",
            parent="com.github.jgstew.download.B",
        ),
    )
    a = tmp_path / "A.download.recipe.yaml"
    write(
        tmp_path,
        "B.download.recipe.yaml",
        rc(
            name="B",
            identifier="com.github.jgstew.download.B",
            parent="com.github.jgstew.download.A",
        ),
    )
    assert "W115" in codes(check(tmp_path, str(a))[0])


# --------------------------------------------------------------------------- #
# W116: prefer https over http
# --------------------------------------------------------------------------- #
HTTP_PROCESS = [
    "Process:",
    "  - Processor: URLDownloaderPython",
    "    Arguments:",
    "      url: http://example.com/foo.zip",
]
HTTPS_PROCESS = [
    "Process:",
    "  - Processor: URLDownloaderPython",
    "    Arguments:",
    "      url: https://example.com/foo.zip",
]


def test_w116_http_flagged(tmp_path):
    path = write(
        tmp_path, "Foo.download.recipe.yaml", rc(name="Foo", process=HTTP_PROCESS)
    )
    assert "W116" in codes(check(tmp_path, path)[0])


def test_w116_https_ok(tmp_path):
    path = write(
        tmp_path, "Foo.download.recipe.yaml", rc(name="Foo", process=HTTPS_PROCESS)
    )
    assert "W116" not in codes(check(tmp_path, path)[0])


def test_w116_marker_suppresses(tmp_path):
    src = rc(name="Foo", process=HTTP_PROCESS, marker="http-url-ok")
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W116" not in codes(check(tmp_path, path)[0])


# --------------------------------------------------------------------------- #
# W117: re_pattern / asset_regex must be valid regexes
# --------------------------------------------------------------------------- #
BAD_RE = [
    "Process:",
    "  - Processor: URLTextSearcher",
    "    Arguments:",
    "      re_pattern: '([unclosed'",
]
GOOD_RE = [
    "Process:",
    "  - Processor: URLTextSearcher",
    "    Arguments:",
    "      re_pattern: 'foo-.*.zip'",
]


def test_w117_invalid_regex_flagged(tmp_path):
    path = write(tmp_path, "Foo.download.recipe.yaml", rc(name="Foo", process=BAD_RE))
    assert "W117" in codes(check(tmp_path, path)[0])


def test_w117_valid_regex_ok(tmp_path):
    path = write(tmp_path, "Foo.download.recipe.yaml", rc(name="Foo", process=GOOD_RE))
    assert "W117" not in codes(check(tmp_path, path)[0])


def test_w117_marker_suppresses(tmp_path):
    src = rc(name="Foo", process=BAD_RE, marker="regex-ok")
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W117" not in codes(check(tmp_path, path)[0])


# --------------------------------------------------------------------------- #
# W118: a com.github.jgstew.Shared*Processors/X step must map to a real file
# --------------------------------------------------------------------------- #
def _proc_step(ref):
    return ["Process:", f"  - Processor: {ref}"]


def test_w118_missing_ref_flagged(tmp_path):
    src = rc(name="Foo", process=_proc_step("com.github.jgstew.SharedProcessors/Nope"))
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W118" in codes(check(tmp_path, path)[0])


def test_w118_existing_ref_ok(tmp_path):
    write(tmp_path, "SharedProcessors/RealProc.py", "# a processor file\n")
    src = rc(
        name="Foo", process=_proc_step("com.github.jgstew.SharedProcessors/RealProc")
    )
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W118" not in codes(check(tmp_path, path)[0])


def test_w118_marker_suppresses(tmp_path):
    src = rc(
        name="Foo",
        process=_proc_step("com.github.jgstew.SharedProcessors/Nope"),
        marker="processor-ref-ok",
    )
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W118" not in codes(check(tmp_path, path)[0])


# --------------------------------------------------------------------------- #
# W120: exactly one blank line between Process steps (YAML-only; auto-fixable)
# --------------------------------------------------------------------------- #
TWO_STEPS_NO_BLANK = [
    "Process:",
    "  - Processor: URLDownloaderPython",
    "  - Processor: EndOfCheckPhase",
]


def test_w120_missing_blank_detected(tmp_path):
    src = rc(name="Foo", process=TWO_STEPS_NO_BLANK)
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W120" in codes(check(tmp_path, path, auto_fix=False)[0])


def test_w120_autofix_inserts_blank(tmp_path):
    src = rc(name="Foo", process=TWO_STEPS_NO_BLANK)
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    _issues, fixed = check(tmp_path, path, auto_fix=True)
    assert "W120" in codes(fixed)
    result = open(path).read()
    assert (
        "  - Processor: URLDownloaderPython\n\n  - Processor: EndOfCheckPhase" in result
    )


def test_w120_marker_suppresses(tmp_path):
    src = rc(name="Foo", process=TWO_STEPS_NO_BLANK, marker="process-spacing-ok")
    path = write(tmp_path, "Foo.download.recipe.yaml", src)
    assert "W120" not in codes(check(tmp_path, path, auto_fix=False)[0])


# --------------------------------------------------------------------------- #
# W121: Input NAME segments are letter-start alphanumeric (dash-separated)
# --------------------------------------------------------------------------- #
def test_w121_underscore_flagged(tmp_path):
    src = rc(name="Foo_Bar", identifier="com.github.jgstew.download.Foo_Bar")
    path = write(tmp_path, "Foo_Bar.download.recipe.yaml", src)
    assert "W121" in codes(check(tmp_path, path)[0])


def test_w121_dashed_camel_ok(tmp_path):
    src = rc(name="Firefox-Win", identifier="com.github.jgstew.download.Firefox-Win")
    path = write(tmp_path, "Firefox-Win.download.recipe.yaml", src)
    assert "W121" not in codes(check(tmp_path, path)[0])


def test_w121_marker_suppresses(tmp_path):
    src = rc(
        name="Foo_Bar",
        identifier="com.github.jgstew.download.Foo_Bar",
        marker="recipe-name-ok",
    )
    path = write(tmp_path, "Foo_Bar.download.recipe.yaml", src)
    assert "W121" not in codes(check(tmp_path, path)[0])


# --------------------------------------------------------------------------- #
# W122: trailing platform/arch suffix must be canonical casing
# --------------------------------------------------------------------------- #
def test_w122_noncanonical_suffix_flagged(tmp_path):
    src = rc(name="Foo-mac", identifier="com.github.jgstew.download.Foo-mac")
    path = write(tmp_path, "Foo-mac.download.recipe.yaml", src)
    assert "W122" in codes(check(tmp_path, path)[0])


def test_w122_canonical_suffix_ok(tmp_path):
    src = rc(name="Foo-Mac", identifier="com.github.jgstew.download.Foo-Mac")
    path = write(tmp_path, "Foo-Mac.download.recipe.yaml", src)
    assert "W122" not in codes(check(tmp_path, path)[0])


def test_w122_non_platform_suffix_ok(tmp_path):
    # a trailing segment that is not a known platform token is left alone
    src = rc(name="tty-share", identifier="com.github.jgstew.download.tty-share")
    path = write(tmp_path, "tty-share.download.recipe.yaml", src)
    assert "W122" not in codes(check(tmp_path, path)[0])


def test_w122_marker_suppresses(tmp_path):
    src = rc(
        name="Foo-mac",
        identifier="com.github.jgstew.download.Foo-mac",
        marker="recipe-name-suffix-ok",
    )
    path = write(tmp_path, "Foo-mac.download.recipe.yaml", src)
    assert "W122" not in codes(check(tmp_path, path)[0])


# --------------------------------------------------------------------------- #
# W123: Description must be distinct across the repo
# --------------------------------------------------------------------------- #
def test_w123_duplicate_description_flagged(tmp_path):
    write(
        tmp_path,
        "A.download.recipe.yaml",
        rc(name="Aye", description="Same words here."),
    )
    b = write(
        tmp_path,
        "B.download.recipe.yaml",
        rc(name="Bee", description="Same words here."),
    )
    assert "W123" in codes(check(tmp_path, b)[0])


def test_w123_distinct_description_ok(tmp_path):
    write(
        tmp_path,
        "A.download.recipe.yaml",
        rc(name="Aye", description="First distinct desc."),
    )
    b = write(
        tmp_path,
        "B.download.recipe.yaml",
        rc(name="Bee", description="Second distinct desc."),
    )
    assert "W123" not in codes(check(tmp_path, b)[0])


def test_w123_marker_suppresses(tmp_path):
    write(
        tmp_path,
        "A.download.recipe.yaml",
        rc(name="Aye", description="Same words here."),
    )
    b = write(
        tmp_path,
        "B.download.recipe.yaml",
        rc(
            name="Bee",
            description="Same words here.",
            marker="duplicate-description-ok",
        ),
    )
    assert "W123" not in codes(check(tmp_path, b)[0])


# --------------------------------------------------------------------------- #
# check_files: non-recipe paths skipped
# --------------------------------------------------------------------------- #
def test_check_files_skips_non_recipe(tmp_path):
    txt = write(tmp_path, "notes.txt", "hello\n")
    assert checker.check_files([txt], root=str(tmp_path)) == []


# --------------------------------------------------------------------------- #
# main() exit codes (main builds the index from cwd, so chdir into tmp_path)
# --------------------------------------------------------------------------- #
def test_main_clean_returns_zero(tmp_path, monkeypatch):
    write(tmp_path, "Foo.download.recipe.yaml", rc(name="Foo"))
    monkeypatch.chdir(tmp_path)
    assert checker.main(["--auto-fix=no", "Foo.download.recipe.yaml"]) == 0


def test_main_strict_warning_returns_one(tmp_path, monkeypatch):
    write(
        tmp_path,
        "Foo_Bar.download.recipe.yaml",
        rc(name="Foo_Bar", identifier="com.github.jgstew.download.Foo_Bar"),
    )
    monkeypatch.chdir(tmp_path)
    # advisory by default -> 0; --strict promotes the W121 warning to a failure
    assert checker.main(["--auto-fix=no", "Foo_Bar.download.recipe.yaml"]) == 0
    assert (
        checker.main(["--auto-fix=no", "--strict", "Foo_Bar.download.recipe.yaml"]) == 1
    )


def test_main_autofix_returns_one(tmp_path, monkeypatch):
    write(
        tmp_path, "Foo.download.recipe.yaml", rc(name="Foo", process=TWO_STEPS_NO_BLANK)
    )
    monkeypatch.chdir(tmp_path)
    # a fix was applied (W120 spacing) -> non-zero so the change is re-staged
    assert checker.main(["--auto-fix=yes", "Foo.download.recipe.yaml"]) == 1
