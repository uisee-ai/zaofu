"""Explicit two-boot K571 host driver, with one owned QEMU at a time."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from uuid import UUID, uuid4

import yaml

from tests.testkit.evidence import sha256_file, write_json_atomic
from tests.testkit.isolated_vm_supervisor import OwnedProcessSupervisor


BASE_SHA = "46b0dbaffa6950a7da5ff2dc5ed34c46084610b3b6d1fae8f1ec2d7e953984a3"
NETWORK_CONFIG = {"version": 2, "ethernets": {"optional-guest-link": {
    "match": {"name": "en*"}, "dhcp4": False, "dhcp6": False, "optional": True,
}}}


def guest_boot_command(nonce: str, manifest_sha: str, *, panic: bool = False) -> str:
    """NoCloud bootcmd runs every boot; all guest mounts are private read-only ISO."""
    return f'''
import hashlib, json, os, pathlib, subprocess, sys, traceback
try:
    mount = pathlib.Path('/mnt/zf-k571')
    mount.mkdir(exist_ok=True)
    subprocess.run(['mount', '-t', 'iso9660', '-o', 'ro', 'LABEL=ZFPAYLOAD', str(mount)], check=True)
    manifest = mount / 'payload-manifest.json'
    assert hashlib.sha256(manifest.read_bytes()).hexdigest() == {manifest_sha!r}
    payload = mount / 'payload'
    for row in json.loads(manifest.read_text())['files']:
        path = payload / row['path']
        assert path.resolve().is_relative_to(payload.resolve()), row['path']
        if 'link' in row:
            assert os.readlink(path) == row['link'], row['path']
        else:
            raw = path.read_bytes()
            assert len(raw) == row['bytes'] and hashlib.sha256(raw).hexdigest() == row['sha256'], row['path']
    root = pathlib.Path('/var/lib/zf-k571')
    root.mkdir(mode=0o700, exist_ok=True)
    (root / 'home').mkdir(exist_ok=True)
    mode = 'restore' if (root / 'guest-observation/state.json').exists() else 'firstboot'
    env = {{'PATH': f'{{payload}}/native/bin:{{payload}}/python/bin:/usr/sbin:/usr/bin:/sbin:/bin',
           'PYTHONPATH': f'{{payload}}/repo:{{payload}}/repo/src', 'PYTHONNOUSERSITE': '1',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1', 'LANG': 'C.UTF-8',
           'HOME': str(root / 'home'), 'SHELL': '/bin/bash', 'TERM': 'xterm-256color'}}
    python = str(payload / 'python/bin/python3')
    print('ZF_K571_BOOT ' + json.dumps({{'nonce': {nonce!r}, 'mode': mode,
        'manifest_sha256': {manifest_sha!r}, 'boot_id': pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'interfaces': sorted(p.name for p in pathlib.Path('/sys/class/net').iterdir())}}), flush=True)
    result = subprocess.run([python, '-m', 'tests.testkit.isolated_vm_guest', '--mode', mode,
        '--root', str(root), '--nonce', {nonce!r}, '--', python, '-m', 'tests.testkit.isolated_vm_scenario'],
        env=env, cwd=root, timeout=170)
    assert result.returncode == 0, result.returncode
    if {panic!r} and mode == 'firstboot':
        state = json.loads((root / 'guest-observation/state.json').read_text())
        checkpoint = json.loads((root / 'checkpoint.json').read_text())
        boot_id = pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        assert state['phase'] == 'firstboot_complete' and state['nonce'] == {nonce!r}
        assert state['first_boot_id'] == checkpoint['boot_id'] == boot_id
        assert checkpoint['workflow_run_id'] and checkpoint['first_attempt']
        assert pathlib.Path('/sys/class/dmi/id/product_uuid').read_text().strip().replace('-', '').lower() == {nonce!r}
        assert sorted(p.name for p in pathlib.Path('/sys/class/net').iterdir()) == ['lo']
        assert pathlib.Path('/sys/kernel/kexec_crash_loaded').read_text().strip() == '0'
        pathlib.Path('/proc/sys/kernel/panic').write_text('0')
        print('ZF_K590_PANIC_ARMED ' + json.dumps({{'nonce': {nonce!r}, 'boot_id': boot_id,
            'interfaces': ['lo'], 'panic_timeout': 0, 'crash_loaded': False,
            'workflow_run_id': checkpoint['workflow_run_id']}}), flush=True)
        pathlib.Path('/proc/sysrq-trigger').write_text('c')
        raise RuntimeError('guest panic trigger unexpectedly returned')
except BaseException:
    traceback.print_exc()
    print('ZF_K571_BOOT_FAILED', flush=True)
    raise
'''


def markers(serial: str, prefix: str) -> list[dict]:
    found = []
    for line in serial.splitlines():
        if prefix in line:
            try:
                row = json.loads(line.split(prefix, 1)[1] if prefix else line)
            except json.JSONDecodeError:
                continue  # serial may currently end mid-record
            if isinstance(row, dict):
                found.append(row)
    return found


def resource_guard(root: Path, pid: int | None = None) -> dict:
    fields = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    memory = int(fields['MemAvailable'].split()[0])
    assert memory >= 6 * 1024**2, 'host available memory below 6 GiB'
    assert shutil.disk_usage(root).free >= 12 * 1024**3, 'host free disk below 12 GiB'
    allocated = sum(p.lstat().st_blocks * 512 for p in root.rglob('*') if p.is_file())
    assert allocated <= 6 * 1024**3, 'new run assets above 6 GiB'
    rss = 0
    if pid:
        fields = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines())
        rss = int(fields.get('VmRSS', '0').split()[0])
        assert rss <= 3 * 1024**2, 'owned QEMU RSS above 3 GiB'
    return {'host_available_kib': memory, 'new_allocated_bytes': allocated, 'qemu_rss_kib': rss}


def run_vm(root: Path, tools: Path, payload_root: Path, *, panic: bool = False) -> dict:
    root, tools, payload_root = root.absolute(), tools.resolve(), payload_root.resolve()
    assert root.parent == Path('/tmp') and root.name.startswith('zf-k571-run-')
    root.mkdir(mode=0o700, exist_ok=False)
    resource_guard(root)
    libs = tools / 'root/usr/lib/x86_64-linux-gnu'
    def tool(name, *args):
        return ['/lib64/ld-linux-x86-64.so.2', '--library-path', str(libs),
                str(tools / 'root/usr/bin' / name), *map(str, args)]
    base = tools / 'image/ubuntu-24.04-minimal-cloudimg-amd64.img'
    assert sha256_file(base) == BASE_SHA
    manifest = payload_root / 'payload-manifest.json'
    preflight = json.loads((payload_root / 'payload-preflight.json').read_text())
    assert preflight['verdict'] == 'pass'
    assert preflight['manifest_sha256'] == sha256_file(manifest)
    nonce = uuid4().hex
    report = {'verdict': 'fail', 'fault_model': 'guest-kernel-panic' if panic else 'owned-QEMU-SIGKILL', 'nonce': nonce,
              'manifest_sha256': sha256_file(manifest), 'base_sha256': BASE_SHA,
              'boots': [], 'total_budget': 600, 'boot_budget': 180}
    overlay = root / 'overlay.qcow2'
    subprocess.run(tool('qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', base, overlay),
                   check=True, capture_output=True, timeout=10)
    firmware = tools / 'root/usr/share/OVMF/OVMF_CODE_4M.fd'
    variables = root / 'OVMF_VARS.fd'
    shutil.copyfile(tools / 'root/usr/share/OVMF/OVMF_VARS_4M.fd', variables)
    subprocess.run(tool('genisoimage', '-quiet', '-output', root / 'payload.iso',
                        '-volid', 'ZFPAYLOAD', '-joliet', '-rock', '-graft-points',
                        f'payload={payload_root / "payload"}', f'payload-manifest.json={manifest}'),
                   check=True, timeout=30)
    seed = root / 'seed'
    seed.mkdir()
    (seed / 'meta-data').write_text(f'instance-id: {nonce}\nlocal-hostname: zf-k571\n')
    # Declare a wildcard optional link.  With ``-nic none`` this preserves the
    # no-network boundary while preventing systemd-networkd-wait-online from
    # treating a nonexistent required NIC as a 120-second boot dependency.
    (seed / 'network-config').write_text(yaml.safe_dump(NETWORK_CONFIG))
    config = {'package_update': False, 'package_upgrade': False, 'users': [],
              'ssh_genkeytypes': [], 'disable_root': True,
              'bootcmd': [['sh', '-c', 'exec python3 -c "$1" > /dev/ttyS0 2>&1',
                           'k571', guest_boot_command(nonce, sha256_file(manifest), panic=panic)]]}
    (seed / 'user-data').write_text('#cloud-config\n' + yaml.safe_dump(config))
    subprocess.run(tool('genisoimage', '-quiet', '-output', root / 'seed.iso',
                        '-volid', 'CIDATA', '-joliet', '-rock', seed), check=True, timeout=15)
    report['assets'] = {str(p): sha256_file(p) for p in (
        firmware, root / 'seed.iso', root / 'payload.iso', tools / 'root/usr/bin/qemu-system-x86_64',
    )}
    supervisor = None
    qmp = None
    interrupted = []
    def note_signal(signum, frame):
        interrupted.append(signum)
        if supervisor:
            supervisor.note_signal(signum, frame)
    previous = {sig: signal.signal(sig, note_signal) for sig in (signal.SIGTERM, signal.SIGINT)}
    started = time.monotonic()
    try:
        for boot, mode in ((1, 'firstboot'), (2, 'restore')):
            serial_path = root / f'serial-{boot}.log'
            command = tool('qemu-system-x86_64', '-machine', 'q35', '-accel', 'kvm', '-cpu', 'host',
                           '-smp', '1', '-m', '1536', '-nodefaults', '-display', 'none', '-vga', 'none',
                           '-monitor', 'none', '-nic', 'none', '-L', tools / 'root/usr/share/qemu',
                           '-serial', f'file:{serial_path}',
                           '-drive', f'if=pflash,format=raw,readonly=on,file={firmware}',
                           '-drive', f'if=pflash,format=raw,file={variables}',
                           '-drive', f'if=virtio,format=qcow2,cache=none,file={overlay}',
                           '-drive', f'if=virtio,format=raw,readonly=on,file={root / "seed.iso"}',
                           '-drive', f'if=virtio,format=raw,readonly=on,file={root / "payload.iso"}',
                           '-sandbox', 'on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny')
            entry = {'boot': boot, 'mode': mode, 'samples': []}
            report['boots'].append(entry)
            if panic and boot == 1:
                from tests.testkit.isolated_vm_panic_probe import _QmpRecorder
                command.extend(['-uuid', str(UUID(hex=nonce)), '-device', 'pvpanic,events=1',
                                '-action', 'panic=shutdown', '-qmp',
                                f'unix:{root / "qmp.sock"},server=on,wait=off'])
                qmp = _QmpRecorder(root / 'qmp.sock', root / 'qmp.jsonl')
            supervisor = OwnedProcessSupervisor(root / f'owned-{boot}', command,
                                                environment={'QEMU_MODULE_DIR': str(libs / 'qemu')})
            process = supervisor.start()
            entry['pid'] = process.pid
            if qmp is not None:
                qmp.start()
            deadline = min(started + 600, time.monotonic() + 180)
            last_sample = 0.0
            while True:
                assert not interrupted, f'host interrupted: {interrupted}'
                if qmp is not None:
                    qmp.poll()
                if not (panic and boot == 1):
                    assert process.poll() is None, supervisor.log_path.read_text()
                assert time.monotonic() < deadline, f'{mode} exceeded boot/run budget'
                if time.monotonic() - last_sample >= 1 and process.poll() is None:
                    entry['samples'].append(resource_guard(root, process.pid))
                    last_sample = time.monotonic()
                serial = serial_path.read_text(errors='replace') if serial_path.exists() else ''
                assert 'ZF_K571_BOOT_FAILED' not in serial, serial[-16000:]
                completed = [r for r in markers(serial, '') if r.get('zf_k571_guest') == 'finished']
                if completed:
                    [finished] = completed
                    assert finished['returncode'] == 0 and finished['mode'] == mode
                    assert finished['nonce'] == nonce
                    [identity] = markers(serial, 'ZF_K571_BOOT ')
                    [scenario] = markers(serial, 'ZF_K571_SCENARIO ')
                    assert identity['interfaces'] == ['lo'] and identity['nonce'] == nonce
                    assert identity['manifest_sha256'] == report['manifest_sha256']
                    assert scenario['state'] == ('inflight' if boot == 1 else 'recovered')
                    entry.update(identity=identity, scenario=scenario, finished=finished)
                    if panic and boot == 1:
                        if process.poll() is None:
                            continue
                        drain_deadline = time.monotonic() + 2
                        while time.monotonic() < drain_deadline and qmp.poll():
                            pass
                        from tests.testkit.isolated_vm_panic_probe import panic_evidence
                        assert process.returncode == 0
                        assert panic_evidence(serial, ''.join(qmp.lines), nonce), 'missing panic proof'
                        [armed] = markers(serial, 'ZF_K590_PANIC_ARMED ')
                        assert armed['boot_id'] == identity['boot_id']
                        assert armed['workflow_run_id'] == scenario['workflow_run_id']
                        entry['panic'] = armed
                        qmp.stop()
                        entry['qmp_sha256'] = sha256_file(root / 'qmp.jsonl')
                        qmp = None
                    break
                assert process.poll() is None, 'guest exited without scenario/launcher evidence'
                time.sleep(0.1)
            if boot == 1 and panic:
                entry['fault'] = 'guest-sysrq-pvpanic-shutdown'
            elif boot == 1:
                process.kill()
                assert process.wait(timeout=5) == -signal.SIGKILL
                entry['fault'] = 'owned-Popen-SIGKILL'
            else:
                first = report['boots'][0]
                assert first['identity']['boot_id'] != identity['boot_id']
                assert first['scenario']['workflow_run_id'] == scenario['workflow_run_id']
                assert scenario['first_attempt'] == first['scenario']['attempt']
            entry['cleanup'] = supervisor.cleanup(reason='injected_fault' if boot == 1 else 'test_finished')
            assert entry['cleanup']['cleanup_ok']
            entry['serial_sha256'] = sha256_file(serial_path)
            supervisor = None
        assert sha256_file(base) == BASE_SHA
        report['verdict'] = 'pass'
    except BaseException as exc:
        report['error'] = repr(exc)
        raise
    finally:
        try:
            if supervisor and supervisor.process is not None:
                report['final_cleanup'] = supervisor.cleanup(reason='driver_exit')
        finally:
            if qmp is not None:
                qmp.stop()
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            report['elapsed_seconds'] = time.monotonic() - started
            report['cleanup_ok'] = all(not Path(f'/proc/{b["pid"]}').exists()
                                       for b in report['boots'] if 'pid' in b)
            write_json_atomic(root / 'vm-run.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--payload', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_vm(args.root, args.tools, args.payload), ensure_ascii=False))
