from pathlib import Path
from zf.runtime.result_workspace_guidance import result_workspace_guidance


def test_scope_explains_exact_exception_without_granting_directory_access():
    text = '\n'.join(result_workspace_guidance(Path('/state/artifacts/attempt/result.json')))
    assert '/state/artifacts/attempt/result.json' in text
    assert 'outside the product checkout' in text
    assert 'not permission to write its directory' in text
    assert 'Reader source checkouts and evidence inputs are read-only' in text
    assert 'events.jsonl' in text and 'kanban.json' in text
