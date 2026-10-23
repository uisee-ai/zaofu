"""Independent product assertions, kept outside the worker checkout."""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile


def accept(kind: str, project: Path) -> dict:
    name = "counter" if kind == "issue" else "ledger"
    spec = importlib.util.spec_from_file_location(name, project / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if kind == "issue":
        assert module.advance(4) == 5
        for current, step in [(4, 3), (4, 0), (4, -2), (-5, 8)]:
            assert module.advance(current, step) == current + step
        return {"passed": True, "checks": 5}
    rows = [{"category": "餐饮", "amount_cents": 1250}, {"category": "餐饮", "amount_cents": -250},
            {"category": "交通", "amount_cents": 300}, {"category": "交通", "amount_cents": 0}]
    expected = {"total_cents": 1300, "by_category": {"餐饮": 1000, "交通": 300}}
    assert module.summarize(rows) == expected
    assert module.summarize([]) == {"total_cents": 0, "by_category": {}}
    bad = [[{"category": "a", "amount_cents": True}], [{"category": "a", "amount_cents": 1.5}],
           [{"category": "", "amount_cents": 1}], [{"category": "a"}]]
    for invalid in bad:
        try:
            module.summarize(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid input accepted: {invalid}")
    with tempfile.TemporaryDirectory(prefix="zf-184-acceptance-") as temp:
        source = Path(temp) / "input.json"
        source.write_text(json.dumps(rows, ensure_ascii=False))
        before = source.read_bytes()
        command = [sys.executable, str(project / "ledger_cli.py"), str(source)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0 and len(result.stdout.splitlines()) == 1
        assert json.loads(result.stdout) == expected and source.read_bytes() == before
        for content in ["not json", json.dumps(bad[0]), "{}"]:
            source.write_text(content)
            result = subprocess.run(command, capture_output=True, text=True, timeout=10)
            assert result.returncode == 2 and result.stderr.strip() and not result.stdout
        source.unlink()
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        assert result.returncode == 2 and result.stderr.strip() and not result.stdout
    assert (project / "README.md").is_file()
    return {"passed": True, "checks": 12}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["issue", "prd"])
    parser.add_argument("project", type=Path)
    args = parser.parse_args()
    print(json.dumps(accept(args.kind, args.project.resolve())))
