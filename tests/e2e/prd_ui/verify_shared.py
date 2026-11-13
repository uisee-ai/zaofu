"""Independent black-box oracle for shared-readme-product.md; never emits verdict events."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
from tempfile import TemporaryDirectory


def verify(root: Path) -> dict:
    root = root.resolve()
    scripts = [root / "read_file.py", root / "file_size.py"]
    missing = [p.name for p in scripts if not p.is_file()]
    if missing:
        return {"passed": False, "missing": missing, "cases": []}
    cases = []
    rng = random.Random(206)
    payloads = [b"plain\n", b"", b"\xff\x00\xfe\r\n", "中文".encode()]
    payloads += [rng.randbytes(n) for n in (1, 7, 63, 1024)]
    with TemporaryDirectory(prefix="zf-shared-oracle-") as directory:
        cwd = Path(directory)
        (cwd / "folder").mkdir()
        (cwd / "folder/sentinel").write_bytes(b"keep")
        # A default README must not make the no-argument case succeed.
        (cwd / "README.md").write_bytes(b"not a default input")
        for index, payload in enumerate(payloads):
            (cwd / f"数据 {index}.bin").write_bytes(payload)

        def snapshot():
            return {str(p.relative_to(cwd)): ("link", str(p.readlink())) if p.is_symlink()
                    else ("file", p.read_bytes()) if p.is_file() else ("directory", None)
                    for p in cwd.rglob("*")}

        for script in scripts:
            inputs = [(f"bytes-{i}", [f"数据 {i}.bin"], payload) for i, payload in enumerate(payloads)]
            inputs += [("missing", ["missing 文件"], None), ("directory", ["folder"], None), ("usage", [], None)]
            for name, args, payload in inputs:
                before = snapshot()
                result = subprocess.run([sys.executable, str(script), *args], cwd=cwd,
                                        capture_output=True, timeout=10)
                if payload is not None:
                    expected = payload if script.name == "read_file.py" else f"{len(payload)}\n".encode()
                    passed = result.returncode == 0 and result.stdout == expected and result.stderr == b""
                elif args:
                    path = args[0].encode()
                    reason = result.stderr.replace(path, b"").strip(b" \t\r\n:;\"'[]()")
                    passed = result.returncode != 0 and not result.stdout and path in result.stderr and bool(reason)
                else:
                    usage = (result.stdout + result.stderr).decode(errors="replace").lower()
                    passed = result.returncode != 0 and ("usage" in usage or "用法" in usage)
                unchanged = snapshot() == before
                cases.append({"entry": script.name, "case": name, "passed": passed and unchanged,
                              "fixture_unchanged": unchanged, "exit_code": result.returncode,
                              "stdout_hex": result.stdout.hex(), "stderr": result.stderr.decode(errors="replace")})
    return {"passed": all(row["passed"] for row in cases), "cases": cases, "seed": 206,
            "python": sys.version, "scripts": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in scripts}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.project, text=True).strip()
    if actual != args.commit:
        parser.error("candidate HEAD differs from frozen commit")
    files = ["read_file.py", "file_size.py"]
    subprocess.run(["git", "ls-files", "--error-unmatch", *files], cwd=args.project, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", *files], cwd=args.project, check=True)
    report = verify(args.project)
    report.update(commit=actual, project=str(args.project.resolve()), oracle_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
