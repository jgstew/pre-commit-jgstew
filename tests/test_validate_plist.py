"""Tests for pre_commit_hooks/validate_plist.py.

Requires the `validate_plist_xml` package (installed via requirements.txt in
CI); skipped where it is not available.
"""

from pathlib import Path

import pytest

EXAMPLES = Path(__file__).parent / "examples"

pytest.importorskip("validate_plist_xml")

from pre_commit_hooks import validate_plist as hook  # noqa: E402


def test_valid_plist_passes():
    assert hook.main([str(EXAMPLES / "nested-sample.plist")]) == 0


def test_no_files_is_zero():
    assert hook.main([]) == 0


def test_malformed_plist_fails(tmp_path):
    bad = tmp_path / "bad.plist"
    bad.write_text("<plist><dict><key>x</key></dict>", encoding="utf-8")
    assert hook.main([str(bad)]) == 1
