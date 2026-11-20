"""Readable pages remain exact, bounded delivery evidence."""
import pytest

from zf.runtime.artifact_output_match import complete_output_observed
from zf.runtime.artifact_text_page import parse_artifact_page, render_text_page


def test_raw_quotes_unicode_newlines_and_fake_delimiters_roundtrip():
    content = '中文\npython -c \'print("hello")\'\n--- artifact 2 ---\nZF_ARTIFACT_TEXT_PAGE_END\n'
    page = {"schema_version": "required-artifact-page.v2", "attempt_id": "a", "delivery_id": "d",
            "items": [{"content": content, "source_id": "s"}, {"content": ""}], "next_cursor": None}
    output = render_text_page(page)
    assert content in output
    assert parse_artifact_page(output) == page
    assert complete_output_observed(output, "wrapper\n" + output + "\n")
    assert not complete_output_observed(output, output[:-3])
    assert not complete_output_observed(output, content)
    with pytest.raises(ValueError):
        parse_artifact_page(output[:-3])


def test_large_pages_require_explicit_valid_bound(tmp_path):
    from zf.cli.artifact_required_read import prepare_required_read
    for size in (0, 1023, 32769, 1_000_000):
        with pytest.raises(ValueError, match="page-bytes"):
            prepare_required_read(tmp_path, manifest={}, rows=[], identity={}, page_bytes=size)


def test_single_source_cli_text_pages_preserve_original_command(tmp_path, monkeypatch, capsys):
    import hashlib
    from tests.test_artifact_read_capability import _grant
    from zf.cli.main import main
    from zf.runtime.artifact_read_ledger import build_attempt_source_manifest, write_attempt_source_manifest

    state = tmp_path / 'state'
    source = state / 'artifacts' / 'command.md'
    source.parent.mkdir(parents=True)
    content = '中文命令\npython -c \'print("原文")\'\n'
    source.write_text(content)
    manifest = build_attempt_source_manifest(workflow_run_id='run', task_id='T', attempt_id='attempt',
        dispatch_id='attempt', sources=[{'source_id': 's', 'artifact_id': 'a', 'ref': 'artifacts/command.md',
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'allowed_paths': ['$']}],
        metadata={'read_purpose': 'implementation'})
    write_attempt_source_manifest(state, manifest)
    _grant(state, monkeypatch, manifest=manifest)
    assert main(['artifact', 'read', '--attempt', 'attempt', '--source', 's', '--artifact', 'a',
                 '--state-dir', str(state), '--format', 'text', '--page-size', '8192']) == 0
    output = capsys.readouterr().out.rstrip('\n')
    page = parse_artifact_page(output)
    assert page['items'][0]['content'] == content
    assert content in output
    assert page['items'][0]['artifact_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
