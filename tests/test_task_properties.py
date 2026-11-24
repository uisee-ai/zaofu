from fastapi.testclient import TestClient

from zf.core.events.log import EventLog
from zf.core.events.model import ZfEvent
from zf.core.feature.schema import Feature
from zf.core.feature.store import FeatureStore
from zf.core.task.schema import Task, TaskContract
from zf.core.task.store import TaskStore
from zf.core.workspace import stable_project_id
from zf.web.server import create_app


def test_reader_projects_current_task_relations_and_last_milestone(tmp_path):
    state = tmp_path / "custom-state"
    state.mkdir()
    store = TaskStore(state / "kanban.json")
    store.add(Task(id="UP", title="准备数据"))
    store.update("UP", status="done")
    store.add(Task(id="PARENT", title="训练工作台"))
    store.add(Task(id="TRAIN", title="恢复训练", blocked_by=["UP"], status="blocked",
                   contract=TaskContract(parent_task_id="PARENT", feature_id="F1")))
    store.add(Task(id="NEXT", title="发布模型", blocked_by=["TRAIN"]))
    store.add(Task(id="OTHER", title="人工检查", blocked_by=["TRAIN"], status="blocked"))
    store.add(Task(id="CHILD", title="保存结果", contract=TaskContract(parent_task_id="TRAIN")))
    store.update("CHILD", status="done")
    FeatureStore(state / "feature_list.json").add(Feature(id="F1", title="模型训练"))
    log = EventLog(state / "events.jsonl")
    change = ZfEvent(type="task.status_changed", task_id="TRAIN", payload={"from": "in_progress", "to": "blocked"})
    log.append(change)
    log.append(ZfEvent(type="task.status_changed", task_id="OTHER", payload={"to": "done", "ref": "TRAIN"}))
    for _ in range(81):
        log.append(ZfEvent(type="worker.heartbeat", task_id="TRAIN"))

    with TestClient(create_app(state, project_root=tmp_path)) as client:
        project = stable_project_id(name="test", root=tmp_path)
        url = f"/api/projects/{project}/tasks/TRAIN"
        response = client.get(url)
        assert response.status_code == 200
        info = response.json()["properties"]
        assert info["milestone"] == {"label": "进入阻塞", "at": change.ts}
        assert info["upstream"] == [{"id": "UP", "title": "准备数据", "status": "done"}]
        assert {row["id"]: row["waiting"] for row in info["downstream"]} == {"NEXT": True, "OTHER": False}
        assert info["children"] == [{"id": "CHILD", "title": "保存结果", "status": "done"}]
        assert info["source"]["kind"] == "task" and info["source"]["id"] == "PARENT"
        store.update("TRAIN", status="done", contract=TaskContract(feature_id="F1"))
        updated = client.get(url).json()["properties"]
        assert not any(row["waiting"] for row in updated["downstream"])
        assert updated["source"] == {"kind": "feature", "id": "F1", "title": "模型训练"}
