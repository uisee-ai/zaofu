"""Independent black-box oracle for the frozen readme-cli-product.md fixture.

This is product-specific test infrastructure; it does not submit runtime verdicts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


def verify(root: Path) -> dict:
    script = root.resolve() / "readme.py"
    if not script.is_file():
        return {"passed": False, "error": "readme.py missing", "cases": []}
    cases = []
    with TemporaryDirectory(prefix="zf-readme-oracle-") as directory:
        cwd = Path(directory)
        (cwd / "正常 README.md").write_bytes(b"README\n\x00\xff\xfe\r\n")
        (cwd / "empty.md").write_bytes(b"")
        (cwd / "folder").mkdir()
        (cwd / "folder" / "sentinel.bin").write_bytes(b"keep nested bytes")
        def snapshot() -> dict:
            return {str(p.relative_to(cwd)): ("file", p.read_bytes()) if p.is_file()
                    else ("directory", None) for p in cwd.rglob("*")}
        before = snapshot()
        for name, args, expected in [
            ("exact-bytes", ["正常 README.md"], b"README\n\x00\xff\xfe\r\n"),
            ("empty", ["empty.md"], b""),
            ("missing", ["missing README.md"], None),
            ("directory", ["folder"], None),
            ("usage", [], None),
        ]:
            result = subprocess.run([sys.executable, str(script), *args], cwd=cwd, capture_output=True, timeout=10)
            if expected is not None:
                passed = result.returncode == 0 and result.stdout == expected and result.stderr == b""
            elif args:
                passed = result.returncode != 0 and result.stdout == b"" and bool(result.stderr.strip())
                path_bytes = args[0].encode()
                reason = result.stderr.replace(path_bytes, b"").strip(b" \t\r\n:;\"'[]()")
                passed = passed and path_bytes in result.stderr and bool(reason)
            else:
                # The frozen input requires usage, but does not choose its stream.
                passed = result.returncode != 0 and bool(result.stdout.strip() or result.stderr.strip())
            after = snapshot()
            passed = passed and after == before
            cases.append({"case": name, "passed": passed, "exit_code": result.returncode,
                          "stdout_hex": result.stdout.hex(), "stderr": result.stderr.decode(errors="replace"),
                          "fixture_unchanged": after == before})
    return {"passed": all(case["passed"] for case in cases), "cases": cases,
            "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.project, text=True).strip()
    if actual != args.commit:
        parser.error("candidate HEAD differs from the expected frozen commit")
    # A dirty executable cannot borrow the clean commit's identity.
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "readme.py", "test_readme.py", "README.md"], cwd=args.project, check=True)
    subprocess.run(["git", "ls-files", "--error-unmatch", "readme.py"], cwd=args.project, check=True, stdout=subprocess.DEVNULL)
    report = verify(args.project)
    report.update(project=str(args.project.resolve()), commit=actual,
                  oracle_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
