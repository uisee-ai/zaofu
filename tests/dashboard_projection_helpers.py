"""Wait for asynchronous test projections without treating pending as empty."""
import time


def ready_get(client, path, **kwargs):
    deadline = time.monotonic() + 20
    while True:
        response = client.get(path, **kwargs)
        if response.status_code == 304:
            return response
        state = response.json().get("read_projection", {}).get("state")
        if response.status_code != 202 and state not in {"building", "refreshing"}:
            return response
        assert time.monotonic() < deadline, response.text
        time.sleep(0.02)
