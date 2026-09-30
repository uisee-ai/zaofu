"""Host driver protocol contracts, without launching a VM."""

import yaml
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.testkit.isolated_vm_run import NETWORK_CONFIG, guest_boot_command, markers


def test_marker_reader_tolerates_console_noise_and_incomplete_tail():
    console = 'kernel noise\n{"zf_k571_guest":"finished","returncode":0}\n{"partial":'
    assert markers(console, '') == [{'zf_k571_guest': 'finished', 'returncode': 0}]
    assert markers('noise\nZF_K571_BOOT {"mode":"firstboot"}\n', 'ZF_K571_BOOT ') == [
        {'mode': 'firstboot'},
    ]


def test_guest_boot_program_is_valid_python_with_fixed_manifest_and_nonce():
    script = guest_boot_command('nonce-test', 'a' * 64)
    compile(script, '<guest-bootcmd>', 'exec')
    assert "'nonce-test'" in script and repr('a' * 64) in script
    assert "'tests.testkit.isolated_vm_scenario'" in script
    assert "'ro'" in script and 'guest-observation/state.json' in script
    assert 'os.sync' not in script and 'fsync' not in script


def test_no_network_seed_declares_only_an_optional_wildcard_link():
    configuration = yaml.safe_load(yaml.safe_dump(NETWORK_CONFIG))
    assert configuration['version'] == 2 and 'network' not in configuration
    [link] = configuration['ethernets'].values()
    assert link == {'match': {'name': 'en*'}, 'dhcp4': False, 'dhcp6': False, 'optional': True}


@pytest.mark.parametrize('damage', ['none', 'phase', 'nonce', 'boot', 'uuid', 'network', 'kdump', 'restore', 'disabled'])
def test_panic_bootcmd_requires_completed_guest_identity_before_fault(tmp_path, damage):
    nonce = 'a' * 32
    script = guest_boot_command(nonce, 'b' * 64, panic=damage != 'disabled')
    tree = ast.parse(script)
    # Execute the actual guarded tail from the emitted guest program against
    # a fake /proc and /sys only. Never run bootcmd mount/CLI or host sysrq.
    tail = tree.body[1].body[-1]
    assert isinstance(tail, ast.If)
    code = compile(ast.fix_missing_locations(ast.Module(body=[tail], type_ignores=[])), '<guest-tail>', 'exec')
    root = tmp_path / 'guest'
    (root / 'guest-observation').mkdir(parents=True)
    state = {'phase': 'firstboot_complete', 'nonce': nonce, 'first_boot_id': 'boot-a'}
    checkpoint = {'boot_id': 'boot-a', 'workflow_run_id': 'run-a', 'first_attempt': {'attempt_id': 'a'}}
    if damage == 'phase': state['phase'] = 'firstboot_started'
    if damage == 'nonce': state['nonce'] = 'foreign'
    if damage == 'boot': checkpoint['boot_id'] = 'boot-b'
    (root / 'guest-observation/state.json').write_text(json.dumps(state))
    (root / 'checkpoint.json').write_text(json.dumps(checkpoint))
    fake = tmp_path / 'virtual'
    values = {'proc/sys/kernel/random/boot_id': 'boot-a',
              'sys/class/dmi/id/product_uuid': 'foreign' if damage == 'uuid' else nonce,
              'sys/kernel/kexec_crash_loaded': '1' if damage == 'kdump' else '0',
              'proc/sys/kernel/panic': '-1', 'proc/sysrq-trigger': ''}
    for key, value in values.items():
        path = fake / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    (fake / 'sys/class/net/lo').mkdir(parents=True)
    if damage == 'network': (fake / 'sys/class/net/eth0').mkdir()
    def virtual_path(value):
        assert Path(value).is_absolute()
        return fake / value.lstrip('/')
    env = {'json': json, 'pathlib': SimpleNamespace(Path=virtual_path), 'root': root,
           'mode': 'restore' if damage == 'restore' else 'firstboot'}
    if damage == 'none':
        with pytest.raises(RuntimeError, match='unexpectedly returned'):
            exec(code, env)
        assert (fake / 'proc/sysrq-trigger').read_text() == 'c'
    else:
        if damage in {'restore', 'disabled'}:
            exec(code, env)
        else:
            with pytest.raises(AssertionError): exec(code, env)
        assert (fake / 'proc/sysrq-trigger').read_text() == ''
        assert (fake / 'proc/sys/kernel/panic').read_text() == '-1'
