#!/usr/bin/env python3
"""Tests for pre_commit_hooks/autopkg_processor_check_conventions.py.

These exercise the public helpers (processor detection, the stub heuristic,
discovery) and the check/auto-fix pipeline via check_file/check_files, plus the
main() entry point's exit codes.

Note: the stub heuristic keys off a parent folder name containing "processor",
and pytest's tmp_path names can themselves contain that substring, so every
test writes into an explicitly named subfolder (e.g. "plainpkg" vs
"SharedProcessors") rather than relying on tmp_path's own basename.
"""

import ast
import json

import pytest

from pre_commit_hooks import autopkg_processor_check_conventions as checker

# A fully convention-conforming processor (no E-codes expected). The class name
# must match the filename stem, so write this as `ExampleClean.py`.
CLEAN_PROCESSOR = '''\
#!/usr/local/autopkg/python
# Created 2024 by JGStew
"""See docstring for ExampleClean class"""

from autopkglib import Processor, ProcessorError

__all__ = ["ExampleClean"]


class ExampleClean(Processor):
    """Reads an input value and writes it straight back out to the environment.

    A deliberately simple processor used to exercise the convention checker: it
    declares one input_variable and one output_variable and copies the value
    across in main(). Raises ProcessorError only as an import sanity check.
    """

    description = __doc__
    input_variables = {
        "example_input": {
            "required": False,
            "default": "",
            "description": "The value to copy to the output.",
        },
    }
    output_variables = {
        "example_output": {
            "description": "The copied value.",
        },
    }

    def main(self):
        """Execution starts here."""
        if False:  # pragma: no cover - keeps ProcessorError referenced
            raise ProcessorError("unreachable")
        self.env["example_output"] = self.env.get("example_input", "")


if __name__ == "__main__":
    PROCESSOR = ExampleClean()
    PROCESSOR.execute_shell()
'''


# A docstring long enough to clear E024 (>= MIN_CLASS_DOCSTRING_LEN) and not the
# generated "<Class> processor." stub, so name/variable checks are tested in
# isolation without E024 noise.
DOC = (
    "A deliberately simple processor used to exercise the convention checker "
    "across its various rules."
)


def processor_src(name, doc=DOC, above_class="", input_body=None):
    """Return the source of an otherwise E-clean processor class named `name`.

    `above_class` is inserted immediately before the `class` line (for the
    per-line E032/E033 opt-out markers). `input_body` (raw lines) fills
    input_variables so W010 can be exercised; None means an empty dict.
    """
    input_block = "{}" if input_body is None else "{\n" + input_body + "    }"
    return (
        "#!/usr/local/autopkg/python\n"
        "# Created 2024 by JGStew\n"
        f'"""See docstring for {name} class"""\n'
        "\n"
        "from autopkglib import Processor, ProcessorError\n"
        "\n"
        f'__all__ = ["{name}"]\n'
        "\n"
        "\n"
        f"{above_class}"
        f"class {name}(Processor):\n"
        f'    """{doc}"""\n'
        "\n"
        "    description = __doc__\n"
        f"    input_variables = {input_block}\n"
        "    output_variables = {}\n"
        "\n"
        "    def main(self):\n"
        '        """Execution starts here."""\n'
        "        _ = ProcessorError\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        f"    PROCESSOR = {name}()\n"
        "    PROCESSOR.execute_shell()\n"
    )


# A minimal recipe schema with a jgstew Processor enum block, for W009 tests.
SCHEMA_JSON = """{
  "definitions": {
    "Process": {
      "properties": {
        "Processor": {
          "anyOf": [
            {
              "enum": [
                "URLDownloader"
              ]
            },
            {
              "enum": [
                "com.github.jgstew.SharedProcessors/Existing"
              ]
            }
          ]
        }
      }
    }
  }
}
"""


def write(folder, name, content):
    """Write `content` to folder/name (creating folder) and return the path str."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(content, encoding="utf-8")
    return str(path)


def codes(issues):
    """Return the set of check-id codes from a list of (lineno, code, msg)."""
    return {code for _lineno, code, _msg in issues}


# --------------------------------------------------------------------------- #
# imports_autopkglib
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "src,expected",
    [
        ("import autopkglib\n", True),
        ("import autopkglib.foo\n", True),
        ("from autopkglib import Processor\n", True),
        ("from autopkglib.something import X\n", True),
        ("import os\n", False),
        ("from os import path\n", False),
    ],
)
def test_imports_autopkglib(src, expected):
    assert checker.imports_autopkglib(ast.parse(src)) is expected


# --------------------------------------------------------------------------- #
# in_processor_folder / is_new_processor_stub / has_processor_subclass
# --------------------------------------------------------------------------- #
def test_in_processor_folder(tmp_path):
    assert checker.in_processor_folder(write(tmp_path / "SharedProcessors", "A.py", ""))
    assert checker.in_processor_folder(
        write(tmp_path / "SharedDangerousProcessors", "B.py", "")
    )
    # case-insensitive
    assert checker.in_processor_folder(write(tmp_path / "MyPROCESSORstuff", "C.py", ""))
    # not a processor folder
    assert not checker.in_processor_folder(write(tmp_path / "utils", "D.py", ""))


def test_is_new_processor_stub_small_in_processor_folder(tmp_path):
    path = write(tmp_path / "SharedProcessors", "NewThing.py", '"""A draft."""\n')
    assert checker.is_new_processor_stub(path) is True


def test_is_new_processor_stub_rejects_large_file(tmp_path):
    big = "\n".join(f"x{i} = {i}" for i in range(checker.STUB_MAX_LINES + 5)) + "\n"
    path = write(tmp_path / "SharedProcessors", "Big.py", big)
    assert checker.is_new_processor_stub(path) is False


def test_is_new_processor_stub_rejects_dunder(tmp_path):
    path = write(tmp_path / "SharedProcessors", "__init__.py", "x = 1\n")
    assert checker.is_new_processor_stub(path) is False


def test_is_new_processor_stub_rejects_skip_marker(tmp_path):
    src = "# %s\nx = 1\n" % checker.SKIP_MARKER
    path = write(tmp_path / "SharedProcessors", "Skip.py", src)
    assert checker.is_new_processor_stub(path) is False


def test_is_new_processor_stub_rejects_non_processor_folder(tmp_path):
    path = write(tmp_path / "plainpkg", "Small.py", "x = 1\n")
    assert checker.is_new_processor_stub(path) is False


def test_has_processor_subclass(tmp_path):
    yes = "class Foo(Processor):\n    pass\n"
    no = "class Foo(object):\n    pass\n"
    assert checker.has_processor_subclass(ast.parse(yes)) is True
    assert checker.has_processor_subclass(ast.parse(no)) is False


# --------------------------------------------------------------------------- #
# check_file: gating (W001 / skip marker) and a clean file
# --------------------------------------------------------------------------- #
def test_clean_processor_has_no_errors(tmp_path):
    path = write(tmp_path / "SharedProcessors", "ExampleClean.py", CLEAN_PROCESSOR)
    issues, fixed = checker.check_file(path, auto_fix=False)
    error_codes = {c for c in codes(issues) if c.startswith("E")}
    assert error_codes == set(), f"unexpected errors: {sorted(error_codes)}"
    assert fixed == []


def test_non_processor_is_skipped_with_w001(tmp_path):
    path = write(tmp_path / "plainpkg", "helper.py", "def foo():\n    return 1\n")
    issues, fixed = checker.check_file(path, auto_fix=True)
    assert codes(issues) == {"W001"}
    assert fixed == []
    # auto_fix must NOT rewrite a non-processor
    assert (tmp_path / "plainpkg" / "helper.py").read_text() == (
        "def foo():\n    return 1\n"
    )


def test_skip_marker_suppresses_everything(tmp_path):
    src = "# %s\nimport autopkglib\nprint('x')\n" % checker.SKIP_MARKER
    path = write(tmp_path / "SharedProcessors", "Skipped.py", src)
    issues, fixed = checker.check_file(path, auto_fix=True)
    assert issues == []
    assert fixed == []


def test_syntax_error_reports_e000(tmp_path):
    path = write(tmp_path / "SharedProcessors", "Broken.py", "def (:\n")
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E000" in codes(issues)


# --------------------------------------------------------------------------- #
# check_file: individual checks (report-only)
# --------------------------------------------------------------------------- #
def test_missing_module_docstring_reports_e002(tmp_path):
    src = "#!/usr/local/autopkg/python\nimport autopkglib\n"
    path = write(tmp_path / "SharedProcessors", "NoDoc.py", src)
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E002" in codes(issues)


def test_module_docstring_present_no_e002(tmp_path):
    # positive counterpart: a docstring is present -> E002 must NOT fire
    src = '#!/usr/local/autopkg/python\n"""A module docstring."""\nimport autopkglib\n'
    path = write(tmp_path / "SharedProcessors", "HasDoc.py", src)
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E002" not in codes(issues)


def test_missing_processorerror_import_reports_e005(tmp_path):
    src = (
        "#!/usr/local/autopkg/python\n" '"""x"""\n' "from autopkglib import Processor\n"
    )
    path = write(tmp_path / "SharedProcessors", "OnlyProc.py", src)
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E005" in codes(issues)


def test_processorerror_imported_no_e005(tmp_path):
    # positive counterpart: ProcessorError is imported -> E005 must NOT fire
    src = (
        "#!/usr/local/autopkg/python\n"
        '"""x"""\n'
        "from autopkglib import Processor, ProcessorError\n"
    )
    path = write(tmp_path / "SharedProcessors", "BothImports.py", src)
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E005" not in codes(issues)


def test_missing_autopkglib_import_reports_e031(tmp_path):
    # kept in scope by subclassing Processor even though the import is absent
    src = (
        "#!/usr/local/autopkg/python\n"
        '"""x"""\n'
        "class WithBase(Processor):\n"
        '    """d"""\n'
    )
    path = write(tmp_path / "SharedProcessors", "WithBase.py", src)
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E031" in codes(issues)


def test_autopkglib_imported_no_e031(tmp_path):
    # positive counterpart: autopkglib is imported -> E031 must NOT fire
    src = (
        "#!/usr/local/autopkg/python\n"
        '"""x"""\n'
        "from autopkglib import Processor, ProcessorError\n"
        "class WithBase(Processor):\n"
        '    """d"""\n'
    )
    path = write(tmp_path / "SharedProcessors", "HasImport.py", src)
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E031" not in codes(issues)


# --------------------------------------------------------------------------- #
# check_file: auto-fix / stub build-out
# --------------------------------------------------------------------------- #
def test_autofix_builds_out_empty_stub(tmp_path):
    path = write(tmp_path / "SharedProcessors", "BuiltOut.py", "")
    _issues, fixed = checker.check_file(path, auto_fix=True)
    fixed_codes = codes(fixed)
    # the core scaffolding fixes should all have fired
    for code in ("E001", "E002", "E031", "E010", "E003", "E006"):
        assert code in fixed_codes, f"expected {code} in {sorted(fixed_codes)}"

    result = (tmp_path / "SharedProcessors" / "BuiltOut.py").read_text()
    assert checker.imports_autopkglib(ast.parse(result))
    assert "class BuiltOut(Processor):" in result
    assert '__all__ = ["BuiltOut"]' in result
    assert 'if __name__ == "__main__":' in result


def test_autofix_stub_is_idempotent(tmp_path):
    path = write(tmp_path / "SharedProcessors", "Idem.py", '"""A short draft."""\n')
    checker.check_file(path, auto_fix=True)
    after_first = (tmp_path / "SharedProcessors" / "Idem.py").read_text()
    # second pass: report-only, should not need scaffolding and must not mutate
    issues, fixed = checker.check_file(path, auto_fix=False)
    after_second = (tmp_path / "SharedProcessors" / "Idem.py").read_text()
    assert after_first == after_second
    assert fixed == []
    # only the "human must finish" items should remain (docstring/test recipe)
    assert codes(issues) <= {"E024", "W005"}


def test_maybe_fix_autopkglib_import_inserts_after_docstring(tmp_path):
    src = '"""module doc"""\n\n\nclass Foo(Processor):\n    pass\n'
    path = write(tmp_path / "SharedProcessors", "Foo.py", src)
    entry = checker.maybe_fix_autopkglib_import(path)
    assert entry is not None and entry[1] == "E031"
    result = (tmp_path / "SharedProcessors" / "Foo.py").read_text()
    assert "from autopkglib import Processor, ProcessorError" in result
    assert checker.imports_autopkglib(ast.parse(result))
    # returns None when the import already exists
    assert checker.maybe_fix_autopkglib_import(path) is None


# --------------------------------------------------------------------------- #
# check_files: --disable filtering, non-.py skipping
# --------------------------------------------------------------------------- #
def test_check_files_disable_filters_codes(tmp_path):
    src = "#!/usr/local/autopkg/python\nimport autopkglib\n"  # missing docstring
    path = write(tmp_path / "SharedProcessors", "NoDoc2.py", src)
    results = checker.check_files([path], auto_fix=False, disabled={"E002"})
    _p, issues, _fixed = results[0]
    assert "E002" not in codes(issues)


def test_check_files_skips_non_python(tmp_path):
    txt = write(tmp_path / "plainpkg", "notes.txt", "hello\n")
    results = checker.check_files([txt], auto_fix=False)
    assert results == []


# --------------------------------------------------------------------------- #
# discover_processor_files
# --------------------------------------------------------------------------- #
def test_discover_processor_files(tmp_path):
    root = tmp_path / "repo"
    proc = write(root / "SharedProcessors", "RealProc.py", CLEAN_PROCESSOR)
    stub = write(root / "SharedProcessors", "StubProc.py", '"""draft"""\n')
    plain = write(root / "utils", "helper.py", "x = 1\n")  # not a processor
    found = set(checker.discover_processor_files(str(root), max_depth=3))
    assert proc in found
    assert stub in found
    assert plain not in found


def test_discover_respects_max_depth(tmp_path):
    root = tmp_path / "repo"
    # depth 4 (deeper than max_depth=3) should be pruned
    deep = write(
        root / "a" / "b" / "c" / "SharedProcessors", "Deep.py", CLEAN_PROCESSOR
    )
    found = set(checker.discover_processor_files(str(root), max_depth=3))
    assert deep not in found


# --------------------------------------------------------------------------- #
# main() exit codes
# --------------------------------------------------------------------------- #
def test_main_returns_zero_on_clean(tmp_path):
    path = write(tmp_path / "SharedProcessors", "ExampleClean.py", CLEAN_PROCESSOR)
    assert checker.main(["--auto-fix=no", path]) == 0


def test_main_returns_one_when_issue_remains(tmp_path):
    src = (
        "#!/usr/local/autopkg/python\n"
        '"""x"""\n'
        "from autopkglib import Processor\n"  # missing ProcessorError -> E005
    )
    path = write(tmp_path / "SharedProcessors", "OnlyProc2.py", src)
    assert checker.main(["--auto-fix=no", path]) == 1


def test_main_returns_one_after_autofix(tmp_path):
    path = write(tmp_path / "SharedProcessors", "FixMe.py", "")
    assert checker.main(["--auto-fix=yes", path]) == 1


# --------------------------------------------------------------------------- #
# E032: class name must be strict CamelCase (allowed acronyms: URL, CURL)
# --------------------------------------------------------------------------- #
def test_e032_lowercase_start_flagged(tmp_path):
    path = write(tmp_path / "SharedProcessors", "fooBar.py", processor_src("fooBar"))
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E032" in codes(issues)


def test_e032_allowed_acronym_url_passes(tmp_path):
    path = write(
        tmp_path / "SharedProcessors", "URLThing.py", processor_src("URLThing")
    )
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E032" not in codes(issues)


def test_e032_camelcase_word_passes(tmp_path):
    # "Json" is an ordinary CamelCase word (not an acronym run) -> allowed
    path = write(
        tmp_path / "SharedProcessors", "JsonThing.py", processor_src("JsonThing")
    )
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E032" not in codes(issues)


def test_e032_non_allowed_acronym_flagged(tmp_path):
    path = write(
        tmp_path / "SharedProcessors", "JSONThing.py", processor_src("JSONThing")
    )
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E032" in codes(issues)


def test_e032_marker_suppresses(tmp_path):
    src = processor_src("JSONMarked", above_class="# processor-name-ok\n")
    path = write(tmp_path / "SharedProcessors", "JSONMarked.py", src)
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "E032" not in codes(issues)


# --------------------------------------------------------------------------- #
# W010: input/output variable keys must be snake_case
# --------------------------------------------------------------------------- #
def test_w010_camelcase_key_flagged(tmp_path):
    body = '        "badKey": {"required": False, "description": "x"},\n'
    path = write(
        tmp_path / "SharedProcessors",
        "BadKey.py",
        processor_src("BadKey", input_body=body),
    )
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "W010" in codes(issues)


def test_w010_all_caps_key_flagged(tmp_path):
    # strict: ALL_CAPS keys are flagged too (must be marked if intentional)
    body = '        "COMPUTE_HASHES": {"required": False, "description": "x"},\n'
    path = write(
        tmp_path / "SharedProcessors",
        "CapsKey.py",
        processor_src("CapsKey", input_body=body),
    )
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "W010" in codes(issues)


def test_w010_snake_key_ok(tmp_path):
    body = '        "good_key": {"required": False, "description": "x"},\n'
    path = write(
        tmp_path / "SharedProcessors",
        "GoodKey.py",
        processor_src("GoodKey", input_body=body),
    )
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "W010" not in codes(issues)


def test_w010_marker_suppresses(tmp_path):
    body = (
        "        # variable-name-ok\n"
        '        "badKey": {"required": False, "description": "x"},\n'
    )
    path = write(
        tmp_path / "SharedProcessors",
        "MarkedKey.py",
        processor_src("MarkedKey", input_body=body),
    )
    issues, _fixed = checker.check_file(path, auto_fix=False)
    assert "W010" not in codes(issues)


# --------------------------------------------------------------------------- #
# E033: class docstring must be unique across the repo (cross-file)
# --------------------------------------------------------------------------- #
E033_DOC = (
    "Reads a value from the environment and copies it straight to the output var."
)


def test_e033_duplicate_docstring_flagged(tmp_path, monkeypatch):
    write(
        tmp_path / "SharedProcessors", "Alpha.py", processor_src("Alpha", doc=E033_DOC)
    )
    write(tmp_path / "SharedProcessors", "Beta.py", processor_src("Beta", doc=E033_DOC))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(checker, "_DOCSTRING_INDEX", None)
    issues, _fixed = checker.check_file("SharedProcessors/Beta.py", auto_fix=False)
    assert "E033" in codes(issues)


def test_e033_distinct_docstrings_pass(tmp_path, monkeypatch):
    write(
        tmp_path / "SharedProcessors",
        "Gamma.py",
        processor_src(
            "Gamma",
            doc="Gamma does one clearly documented and distinct thing worth noting.",
        ),
    )
    write(
        tmp_path / "SharedProcessors",
        "Delta.py",
        processor_src(
            "Delta",
            doc="Delta does a different clearly documented distinct thing entirely.",
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(checker, "_DOCSTRING_INDEX", None)
    issues, _fixed = checker.check_file("SharedProcessors/Delta.py", auto_fix=False)
    assert "E033" not in codes(issues)


def test_e033_marker_suppresses(tmp_path, monkeypatch):
    write(
        tmp_path / "SharedProcessors", "Alpha.py", processor_src("Alpha", doc=E033_DOC)
    )
    write(
        tmp_path / "SharedProcessors",
        "Beta.py",
        processor_src("Beta", doc=E033_DOC, above_class="# duplicate-docstring-ok\n"),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(checker, "_DOCSTRING_INDEX", None)
    issues, _fixed = checker.check_file("SharedProcessors/Beta.py", auto_fix=False)
    assert "E033" not in codes(issues)


def test_e033_errors_via_main(tmp_path, monkeypatch):
    # E033 is an error, so a duplicate must make the run exit non-zero.
    write(
        tmp_path / "SharedProcessors", "Alpha.py", processor_src("Alpha", doc=E033_DOC)
    )
    write(tmp_path / "SharedProcessors", "Beta.py", processor_src("Beta", doc=E033_DOC))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(checker, "_DOCSTRING_INDEX", None)
    assert checker.main(["--auto-fix=no"]) == 1


# --------------------------------------------------------------------------- #
# W009: processor should be listed in the recipe schema's Processor enum
# --------------------------------------------------------------------------- #
def test_w009_reports_when_missing(tmp_path, monkeypatch):
    write(tmp_path, ".AutoPkgRecipeOpinionated.schema.json", SCHEMA_JSON)
    write(tmp_path / "SharedProcessors", "NewProc.py", processor_src("NewProc"))
    monkeypatch.chdir(tmp_path)
    issues, _fixed = checker.check_file("SharedProcessors/NewProc.py", auto_fix=False)
    assert "W009" in codes(issues)


def test_w009_present_no_report(tmp_path, monkeypatch):
    write(tmp_path, ".AutoPkgRecipeOpinionated.schema.json", SCHEMA_JSON)
    write(tmp_path / "SharedProcessors", "Existing.py", processor_src("Existing"))
    monkeypatch.chdir(tmp_path)
    issues, _fixed = checker.check_file("SharedProcessors/Existing.py", auto_fix=False)
    assert "W009" not in codes(issues)


def test_w009_no_schema_no_report(tmp_path, monkeypatch):
    write(tmp_path / "SharedProcessors", "NewProc.py", processor_src("NewProc"))
    monkeypatch.chdir(tmp_path)  # no schema file present -> check is a no-op
    issues, _fixed = checker.check_file("SharedProcessors/NewProc.py", auto_fix=False)
    assert "W009" not in codes(issues)


def test_w009_marker_suppresses(tmp_path, monkeypatch):
    write(tmp_path, ".AutoPkgRecipeOpinionated.schema.json", SCHEMA_JSON)
    src = processor_src("NewProc", above_class="# schema-enum-ok\n")
    write(tmp_path / "SharedProcessors", "NewProc.py", src)
    monkeypatch.chdir(tmp_path)
    issues, _fixed = checker.check_file("SharedProcessors/NewProc.py", auto_fix=False)
    assert "W009" not in codes(issues)


def test_w009_autofix_appends_to_schema(tmp_path, monkeypatch):
    schema = tmp_path / ".AutoPkgRecipeOpinionated.schema.json"
    schema.write_text(SCHEMA_JSON, encoding="utf-8")
    write(tmp_path / "SharedProcessors", "NewProc.py", processor_src("NewProc"))
    monkeypatch.chdir(tmp_path)
    _issues, fixed = checker.check_file("SharedProcessors/NewProc.py", auto_fix=True)
    assert "W009" in codes(fixed)
    data = json.loads(schema.read_text(encoding="utf-8"))
    values = set()
    for block in data["definitions"]["Process"]["properties"]["Processor"]["anyOf"]:
        values.update(block.get("enum", []))
    assert "com.github.jgstew.SharedProcessors/NewProc" in values
