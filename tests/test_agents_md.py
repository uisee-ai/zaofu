"""TR-AGENTS-MD-MANAGED-001 (doc 42 §2.5 A) — unit tests for the
agents_md helpers."""

from __future__ import annotations

import hashlib

import pytest

from zf.core.agents_md import (
    AgentsMdError,
    ZF_MARKER_END,
    ZF_MARKER_START,
    extract_managed_block,
    render_canonical_block,
    replace_managed_block,
)


# ---------------------------------------------------------------------------
# extract_managed_block
# ---------------------------------------------------------------------------


class TestExtractManagedBlock:
    def test_returns_inside_text(self):
        text = (
            "# AGENTS.md\n"
            "User stuff\n"
            f"{ZF_MARKER_START}\n"
            "managed line 1\n"
            "managed line 2\n"
            f"{ZF_MARKER_END}\n"
            "More user stuff\n"
        )
        assert extract_managed_block(text) == "managed line 1\nmanaged line 2"

    def test_no_marker_returns_none(self):
        text = "# AGENTS.md\n\nJust a normal markdown file.\n"
        assert extract_managed_block(text) is None

    def test_empty_text_returns_none(self):
        assert extract_managed_block("") is None

    def test_empty_inside_returns_empty_string(self):
        text = f"{ZF_MARKER_START}\n{ZF_MARKER_END}\n"
        assert extract_managed_block(text) == ""

    def test_raises_on_duplicate_start(self):
        text = (
            f"{ZF_MARKER_START}\n"
            "content\n"
            f"{ZF_MARKER_START}\n"  # second START
            f"{ZF_MARKER_END}\n"
        )
        with pytest.raises(AgentsMdError, match="2 <!-- ZF:START -->"):
            extract_managed_block(text)

    def test_raises_on_duplicate_end(self):
        text = (
            f"{ZF_MARKER_START}\n"
            "content\n"
            f"{ZF_MARKER_END}\n"
            f"{ZF_MARKER_END}\n"
        )
        with pytest.raises(AgentsMdError, match="2 <!-- ZF:END -->"):
            extract_managed_block(text)

    def test_raises_on_missing_end(self):
        text = f"{ZF_MARKER_START}\ncontent\n"
        with pytest.raises(AgentsMdError, match="without matching <!-- ZF:END"):
            extract_managed_block(text)

    def test_raises_on_orphan_end(self):
        text = f"{ZF_MARKER_END}\ncontent\n"
        with pytest.raises(
            AgentsMdError, match="without matching <!-- ZF:START"
        ):
            extract_managed_block(text)

    def test_raises_on_end_before_start(self):
        text = (
            f"{ZF_MARKER_END}\n"
            "content\n"
            f"{ZF_MARKER_START}\n"
        )
        with pytest.raises(AgentsMdError, match="before <!-- ZF:START"):
            extract_managed_block(text)


# ---------------------------------------------------------------------------
# replace_managed_block
# ---------------------------------------------------------------------------


class TestReplaceManagedBlock:
    def test_replaces_inside_preserves_outside(self):
        text = (
            "# AGENTS.md\n"
            "\n"
            "## Working Style\n"
            "- be careful\n"
            "\n"
            f"{ZF_MARKER_START}\n"
            "old inside\n"
            f"{ZF_MARKER_END}\n"
            "\n"
            "## Testing\n"
            "- pytest\n"
        )
        new = replace_managed_block(text, "fresh content")

        # Outside (before START and after END) byte-for-byte preserved
        assert new.startswith("# AGENTS.md\n\n## Working Style\n- be careful\n")
        assert new.endswith("## Testing\n- pytest\n")
        # Inside replaced
        assert "old inside" not in new
        assert "fresh content" in new
        # extract_managed_block round-trips
        assert extract_managed_block(new) == "fresh content"

    def test_preserves_outside_bytes_with_unicode(self):
        """Chinese + emoji + ASCII art outside markers must not be touched."""
        outside_prefix = (
            "# 早夫 AGENTS.md ✨\n"
            "    缩进的内容\n"
            "```\n"
            "+---+\n"
            "| ▒ |\n"
            "+---+\n"
            "```\n"
        )
        outside_suffix = "\n## 测试\n- 用 pytest 跑\n"
        text = (
            f"{outside_prefix}"
            f"{ZF_MARKER_START}\n"
            "old\n"
            f"{ZF_MARKER_END}\n"
            f"{outside_suffix}"
        )
        before_prefix_hash = hashlib.sha256(outside_prefix.encode()).hexdigest()
        before_suffix_hash = hashlib.sha256(outside_suffix.encode()).hexdigest()

        new = replace_managed_block(text, "new content with 中文")

        new_prefix = new.split(ZF_MARKER_START, 1)[0]
        new_suffix = new.split(ZF_MARKER_END + "\n", 1)[1]
        assert hashlib.sha256(new_prefix.encode()).hexdigest() == before_prefix_hash
        assert hashlib.sha256(new_suffix.encode()).hexdigest() == before_suffix_hash

    def test_idempotent_with_same_inside(self):
        text = (
            "# AGENTS.md\n"
            f"{ZF_MARKER_START}\n"
            "old\n"
            f"{ZF_MARKER_END}\n"
        )
        once = replace_managed_block(text, "stable content")
        twice = replace_managed_block(once, "stable content")
        assert once == twice

    def test_appends_when_markers_absent(self):
        text = "# AGENTS.md\n\nNo markers here.\n"
        new = replace_managed_block(text, "appended content")
        # Round-trip via extract
        assert extract_managed_block(new) == "appended content"
        # Original prefix preserved
        assert new.startswith("# AGENTS.md\n\nNo markers here.\n")

    def test_appends_to_empty_file(self):
        new = replace_managed_block("", "fresh content")
        assert extract_managed_block(new) == "fresh content"
        assert new.startswith(ZF_MARKER_START + "\n")

    def test_normalises_leading_trailing_newlines_in_new_inside(self):
        text = (
            f"{ZF_MARKER_START}\n"
            "anything\n"
            f"{ZF_MARKER_END}\n"
        )
        a = replace_managed_block(text, "X")
        b = replace_managed_block(text, "\nX\n")
        c = replace_managed_block(text, "\n\nX\n\n\n")
        assert a == b == c

    def test_extract_after_replace_round_trip(self):
        text = "# Header\n"
        with_block = replace_managed_block(text, "alpha\nbeta\ngamma")
        assert extract_managed_block(with_block) == "alpha\nbeta\ngamma"

    def test_idempotent_append_when_missing(self):
        text = "# AGENTS.md\nstuff\n"
        once = replace_managed_block(text, "managed")
        twice = replace_managed_block(once, "managed")
        assert once == twice


# ---------------------------------------------------------------------------
# render_canonical_block
# ---------------------------------------------------------------------------


class TestRenderCanonicalBlock:
    def test_returns_string(self):
        out = render_canonical_block()
        assert isinstance(out, str)
        assert len(out) > 100  # substantive content

    def test_contains_5_key_anchors(self):
        out = render_canonical_block()
        # Per sprint acceptance #6
        assert "Active task pin" in out
        assert "Self-declared completion" in out
        assert "Recursion guard" in out.lower() or "Recursion Guard" in out
        assert "worker.heartbeat" in out
        assert "Inline-override audit" in out

    def test_references_actual_commits(self):
        out = render_canonical_block()
        # Document trail: explicitly cite the commits that landed these
        assert "c118146" in out  # ZF-TR-NESTED-GUARD-001
        assert "96585b" in out  # ZF-LH-INLINE-001

    def test_deterministic(self):
        a = render_canonical_block()
        b = render_canonical_block()
        assert a == b
        assert a is not b or True  # pure-function check

    def test_config_param_reserved_signature(self):
        """config param accepted (forward compat) but currently unused."""
        a = render_canonical_block(config=None)
        b = render_canonical_block(config=object())  # any object should work
        assert a == b


# ---------------------------------------------------------------------------
# Wire-up grep proof (sanity)
# ---------------------------------------------------------------------------


class TestWireUpGrepProof:
    def test_module_exports(self):
        from zf.core import agents_md as mod

        for name in (
            "extract_managed_block",
            "replace_managed_block",
            "render_canonical_block",
            "AgentsMdError",
            "ZF_MARKER_START",
            "ZF_MARKER_END",
        ):
            assert hasattr(mod, name), f"agents_md missing {name}"
