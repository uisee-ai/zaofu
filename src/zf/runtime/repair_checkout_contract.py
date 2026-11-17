"""Bind source-relative repair scope and Python checks to the isolated checkout."""
from pathlib import Path
import shlex
import sys


def bind_checkout_contract(contract: dict, source_root: Path) -> dict:
    root = source_root.resolve()
    scope = []
    for item in contract["scope"]:
        path = Path(item)
        if ".." in path.parts:
            raise ValueError("repair scope must not traverse outside its checkout")
        try:
            relative = (path if path.is_absolute() else root / path).resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError("repair scope is outside its source repository") from exc
        if str(relative) == ".":
            raise ValueError("repair scope must name bounded repository paths")
        scope.append(relative.as_posix())

    def command(value: str) -> str:
        text = value.strip()
        # Translate only the exact source-cwd prefix. Other shell forms are
        # opaque: never guess how to rewrite commands targeting another tree.
        for quoted in (shlex.quote(str(root)), f'"{root}"', str(root)):
            prefix = f"cd {quoted} && "
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        if str(root) in text:
            raise ValueError("repair verification must run in its isolated checkout")
        for prefix in (".venv/bin/python -m pytest", ".venv/bin/pytest", "python -m pytest", "python3 -m pytest", "pytest"):
            if text == prefix or text.startswith(prefix + " "):
                return f"env PYTHONPATH=src {shlex.quote(sys.executable)} -m pytest" + text[len(prefix):]
        return text

    instruction = command(contract["verification_instruction"])
    commands = [command(item) for item in contract["verification_commands"]]
    return {**contract, "scope": scope,
            "verification_instruction": instruction, "verification_commands": commands}
