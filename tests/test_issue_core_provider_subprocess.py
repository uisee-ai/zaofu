from __future__ import annotations

import json
import sys

from issue_core import ProviderRequest, SubprocessJsonProvider


def test_subprocess_json_provider_is_host_neutral() -> None:
    script = (
        "import json,sys; req=json.load(sys.stdin); "
        "print(json.dumps({'ok': True, 'text': json.dumps({'status':'ready','report':{'title':req['user']}})}))"
    )
    provider = SubprocessJsonProvider((sys.executable, "-c", script), timeout_seconds=5)
    response = provider.complete(ProviderRequest(system="s", user="u", context={}))
    assert response.ok is True
    assert json.loads(response.text)["report"]["title"] == "u"
