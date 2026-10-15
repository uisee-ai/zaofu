from pathlib import Path

import pytest
from fastapi import HTTPException

from zf.web.channel_file_preview import channel_file_preview


def preview(
    root: Path, ref: str, *, declared: str | None = None, thread: str = "main", **extra
):
    return channel_file_preview(
        project_root=root,
        state_dir=root / "custom-state",
        channel={
            "messages": [
                {
                    "thread_id": "main",
                    "refs": {"artifacts": [{"path": declared or ref, **extra}]},
                }
            ]
        },
        thread_id=thread,
        ref=ref,
    )


def test_complete_markdown_and_custom_state(tmp_path):
    state = tmp_path / "custom-state"
    state.mkdir()
    body = "# PRD\n\n| A | B |\n|---|---|\n|1|2|\n\n最后一段完整保留。"
    (state / "result.md").write_text(body)
    result = preview(tmp_path, "result.md")
    assert result["content"] == body
    assert result["format"] == "markdown"
    assert result["byte_count"] == len(body.encode())


@pytest.mark.parametrize("suffix", [" §1—3", " §10及§10.1—10.7", ":12-20", ":L12-L20"])
def test_declared_citation_previews_complete_file_and_keeps_authorized_ref(tmp_path, suffix):
    import hashlib

    body = "# Complete PRD\n\nFirst and last sections.\n"
    (tmp_path / "Full PRD.md").write_text(body)
    ref = "Full PRD.md" + suffix
    result = preview(tmp_path, ref, sha256=hashlib.sha256(body.encode()).hexdigest())
    assert result["content"] == body
    assert result["ref"] == ref
    assert result["name"] == "Full PRD.md"
    assert result["format"] == "markdown"


@pytest.mark.parametrize("requested,declared", [
    ("PRD.md", "PRD.md §1"),
    ("PRD.md §2", "PRD.md §1"),
    ("PRD.md §1", "PRD.md"),
])
def test_citation_does_not_expand_declared_reference_authority(tmp_path, requested, declared):
    (tmp_path / "PRD.md").write_text("# PRD")
    with pytest.raises(HTTPException) as error:
        preview(tmp_path, requested, declared=declared)
    assert error.value.status_code == 403


@pytest.mark.parametrize("ref,code", [
    ("../PRD.md §1", 403),
    (".private.md §1", 403),
    ("PRD.md §1; other.md §2", 415),
    ("PRD.md, other.md §2", 415),
    ("PRD.md:1; other.md:2", 415),
    ("PRD.md §unstructured explanation", 415),
    ("link.md §1", 403),
])
def test_citation_keeps_path_guards(tmp_path, ref, code):
    (tmp_path / "PRD.md").write_text("# PRD")
    (tmp_path / "link.md").symlink_to(tmp_path / "PRD.md")
    with pytest.raises(HTTPException) as error:
        preview(tmp_path, ref)
    assert error.value.status_code == code


def test_citation_keeps_digest_guard(tmp_path):
    (tmp_path / "PRD.md").write_text("# Changed")
    with pytest.raises(HTTPException) as error:
        preview(tmp_path, "PRD.md §1", sha256="stale")
    assert error.value.status_code == 409


@pytest.mark.parametrize(
    "case,code",
    [
        ("unreferenced", 403),
        ("thread", 403),
        ("parent", 403),
        ("secret", 403),
        ("symlink", 403),
        ("directory_symlink", 403),
        ("missing", 404),
        ("changed", 409),
        ("large", 413),
        ("binary", 415),
        ("credentials", 403),
        ("unsupported", 415),
        ("outside", 403),
    ],
)
def test_fail_closed(tmp_path, case, code):
    ref = "result.md"
    (tmp_path / ref).write_text("# Safe")
    kwargs = {}
    if case == "unreferenced":
        kwargs["declared"] = "other.md"
    elif case == "thread":
        kwargs["thread"] = "other"
    elif case == "parent":
        ref = "../result.md"
    elif case == "secret":
        ref = ".env.md"
    elif case == "symlink":
        (tmp_path / "link.md").symlink_to(tmp_path / ref)
        ref = "link.md"
    elif case == "directory_symlink":
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        ref = "link/result.md"
    elif case == "missing":
        ref = "missing.md"
    elif case == "changed":
        kwargs["sha256"] = "wrong"
    elif case == "large":
        (tmp_path / ref).write_text("a" * (4 * 1024 * 1024 + 1))
    elif case == "binary":
        (tmp_path / ref).write_bytes(b"\xff\xfe")
    elif case == "credentials":
        (tmp_path / ref).write_text("API_KEY=sk-this-is-a-secret-value-1234567890")
    elif case == "unsupported":
        ref = "image.png"
    elif case == "outside":
        ref = "/tmp/unrelated.md"
    with pytest.raises(HTTPException) as error:
        preview(tmp_path, ref, **kwargs)
    assert error.value.status_code == code


@pytest.mark.parametrize("link", ["[PRD](docs/PRD.md)", '[PRD](docs/PRD.md "Draft")', "[PRD](<docs/PRD.md>)"])
def test_markdown_message_link_authorizes_only_the_same_thread(tmp_path, link):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/PRD.md").write_text("# Complete\nLast line")
    channel = {"messages": [{"thread_id": "main", "text": link}]}
    args = dict(project_root=tmp_path, state_dir=tmp_path / "state", channel=channel, ref="docs/PRD.md")
    assert channel_file_preview(**args, thread_id="main")["content"].endswith("Last line")
    with pytest.raises(HTTPException) as error:
        channel_file_preview(**args, thread_id="other")
    assert error.value.status_code == 403


@pytest.mark.parametrize("text", ["docs/PRD.md", "![image](docs/PRD.md)", "[remote](https://host/docs/PRD.md)"])
def test_plain_paths_images_and_remote_urls_do_not_grant_file_access(tmp_path, text):
    with pytest.raises(HTTPException) as error:
        channel_file_preview(project_root=tmp_path, state_dir=tmp_path / "state",
                             channel={"messages": [{"text": text}]}, thread_id="main", ref="docs/PRD.md")
    assert error.value.status_code == 403
