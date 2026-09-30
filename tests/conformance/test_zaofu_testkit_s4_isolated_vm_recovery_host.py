"""Explicit offline VM SIGKILL/reboot, never part of ordinary MAINLINE."""

import json
import os
from pathlib import Path
import tempfile

import pytest

from tests.testkit.evidence import sha256_file
from tests.testkit.isolated_vm_run import run_vm


pytestmark = [pytest.mark.host, pytest.mark.serial]


def test_private_guest_panic_capability_only(record_property):
    if os.environ.get('ZF_RUN_VM_PANIC_PROBE') != '1':
        pytest.skip('requires explicit ZF_RUN_VM_PANIC_PROBE=1; not recovery acceptance')
    from tests.testkit.isolated_vm_panic_probe import run_panic_probe
    from uuid import uuid4
    root = Path('/tmp') / f'zf-k590-panic-{uuid4().hex}'
    try:
        report = run_panic_probe(root, Path(os.environ['ZF_VM_TOOL_ROOT']))
        assert report['verdict'] == 'pass' and report['cleanup_ok']
    finally:
        path = root / 'panic-probe.json'
        record_property('panic_probe_path', str(path))
        if path.is_file():
            record_property('panic_probe_sha256', sha256_file(path))


def test_guest_panic_inflight_run_recovers_through_full_cli_start(record_property):
    if os.environ.get('ZF_RUN_VM_PANIC_RECOVERY') != '1':
        pytest.skip('requires explicit ZF_RUN_VM_PANIC_RECOVERY=1 and current payload')
    from uuid import uuid4
    root = Path('/tmp') / f'zf-k571-run-panic-{uuid4().hex[:12]}'
    try:
        report = run_vm(root, Path(os.environ['ZF_VM_TOOL_ROOT']),
                        Path(os.environ['ZF_VM_PAYLOAD_ROOT']), panic=True)
        assert report['fault_model'] == 'guest-kernel-panic'
        assert report['verdict'] == 'pass' and report['cleanup_ok']
    finally:
        path = root / 'vm-run.json'
        record_property('vm_report_path', str(path))
        if path.is_file():
            record_property('vm_report_sha256', sha256_file(path))
            record_property('vm_evidence', json.dumps(json.loads(path.read_text()), sort_keys=True))


def test_owned_vm_inflight_run_recovers_through_full_cli_start(record_property):
    if os.environ.get('ZF_RUN_VM_RECOVERY') != '1':
        pytest.skip('requires ZF_RUN_VM_RECOVERY=1 and prepared private tools/payload')
    parent = Path(tempfile.mkdtemp(prefix='zf-k571-run-20260925-'))
    # The runner requires a new direct /tmp output, not pytest's disposable tree.
    root = parent.with_name(parent.name + '-evidence')
    parent.rmdir()
    try:
        report = run_vm(root, Path(os.environ['ZF_VM_TOOL_ROOT']),
                        Path(os.environ['ZF_VM_PAYLOAD_ROOT']))
        assert report['verdict'] == 'pass' and report['cleanup_ok']
    finally:
        path = root / 'vm-run.json'
        record_property('vm_report_path', str(path))
        if path.is_file():
            record_property('vm_report_sha256', sha256_file(path))
            record_property('vm_evidence', json.dumps(json.loads(path.read_text()), sort_keys=True))
