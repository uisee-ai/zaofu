from copy import deepcopy
import hashlib
import json

import pytest

from zf.runtime.plan_structural_delta import make_delta, make_verified_delta, replay_delta


def test_delta_replays_complete_nested_changes_without_mutating_inputs():
    old = {"tasks": [{"id": "A", "base": "old", "refs": ["old"]}, {"id": "B"}],
           "shared": {"unchanged": "a" * 100_000, "remove": 1}}
    new = deepcopy(old)
    new["tasks"][0].update(base="new", refs=["old", "new"])
    new["shared"].pop("remove")
    new["shared"]["added"] = {"required": True}
    original = deepcopy(old)
    delta = make_delta(old, new)
    assert replay_delta(old, delta) == new
    assert old == original
    assert len(json.dumps(delta)) < 5000


@pytest.mark.parametrize("mutation", ["baseline", "candidate", "old_value", "new_value", "path", "operation"])
def test_delta_fails_closed_on_tampering(mutation):
    old = {"tasks": [{"base": "old"}], "shared": "same"}
    new = {"tasks": [{"base": "new"}], "shared": "same"}
    delta = make_delta(old, new)
    if mutation == "baseline": old["shared"] = "wrong"
    if mutation == "candidate": delta["new_sha256"] = "0" * 64
    if mutation == "old_value": delta["changes"][0]["old"] = "wrong"
    if mutation == "new_value": delta["changes"][0]["new"] = "wrong"
    if mutation == "path": delta["changes"][0]["path"] = ["tasks", -1, "base"]
    if mutation == "operation": delta["changes"][0]["op"] = "remove"
    with pytest.raises(ValueError):
        replay_delta(old, delta)


@pytest.mark.parametrize("old,new", [(1, True), (1, 1.0), ({"x": 1}, [1]), ([], [1])])
def test_delta_preserves_json_type_and_root_replacements(old, new):
    assert json.dumps(replay_delta(old, make_delta(old, new))) == json.dumps(new)


def test_verified_delta_binds_exact_source_bytes():
    old = b'{ "x": 1 }'
    new = b'{"x":2}'
    digest = lambda value: hashlib.sha256(value).hexdigest()
    delta = make_verified_delta(old, new, old_sha256=digest(old), new_sha256=digest(new))
    assert delta["old_file_sha256"] == digest(old)
    assert delta["new_file_sha256"] == digest(new)
    assert replay_delta(json.loads(old), delta) == json.loads(new)
    with pytest.raises(ValueError):
        make_verified_delta(old + b' ', new, old_sha256=digest(old), new_sha256=digest(new))


@pytest.mark.parametrize("bad", [b'{"x":1,"x":2}', b'{"x":NaN}'])
def test_verified_delta_rejects_ambiguous_json(bad):
    digest = hashlib.sha256(bad).hexdigest()
    with pytest.raises(ValueError):
        make_verified_delta(bad, bad, old_sha256=digest, new_sha256=digest)
