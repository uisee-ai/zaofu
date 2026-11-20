"""Large bound source for actual API/browser chapter-reading qualification."""
import hashlib
import json

from zf.core.events.model import ZfEvent


def add_source_document_fixture(state, writer):
    content = "# 来源示例\n\n**开头可读**\n\n"
    content += "".join(f"## 来源章节 {i}\n\n条款内容 {i}\n\n" for i in range(130))
    content += "## 超长来源\n\n" + "数据😀" * 15000 + "\nEND-LONG-SOURCE\n"
    content += "\n## 最后来源\n\nEND-OF-BOUND-SOURCE\n"
    (state / "artifacts/chapter-source.md").write_text(content)
    bundle = json.dumps({"schema_version": "plan-preview-bundle.v1", "artifacts": [{
        "name": "requirement_source", "ref": "artifacts/chapter-source.md",
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }]}).encode()
    (state / "artifacts/chapter-bundle.json").write_bytes(bundle)
    writer.append(ZfEvent(type="plan.approval.requested", actor="fixture", payload={
        "plan_id": "evt-preview-chapters", "task_count": 1,
        "plan_artifact_ref": "artifacts/full-plan.md", "task_map_ref": "artifacts/task-map.json",
        "plan_preview_bundle_ref": "artifacts/chapter-bundle.json",
        "plan_preview_bundle_digest": hashlib.sha256(bundle).hexdigest(),
    }))
