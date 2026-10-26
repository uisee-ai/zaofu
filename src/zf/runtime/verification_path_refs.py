"""Extract repository path references from verification commands."""

from __future__ import annotations

import re
import shlex


_CODE_ARGUMENT_FLAGS = {
    "-c",
    "-e",
    "-ec",
    "-lc",
    "-lec",
    "--command",
    "--eval",
}
_SHELL_COMMAND_SEPARATORS = {"&&", "||", ";", "|", "&"}
_RUNTIME_PATH_VALUE_FLAGS = {"--state-dir"}
_DOCKER_VALUE_FLAGS = {
    "--env", "-e", "--mount", "--volume", "-v", "--workdir", "-w",
    "--user", "-u", "--network", "--entrypoint", "--name", "--publish", "-p",
    "--hostname", "--add-host", "--platform", "--tmpfs", "--pull",
}
_OCI_DIGEST_REF_RE = re.compile(
    r"^(?:[A-Za-z0-9._-]+(?::[0-9]+)?/)+"
    r"[A-Za-z0-9._-]+@sha256:[0-9a-fA-F]{64}$"
)


def command_path_refs(command: str) -> list[str]:
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    refs: list[str] = []
    skip_code_argument = False
    skip_runtime_path_value = False
    docker_run = len(tokens) >= 2 and tokens[0] == "docker" and tokens[1] == "run"
    docker_image_seen = not docker_run
    skip_docker_value = False
    command_position = True
    for index, token in enumerate(tokens):
        normalized = str(token).strip()
        if docker_run and index < 2:
            continue
        if docker_run and not docker_image_seen:
            if skip_docker_value:
                skip_docker_value = False
                continue
            if normalized in _DOCKER_VALUE_FLAGS:
                skip_docker_value = True
                continue
            if any(normalized.startswith(f"{flag}=") for flag in _DOCKER_VALUE_FLAGS):
                continue
            if normalized.startswith("-"):
                continue
            # The first non-option after docker run is the image name. Docker
            # runtime identifiers and mount destinations are not repository
            # paths; only the command after the image belongs to this gate.
            docker_image_seen = True
            command_position = True
            continue
        if normalized in _SHELL_COMMAND_SEPARATORS:
            command_position = True
            skip_code_argument = False
            skip_runtime_path_value = False
            continue
        if skip_code_argument:
            skip_code_argument = False
            command_position = False
            continue
        if skip_runtime_path_value:
            skip_runtime_path_value = False
            command_position = False
            continue
        lowered = normalized.lower()
        if lowered in _CODE_ARGUMENT_FLAGS:
            skip_code_argument = True
            command_position = False
            continue
        if lowered in _RUNTIME_PATH_VALUE_FLAGS:
            skip_runtime_path_value = True
            command_position = False
            continue
        # The executable selects the host tool, not a repository input. Keep
        # relative command paths in scope so project-owned scripts still need
        # an explicit task claim. Package-manager shims are generated
        # dependency tools, however, and are not task-owned source paths.
        if command_position and normalized.startswith("/"):
            command_position = False
            continue
        if command_position and normalized.startswith(
            ("./node_modules/.bin/", "node_modules/.bin/")
        ):
            command_position = False
            continue
        command_position = False
        for cleaned in _path_ref_candidates_from_shell_token(token):
            if "::" in cleaned:
                cleaned = cleaned.split("::", 1)[0]
            if _looks_like_path_ref(cleaned):
                refs.append(cleaned)
    return list(dict.fromkeys(refs))


def _path_ref_candidates_from_shell_token(token: str) -> list[str]:
    cleaned = _clean_path_ref_token(token)
    if not cleaned or cleaned.startswith("-") or "://" in cleaned:
        return []
    if cleaned.startswith("$("):
        inner = cleaned[2:].strip()
        if inner.endswith(")"):
            inner = inner[:-1].strip()
        try:
            inner_tokens = shlex.split(inner)
        except ValueError:
            inner_tokens = inner.split()
        out: list[str] = []
        for inner_token in inner_tokens:
            out.extend(_path_ref_candidates_from_shell_token(inner_token))
        return out
    if any(ch.isspace() for ch in cleaned):
        return []
    if "=" in cleaned and not cleaned.startswith(("./", "../", "/")):
        cleaned = cleaned.rsplit("=", 1)[-1]
    cleaned = _clean_path_ref_token(cleaned)
    return [cleaned] if cleaned else []


def _clean_path_ref_token(token: str) -> str:
    cleaned = str(token or "").strip().strip("'\"")
    return cleaned.rstrip(".,;:)]}，。；：、）】》")


def _looks_like_path_ref(token: str) -> bool:
    if token in {".", ".."}:
        return False
    # Content-addressed OCI image names use slash-separated repository names,
    # but they are runtime identifiers rather than repository filesystem refs.
    if _OCI_DIGEST_REF_RE.fullmatch(token):
        return False
    if token.startswith(("./", "../", "/")):
        return True
    return "/" in token and not token.startswith("-")
