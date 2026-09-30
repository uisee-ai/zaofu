from .capabilities import allocate_port, docker_available


def test_allocate_port_returns_bindable_ephemeral_port():
    port = allocate_port()
    assert 0 < port < 65536


def test_docker_probe_returns_structured_result():
    available, reason = docker_available()
    assert isinstance(available, bool)
    assert reason
