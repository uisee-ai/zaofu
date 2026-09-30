"""Attachment reuse across short-lived API services, with digest validation."""
from datetime import timedelta
from dataclasses import replace
from io import BytesIO

import pytest

from tests.test_external_issue_ingress import _service, _mirror, _config
from zf.runtime.external_issue_ingress import ExternalIssueIngressService


@pytest.mark.parametrize('unavailable', [False, True])
def test_capture_survives_new_service_and_retries_after_expiry(tmp_path, monkeypatch, unavailable):
    service, reconciler = _service(tmp_path, _mirror(created_at='2026-09-01T00:00:00+00:00'))
    mirror = service.mirrors.get(8)
    mirror, _ = service.mirrors.upsert(replace(mirror, body_digest=''),
        '![image](https://github.com/user-attachments/assets/example.png)')
    calls = []
    def download(*args, **kwargs):
        calls.append(1)
        if unavailable:
            raise OSError('temporary failure')
        response = BytesIO(b'PNG fixture')
        response.headers = {'Content-Type':'image/png'}
        return response
    monkeypatch.setattr('urllib.request.urlopen', download)
    first = service._issue_attachment_refs(mirror)
    another = ExternalIssueIngressService(service.state_dir, _config(), project_root=tmp_path,
        reconciler=reconciler, now=service.now)
    assert another._issue_attachment_refs(mirror) == first
    assert len(calls) == 1
    another.now = lambda:service.now() + timedelta(seconds=300 if unavailable else 86400)
    another._issue_attachment_refs(mirror)
    assert len(calls) == 2
    if not unavailable:
        (service.state_dir / first[0]['ref']).write_bytes(b'corrupt')
        assert another._issue_attachment_refs(mirror) == []
        assert len(calls) == 3
        # Immutable evidence must never be trusted or silently overwritten.
        assert (service.state_dir / first[0]['ref']).read_bytes() == b'corrupt'


def test_poll_does_not_download_attachments_for_ineligible_historical_issue(tmp_path, monkeypatch):
    service, _ = _service(tmp_path, _mirror(created_at='2026-08-01T00:00:00+00:00'))
    def unexpected(*args):
        raise AssertionError('Historical issue should not capture attachments')
    monkeypatch.setattr(service, '_issue_attachment_refs', unexpected)
    assert service.poll_once().ok
