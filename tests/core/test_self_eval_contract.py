from __future__ import annotations

from pathlib import Path

import pytest

from zf.core.self_eval import (
    SelfEvalContractError,
    load_self_eval_contract,
    parse_self_eval_contract,
)


def _valid_contract(output_dir: Path) -> dict:
    return {
        "version": 1,
        "goal": "Verify deterministic self-eval contract loading.",
        "scope": {
            "allow": ["src/zf/**"],
            "exclude": [".zf/**"],
        },
        "metric": {
            "name": "pytest_pass",
            "direction": "higher_is_better",
            "pattern": r"score=(?P<score>\d+)",
        },
        "verify": {
            "command": "PYTHONPATH=src python3 -m pytest tests/test_cli_main.py -q",
        },
        "guards": [
            {
                "name": "py_compile",
                "command": "PYTHONPATH=src python3 -m compileall -q src",
            },
        ],
        "output": {
            "dir": str(output_dir),
            "iterations": "iterations.tsv",
            "summary": "summary.md",
        },
    }


def test_load_self_eval_contract_validates_required_sections(tmp_path: Path):
    contract_path = tmp_path / "contract.yaml"
    contract_path.write_text(
        """
version: 1
goal: Verify deterministic self-eval contract loading.
scope:
  allow:
    - src/zf/**
  exclude:
    - .zf/**
metric:
  name: pytest_pass
  direction: higher_is_better
  pattern: 'score=(?P<score>\\d+)'
verify:
  command: "PYTHONPATH=src python3 -m pytest tests/test_cli_main.py -q"
guards:
  - name: py_compile
    command: "PYTHONPATH=src python3 -m compileall -q src"
output:
  dir: /tmp/self-eval-out
""",
        encoding="utf-8",
    )

    contract = load_self_eval_contract(contract_path)

    assert contract.version == "1"
    assert contract.goal == "Verify deterministic self-eval contract loading."
    assert contract.scope.allow == ["src/zf/**"]
    assert contract.scope.exclude == [".zf/**"]
    assert contract.metric.name == "pytest_pass"
    assert contract.metric.direction == "higher_is_better"
    assert contract.metric.pattern == r"score=(?P<score>\d+)"
    assert contract.verify.command.startswith("PYTHONPATH=src")
    assert contract.guards[0].name == "py_compile"
    assert contract.output.dir == "/tmp/self-eval-out"


@pytest.mark.parametrize(
    ("patch", "expected"),
    [
        ({"scope": {"allow": []}}, "scope.allow"),
        ({"verify": {}}, "verify.command"),
        ({"metric": {"name": "pytest_pass", "direction": "sideways"}}, "metric.direction"),
        ({"metric": {"name": "pytest_pass", "direction": "higher_is_better", "pattern": "["}}, "metric.pattern"),
        ({"guards": None}, "guards"),
        ({"output": {}}, "output.dir"),
    ],
)
def test_parse_self_eval_contract_fails_closed_for_invalid_contracts(
    tmp_path: Path,
    patch: dict,
    expected: str,
):
    data = _valid_contract(tmp_path / "out")
    data.update(patch)

    with pytest.raises(SelfEvalContractError) as excinfo:
        parse_self_eval_contract(data)

    assert any(expected in error for error in excinfo.value.errors)


def test_contract_validation_never_executes_verify_command(tmp_path: Path):
    sentinel = tmp_path / "verify-ran"
    contract_path = tmp_path / "invalid.yaml"
    contract_path.write_text(
        f"""
version: 1
goal: Invalid output must fail before verify command runs.
scope:
  allow:
    - src/zf/**
metric:
  name: score
  direction: higher_is_better
verify:
  command: "python3 -c \\"from pathlib import Path; Path({str(sentinel)!r}).write_text('ran')\\""
guards: []
output: {{}}
""",
        encoding="utf-8",
    )

    with pytest.raises(SelfEvalContractError):
        load_self_eval_contract(contract_path)

    assert not sentinel.exists()


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("codex exec run", "provider CLI"),
        ("openai.com api models.list", "provider CLI"),
        ("/usr/local/bin/openai api models.list", "provider CLI"),
        ("'C:\\Tools\\codex.exe' exec run", "provider CLI"),
        (r"C:\Tools\codex.COM exec run", "provider CLI"),
        ("bash -lc 'codex exec run'", "shell -c"),
        ("bash -o pipefail -c 'codex exec run'", "shell -c"),
        ("/bin/bash -lc 'openai api models.list'", "shell -c"),
        ("sh -c 'claude --help'", "shell -c"),
        ("python3 -m openai --help", "provider Python module"),
        ("python3 -m OpenAI --help", "provider Python module"),
        ("/usr/bin/python3.12 -m openai.cli --help", "provider Python module"),
        ("python3 -mopenai --help", "provider Python module"),
        ("python3 -Im anthropic --help", "provider Python module"),
        ("python3 -I -mopenai.cli --help", "provider Python module"),
        ("python.exe -m anthropic --help", "provider Python module"),
        ("py -3 -m claude_code --help", "provider Python module"),
        ("py -3m claude_code --help", "provider Python module"),
        ("PYTHONPATH=src python3 -m anthropic --help", "provider Python module"),
        ("PYTHONPATH=src /usr/bin/python3 -m codex --help", "provider Python module"),
        (r"C:\Tools\codex.exe exec run", "provider CLI"),
        (r"C:\Tools\openai.cmd api models.list", "provider CLI"),
        ("env codex exec run", "env wrapper"),
        ("/usr/bin/env openai api models.list", "env wrapper"),
        ("env FOO=1 claude --help", "env wrapper"),
        ("env --ignore-environment codex exec run", "env wrapper"),
        ("env -S 'codex exec run'", "env wrapper"),
        ("timeout 10 openai api models.list", "provider CLI"),
        ("timeout --foreground 10s codex exec run", "provider CLI"),
        ("nice -n 5 claude --help", "provider CLI"),
        ("nice --adjustment=5 python3 -m openai --help", "provider Python module"),
        ("nohup codex exec run", "provider CLI"),
        ("busybox sh -c 'openai api models.list'", "shell -c"),
        ("busybox env codex exec run", "env wrapper"),
        ("python3 -m pytest -q|codex exec run", "shell control operator"),
        ("python3 -m pytest -q;python3 -m openai --help", "shell control operator"),
        ("python3 -m pytest -q && claude --help", "shell control operator"),
    ],
)
def test_contract_rejects_provider_cli_and_wrappers(
    tmp_path: Path,
    command: str,
    expected: str,
):
    data = _valid_contract(tmp_path / "out")
    data["verify"] = {"command": command}

    with pytest.raises(SelfEvalContractError) as excinfo:
        parse_self_eval_contract(data)

    assert any(expected in error for error in excinfo.value.errors)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("claude --help", "provider CLI"),
        ("codex.com exec run", "provider CLI"),
        ("'C:\\Tools\\claude.exe' --help", "provider CLI"),
        (r"C:\Tools\claude.cmd --help", "provider CLI"),
        ("/bin/sh -c 'openai api models.list'", "shell -c"),
        ("python3 -m Claude_Code --help", "provider Python module"),
        ("python3 -Im codex --help", "provider Python module"),
        ("python.exe -m openai.cli --help", "provider Python module"),
        ("env -S 'codex exec run'", "env wrapper"),
        ("/usr/bin/env FOO=1 codex exec run", "env wrapper"),
        ("timeout -k 1s 10s openai api models.list", "provider CLI"),
        ("nice -5 codex exec run", "provider CLI"),
        ("nohup claude --help", "provider CLI"),
        ("busybox sh -c 'python3 -m openai --help'", "shell -c"),
        ("python3 -m pytest -q;codex exec run", "shell control operator"),
        ("python3 -m pytest -q || /usr/local/bin/openai api models.list", "shell control operator"),
    ],
)
def test_contract_rejects_provider_wrappers_in_guards(
    tmp_path: Path,
    command: str,
    expected: str,
):
    data = _valid_contract(tmp_path / "out")
    data["guards"] = [{"name": "bad", "command": command}]

    with pytest.raises(SelfEvalContractError) as excinfo:
        parse_self_eval_contract(data)

    assert any(expected in error for error in excinfo.value.errors)


@pytest.mark.parametrize(
    "command",
    [
        "timeout 10s python3 -m pytest tests/test_cli_main.py -q",
        "nice -n 5 python3 -m compileall -q src",
        "nohup python3 -m pytest tests/test_cli_main.py -q",
        "busybox sh -e",
    ],
)
def test_contract_allows_local_wrapper_commands_without_provider_calls(
    tmp_path: Path,
    command: str,
):
    data = _valid_contract(tmp_path / "out")
    data["verify"] = {"command": command}

    contract = parse_self_eval_contract(data)

    assert contract.verify.command == command
