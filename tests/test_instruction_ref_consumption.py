"""W1:instruction_ref/criteria_ref 的 briefing 消费半边(doc 90 §6.1)。"""

from __future__ import annotations

from zf.runtime.injection import materialize_instruction_refs


class TestMaterializeRefs:
    def test_ref_resolves_to_file_content(self, tmp_path):
        (tmp_path / "skills").mkdir()
        (tmp_path / "skills" / "scan.md").write_text("# 扫描方法论\n按契约冻结。")
        out = materialize_instruction_refs(
            {"child_id": "c1", "instruction_ref": "skills/scan.md"},
            project_root=tmp_path,
        )
        assert out["instruction"].startswith("# 扫描方法论")
        assert out["instruction_ref"] == "skills/scan.md"  # provenance 保留

    def test_criteria_ref_same_mechanism(self, tmp_path):
        (tmp_path / "acceptance.md").write_text("必须真环境跑通")
        out = materialize_instruction_refs(
            {"criteria_ref": "acceptance.md"}, project_root=tmp_path,
        )
        assert out["criteria"] == "必须真环境跑通"

    def test_explicit_wins_mutual_exclusion(self, tmp_path):
        (tmp_path / "x.md").write_text("ref content")
        out = materialize_instruction_refs(
            {"instruction": "explicit", "instruction_ref": "x.md"},
            project_root=tmp_path,
        )
        assert out["instruction"] == "explicit"
        assert "ignored" in out["instruction_ref_note"]

    def test_missing_ref_visible_not_silent(self, tmp_path):
        out = materialize_instruction_refs(
            {"instruction_ref": "skills/none.md"}, project_root=tmp_path,
        )
        assert "[instruction_ref missing: skills/none.md]" == out["instruction"]

    def test_escape_rejected(self, tmp_path):
        for bad in ("/etc/passwd", "../out.md"):
            out = materialize_instruction_refs(
                {"instruction_ref": bad}, project_root=tmp_path,
            )
            assert "rejected (escape)" in out["instruction"]

    def test_payload_without_refs_passthrough_same_object_shape(self):
        payload = {"instruction": "x"}
        assert materialize_instruction_refs(
            payload, project_root="/tmp") == payload
