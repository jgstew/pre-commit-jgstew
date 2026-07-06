"""Tests for pre_commit_hooks/validate_bes.py.

Requires the `validate_bes_xml` package (installed via requirements.txt in CI);
skipped where it is not available.
"""

from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parent / "examples"

pytest.importorskip("validate_bes_xml")

from pre_commit_hooks import validate_bes as hook  # noqa: E402


def test_valid_bes_passes():
    assert hook.main([str(EXAMPLES / "example-test.bes")]) == 0


def test_no_files_is_zero():
    assert hook.main([]) == 0


def test_malformed_bes_fails(tmp_path):
    bad = tmp_path / "bad.bes"
    bad.write_text("<BES><Task><Unclosed></Task></BES>", encoding="utf-8")
    assert hook.main([str(bad)]) == 1


def test_mixed_valid_and_invalid_counts_only_bad(tmp_path):
    bad = tmp_path / "bad.bes"
    bad.write_text("<BES><Task><Unclosed></Task></BES>", encoding="utf-8")
    good = str(EXAMPLES / "example-test.bes")
    assert hook.main([good, str(bad)]) == 1
