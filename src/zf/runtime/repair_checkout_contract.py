"""Bind source-relative repair scope and Python checks to the isolated checkout."""
from pathlib import Path
import shlex
import sys


def trusted_pytest_invocation(command: str) -> dict | None:
    """Normalize known test spellings without a shell or candidate executable."""
    if any(token in command for token in ('\n', '\r', ';', '&', '|', '<', '>', '`', '$(')):
        return None
    try:
        argv = shlex.split(command)
    except ValueError:
        return None
    env = {'PYTHONPATH': 'src'}
    allowed = {'PYTHONPATH': 'src', 'PYTEST_ADDOPTS': '--no-cov'}
    if argv and argv[0] == 'env':
        argv = argv[1:]
        if not argv or '=' not in argv[0]:
            return None
    while argv and '=' in argv[0]:
        key, value = argv.pop(0).split('=', 1)
        if allowed.get(key) != value:
            return None
        env[key] = value
    if argv and argv[0] in {'pytest', '.venv/bin/pytest'}:
        arguments = argv[1:]
    elif argv[:3] == ['uv', 'run', 'pytest']:
        arguments = argv[3:]
    elif (len(argv) >= 3 and argv[0] in {'python', 'python3', '.venv/bin/python', sys.executable}
          and argv[1:3] == ['-m', 'pytest']):
        arguments = argv[3:]
    else:
        return None
    return {'argv': [sys.executable, '-m', 'pytest', *arguments], 'env': env}


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
        invocation = trusted_pytest_invocation(text)
        if invocation is not None:
            return shlex.join(['env', *(f'{k}={v}' for k, v in invocation['env'].items()),
                               *invocation['argv']])
        return text

    instruction = command(contract["verification_instruction"])
    commands = [command(item) for item in contract["verification_commands"]]
    return {**contract, "scope": scope,
            "verification_instruction": instruction, "verification_commands": commands}
