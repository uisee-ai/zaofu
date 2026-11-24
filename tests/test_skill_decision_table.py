from fastapi import FastAPI
from fastapi.testclient import TestClient
from types import SimpleNamespace

from zf.runtime.skill_evaluation_projection import _database, _publish
from zf.web.artifact_query_routes import build_artifact_query_router


def test_project_skill_table_uses_latest_card_and_preserves_unknowns(tmp_path):
    state = tmp_path / "one"
    _database(state).parent.mkdir(parents=True)
    card = dict(
        rubric_version="v1",
        terminal_event_id="e",
        workflow_run_id="run",
        operation_id="op",
        attempt_id="try",
        dispatch_id="d",
        task_id="T",
        role="verify",
        task_outcome="passed",
        independent_review={
            "status": "abstained",
            "decision": {"result": "unknown", "critical_false_release": False},
        },
        provider="codex",
        model=None,
        configured_model="m",
        harness_version=None,
        output_profile_id="task-verify",
        skills=[
            dict(
                name="method",
                digest="d0",
                materialization="resolved",
                read_status="unknown",
                auto_inject=False,
                load_on_demand=True,
            )
        ],
    )
    _publish(_database(state), iter([card]))
    newer = {
        **card,
        "skills": [{**card["skills"][0], "read_status": "observed_header"}],
    }
    _publish(_database(state), iter([newer]))
    other = {**newer, "attempt_id": "retry", "configured_model": "m2"}
    _publish(_database(state), iter([other]))
    app = FastAPI()
    app.include_router(
        build_artifact_query_router(
            state_dir=state,
            project_root=tmp_path,
            config=None,
            resolve_ctx=lambda project: SimpleNamespace(state_dir=tmp_path / project),
        )
    )
    client = TestClient(app)
    response = client.get("/api/projects/one/skill-decisions")
    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 2 and {r["configured_model"] for r in rows} == {"m", "m2"}
    assert all(r["dispatches"] == r["tasks"] == r["observed_reads"] == 1 for r in rows)
    assert all(
        r["model"] is None
        and r["harness_version"] is None
        and r["confirmed_false_releases"] is None
        for r in rows
    )
    assert rows[0]["evidence"]["task_id"] == "T" and rows[0]["mode"] == "on_demand"
    assert client.get("/api/projects/two/skill-decisions").json() == {"rows": []}
    _publish(
        _database(state),
        iter(
            [
                {
                    **newer,
                    "independent_review": {
                        "status": "reviewed",
                        "decision": {"result": "fail", "critical_false_release": True},
                    },
                }
            ]
        ),
    )
    rows = client.get("/api/projects/one/skill-decisions").json()["rows"]
    assert rows[0]["reviewed_dispatches"] == rows[0]["confirmed_false_releases"] == 1
    assert rows[1]["confirmed_false_releases"] is None
