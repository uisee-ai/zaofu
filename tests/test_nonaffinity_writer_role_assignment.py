from types import SimpleNamespace

from zf.runtime.orchestrator_fanout import assign_nonaffinity_writer_roles


def _role(instance_id):
    return SimpleNamespace(instance_id=instance_id)


def test_assigns_by_owner_role_not_position():
    # owner_role swapped vs list position: task0 -> dev-web, task1 -> dev-api.
    roles = [_role("dev-api"), _role("dev-web"), _role("dev-runtime")]
    tasks = [
        {"task_id": "T-FEATURES", "owner_role": "dev-runtime"},
        {"task_id": "T-CORE", "owner_role": "dev-api"},
        {"task_id": "T-CLI", "owner_role": "dev-web"},
    ]
    assigned = assign_nonaffinity_writer_roles(tasks, roles)
    assert [r.instance_id for r in assigned] == ["dev-runtime", "dev-api", "dev-web"]


def test_positional_fallback_when_no_owner_role():
    # owner_role-less plans keep the prior positional behaviour.
    roles = [_role("dev-1"), _role("dev-2")]
    tasks = [{"task_id": "T-1"}, {"task_id": "T-2"}]
    assigned = assign_nonaffinity_writer_roles(tasks, roles)
    assert [r.instance_id for r in assigned] == ["dev-1", "dev-2"]


def test_unmatched_owner_role_falls_back_positionally():
    # owner_role that is not a fanout role (e.g. the role NAME "dev") falls back.
    roles = [_role("dev-1"), _role("dev-2")]
    tasks = [
        {"task_id": "T-1", "owner_role": "dev"},
        {"task_id": "T-2", "owner_role": "dev"},
    ]
    assigned = assign_nonaffinity_writer_roles(tasks, roles)
    assert [r.instance_id for r in assigned] == ["dev-1", "dev-2"]


def test_mixed_match_and_fallback_no_double_assignment():
    # one task names a specific role; the other has none. No role is reused.
    roles = [_role("dev-api"), _role("dev-web")]
    tasks = [
        {"task_id": "T-1"},                       # no owner -> fallback
        {"task_id": "T-2", "owner_role": "dev-api"},  # matches dev-api
    ]
    assigned = assign_nonaffinity_writer_roles(tasks, roles)
    ids = [r.instance_id for r in assigned]
    assert ids[1] == "dev-api"          # honored
    assert ids[0] == "dev-web"          # fallback took the only leftover
    assert len(set(ids)) == 2           # no double-assignment
