"""Tests for pre_commit_jgstew/github_action_set_output_fix.py."""

from pre_commit_jgstew import github_action_set_output_fix as hook

SET_OUTPUT_LINE = '        run: echo "::set-output name=version::1.2.3"\n'
SAVE_STATE_LINE = '        run: echo "::save-state name=foo::bar"\n'


def gha_file(tmp_path, content, name="wf.yaml"):
    """Create a workflow file under a .github/workflows path."""
    path = tmp_path / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_detects_set_output_without_overwrite(tmp_path):
    path = gha_file(tmp_path, SET_OUTPUT_LINE)
    assert hook.main([path]) == 1
    # no overwrite -> file unchanged
    assert "::set-output" in open(path).read()


def test_overwrite_rewrites_set_output(tmp_path):
    path = gha_file(tmp_path, SET_OUTPUT_LINE)
    assert hook.main(["--overwrite", path]) == 1
    fixed = open(path).read()
    assert "::set-output" not in fixed
    assert "version=1.2.3" in fixed
    assert "$GITHUB_OUTPUT" in fixed


def test_overwrite_rewrites_save_state(tmp_path):
    path = gha_file(tmp_path, SAVE_STATE_LINE)
    assert hook.main(["--overwrite", path]) == 1
    fixed = open(path).read()
    assert "::save-state" not in fixed
    assert "$GITHUB_STATE" in fixed


def test_clean_workflow_untouched(tmp_path):
    path = gha_file(tmp_path, '        run: echo "hello"\n')
    assert hook.main(["--overwrite", path]) == 0


def test_non_github_yaml_is_skipped(tmp_path):
    # right extension but not under .github -> skipped
    path = tmp_path / "elsewhere.yaml"
    path.write_text(SET_OUTPUT_LINE, encoding="utf-8")
    assert hook.main([str(path)]) == 0


def test_non_yaml_is_skipped(tmp_path):
    path = tmp_path / ".github" / "notes.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SET_OUTPUT_LINE, encoding="utf-8")
    assert hook.main([str(path)]) == 0
