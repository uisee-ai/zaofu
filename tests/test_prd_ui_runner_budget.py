import pytest

from tests.e2e.prd_ui import run


@pytest.mark.parametrize('turn', ['0', '-1', '601'])
def test_invalid_ui_wait_budget_never_starts_a_provider_or_container(monkeypatch, capsys, turn):
    monkeypatch.setattr('sys.argv', ['run.py', '--provider', 'real', '--journey', 'channel',
                                   '--timeout', '600', '--turn-timeout', turn])
    monkeypatch.setattr(run, 'command', lambda *a, **kw: pytest.fail('invalid budget started a process'))
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 2
    assert 'turn-timeout' in capsys.readouterr().err
