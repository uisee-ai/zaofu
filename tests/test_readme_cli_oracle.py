"""Negative controls for the product-specific independent acceptance oracle."""
from tests.e2e.prd_ui.verify_readme import verify


def test_missing_delivery_is_not_pass(tmp_path):
    assert verify(tmp_path)["passed"] is False


def test_success_exit_and_claimed_test_pass_cannot_replace_behavior(tmp_path):
    (tmp_path / "readme.py").write_text('print("all tests passed")\n')
    result = verify(tmp_path)
    assert result["passed"] is False
    assert len(result["cases"]) == 5
    assert not any(case["passed"] for case in result["cases"])


def test_correct_stdout_cannot_hide_input_mutation(tmp_path):
    (tmp_path / "readme.py").write_text('''import pathlib, sys
p = pathlib.Path(sys.argv[1])
sys.stdout.buffer.write(p.read_bytes())
p.write_bytes(b"changed")
''')
    result = verify(tmp_path)
    assert not result["passed"]
    assert result["cases"][0]["exit_code"] == 0
    assert not result["cases"][0]["fixture_unchanged"]


def test_complete_calibration_fixture_is_accepted(tmp_path):
    (tmp_path / "readme.py").write_text('''import pathlib, sys
if len(sys.argv) != 2:
    print("usage: readme.py <path>", file=sys.stderr)
    raise SystemExit(2)
try:
    body = pathlib.Path(sys.argv[1]).read_bytes()
except OSError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(1)
sys.stdout.buffer.write(body)
''')
    result = verify(tmp_path)
    assert result["passed"]
    assert len(result["cases"]) == 5


def test_path_without_error_reason_is_rejected(tmp_path):
    (tmp_path / "readme.py").write_text('''import sys
print(sys.argv[1] if len(sys.argv) > 1 else "usage", file=sys.stderr)
raise SystemExit(1)
''')
    cases = {case["case"]: case for case in verify(tmp_path)["cases"]}
    assert not cases["missing"]["passed"]
    assert not cases["directory"]["passed"]


def test_nested_directory_mutation_is_not_hidden(tmp_path):
    (tmp_path / "readme.py").write_text('''import pathlib, sys
if len(sys.argv) > 1:
    p = pathlib.Path(sys.argv[1])
    if p.is_dir():
        (p / "sentinel.bin").write_bytes(b"changed")
    print(f"cannot read {p}: invalid input", file=sys.stderr)
raise SystemExit(1)
''')
    case = next(case for case in verify(tmp_path)["cases"] if case["case"] == "directory")
    assert not case["fixture_unchanged"]
    assert not case["passed"]
