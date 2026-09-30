from copy import deepcopy
import json
from pathlib import Path
import re

import pytest

from zf.core.self_issue.markdown import render_publication_markdown


def test_public_issue_keeps_evidence_and_version_context_summary() -> None:
    body = render_publication_markdown({
        "bug_description": "Import fails",
        "environment": {},
        "analysis": {
            "evidence_summary": [{"ref": "e1", "summary": "clean-install log"}],
            "verification_summary": [{"name": "source", "status": "not_run", "next_action_scope": "investigation"}],
            "investigation_gaps": ["Locate pyproject.toml"],
            "downstream_verification_tasks": ["Rebuild fixed wheel"],
            "version_context": {"source_match_status": "mismatch_or_unverified"},
        },
    }, marker="test")
    assert "## Evidence summary" in body
    assert "## Verification summary" in body
    assert "## Investigation gaps" in body
    assert "## Downstream verification tasks" in body
    assert "## Version context" in body


def test_legacy_json_steps_and_empty_future_fields_render_without_placeholders():
    body = render_publication_markdown({
        'reproduction_steps': '["Install wheel", "Run help"]',
        'analysis': {'version_context': {'reported_source_revision': 'abc123', 'pr_head': '', 'merge_sha': None}},
        'published_attachments': [{'filename': 'screen.png', 'url': 'https://example.org/screen.png', 'content_type': 'image/png'}],
    }, marker='test')
    assert '1. Install wheel\n2. Run help' in body
    assert 'Pr head' not in body and 'Merge sha' not in body and 'Not provided.' not in body
    assert '[screen.png](<https://example.org/screen.png>)' in body and '![' not in body


def test_binary_attachment_notice_omits_retry_instruction():
    body = render_publication_markdown({'binary_attachments_omitted': True}, marker='test')
    assert 'separate Evidence comment.' in body
    assert 'If that step remains pending' not in body
    assert 'Published & View' not in body


def test_analysis_peers_semantic_order_and_expanded_evidence_are_lossless():
    evidence = [{'id': 'E1', 'result': 'wheel declares only PyYAML', 'command': 'inspect wheel',
                 'locations': ['pyproject.toml:11'], 'artifact': 'build.whl', 'exit_code': 0},
                {'id': 'E2', 'summary': 'CLI import fails', 'execution_scope': 'not a fresh install'}]
    body = render_publication_markdown({'analysis': {
        'claims': [
            {'id': 'C1', 'statement': 'Missing dependency', 'confidence': 'high',
             'status': 'reproduced', 'evidence_refs': ['E1', 'E2']},
            {'id': 'C2', 'claim': 'Help affected', 'status': 'observed',
             'support': [{'evidence_ref': 'E2', 'conclusion': 'parser import blocks help'}]},
        ],
        'conflicts': [{'subject': 'Source identity', 'resolution': 'Do not bind build commit',
                       'evidence_refs': ['E1'], 'details': 'different checkout'}],
        'hypotheses': [{'statement': 'dev extra masks defect', 'limitation': 'tests not executed',
                        'basis': 'dependency declaration', 'evidence_refs': ['E2']}],
        'evidence_summary': evidence,
        'verification_summary': [{'name': 'provenance', 'status': 'passed_with_limitations',
                                  'next_action_scope': 'investigation', 'evidence_refs': ['E1']}],
        'executed_reproduction': {'evidence_refs': ['E2'], 'outcome': 'exit 1'},
    }}, marker='test')
    analysis = body.split('## Analysis')[1].split('## Evidence summary')[0]
    assert '### 一、Claims' in analysis
    assert '\n1. **Claim:** Missing dependency' in analysis
    assert '\n2. **Claim:** Help affected' in analysis
    assert '(1) **Confidence:** high' in analysis and '(2) **Status:** reproduced' in analysis
    assert analysis.count('**Confidence:**') == 2
    assert '**Confidence:** 未单独评估（报告未给出）' in analysis
    assert '(3) **Support:**' in analysis
    assert '\n   a. build.whl：wheel declares only PyYAML' in analysis
    assert 'C1' not in analysis and 'C2' not in analysis
    assert analysis.index('**Conflict:** Source identity') < analysis.index('**Resolution:** Do not bind build commit')
    for detail in ('CLI import fails', 'parser import blocks help', 'tests not executed',
                   'dependency declaration', 'inspect wheel', 'pyproject.toml:11', 'build.whl',
                   'Exit code:** 0', 'not a fresh install', 'Next action scope:** investigation'):
        assert detail in body
    assert '\n  -\n' not in body and '**Evidence refs:**\n      - E' not in body


def test_publication_shape_is_stable_for_rich_environment_and_legacy_aliases():
    body = render_publication_markdown({
        "bug_description": "Missing dependency",
        "actual_behavior": "CLI help fails before parsing.",
        "expected_behavior": "CLI help starts.",
        "environment": {
            "investigator_os": "Ubuntu 24.04.2 LTS",
            "investigator_kernel": "Linux 7.0.0-30-generic",
            "investigator_architecture": "x86_64",
            "investigator_python": "3.11.15",
        },
        "project_version": "0.0.7",
        "source_code_revision": "source-sha",
        "analysis": {
            "classification_status": "provisional",
            "severity_status": "provisional",
            "severity_reason": "The public CLI is blocked.",
            "external_issue_disposition": "needs_triage",
            "version_context": {"issue_project_root": "fd643cad"},
            "claims": [{"claim": "The base wheel omits httpx", "confidence": "high",
                        "status": "observed", "support": [{"evidence_ref": "E1",
                        "conclusion": "METADATA lists only PyYAML."}]}],
            "conflicts": [{"conflict": "Top-level import scope", "support": [{
                "evidence_ref": "E1", "conclusion": "import zf succeeds."}],
                "resolution": "Limit the claim to CLI startup."}],
            "hypotheses": [{"claim": "The dev extra masks the missing dependency",
                            "confidence": "high", "status": "supported", "support": [{
                                "evidence_ref": "E1", "conclusion": "Dev tests have httpx."}]}],
            "evidence_summary": [{"id": "E1", "locations": ["pyproject.toml:11", "catalog.py:14"],
                                   "summary": "Dependency declaration and import location."}],
        },
        "classification": "bug", "severity": "P1", "reproduction_status": "reproduced",
        "component": "Python packaging", "impact_scope": "Base-wheel CLI users",
        "assessment_confidence": "high", "evidence_collection_mode": "bounded",
        "evidence_collection_status": "completed", "assessment_status": "completed",
    }, marker="stable")
    assert "- **Operating system:** Ubuntu 24.04.2 LTS" in body
    assert "- **OS version:** 7.0.0-30-generic" in body
    assert "- **Architecture:** x86_64" in body
    assert "- **Python version:** 3.11.15" in body
    assert "- **Project version:** 0.0.7" in body
    assert "- **Project source commit:** source-sha" in body
    triage = body.split("## Triage", 1)[1].split("## Analysis", 1)[0]
    assert triage.index("**Severity:**") < triage.index("**Severity status:**")
    assert triage.index("**Component:**") < triage.index("**Impact scope:**")
    assert triage.index("**Assessment confidence:**") < triage.index("**Assessment:**")
    analysis = body.split("## Analysis", 1)[1].split("## Evidence summary", 1)[0]
    assert "### 四、Other analysis" in analysis
    hypothesis = analysis.split("### 三、Hypotheses", 1)[1].split("### 四、", 1)[0]
    assert hypothesis.index("**Hypothesis:**") < hypothesis.index("**Confidence:** high") < hypothesis.index("**Status:** supported") < hypothesis.index("**Support:**")
    assert "\n   a. pyproject.toml:11; catalog.py:14：METADATA lists only PyYAML." in analysis
    assert not re.search(r"\bE\d+\b", body)
    assert "a. a." not in body
    evidence = body.split("## Evidence summary", 1)[1]
    assert "**Locations:**" in evidence
    assert "\n   a. pyproject.toml:11" in evidence


def test_locations_keep_concrete_paths_and_nested_records_use_roman_markers():
    body = render_publication_markdown({
        "analysis": {
            "attachment_warnings": [
                {"reason": "unsupported_file_type", "ref": "artifact.whl"},
                {"reason": "outside_evidence_roots", "ref": "tests/test.py:49"},
            ],
            "evidence_summary": [{
                "id": "E1", "summary": "source check",
                "locations": ["/home/uisee/project/pyproject.toml:11", "/home/uisee/project/src/zf/cli/main.py:25"],
            }],
            "verification_summary": [{"name": "source", "evidence_refs": ["E1"]}],
        },
    }, marker="paths")
    assert "/home/uisee/project/pyproject.toml:11" in body
    assert "[REDACTED_PERSONAL_PATH]" not in body
    assert "a.  \n      i. **Reason:** unsupported_file_type" in body
    assert "ii. **Ref:** artifact.whl" in body
    assert "(1) **Evidence refs:**  \n   a. /home/uisee/project/pyproject.toml:11" in body


def test_legacy_gap_objects_render_without_python_repr_or_lost_limits():
    body = render_publication_markdown({'analysis': {'investigation_gaps': [
        "{'field': 'build_origin', 'kind': 'provenance_limitation', 'blocking': False, 'description': 'No build SHA'}",
    ]}}, marker='test')
    assert 'Description:** No build SHA' in body
    assert 'Blocking:** No' in body and 'provenance_limitation' in body
    assert "{'field':" not in body


def test_issue_59_golden_body_changes_only_the_seven_requested_items():
    fixture = json.loads((Path(__file__).parent / 'fixtures/self_issue/issue_59_template.json').read_text())
    data = deepcopy(fixture['disclosed'])
    # Agent-collected facts, not facts fabricated by the rendering host.
    data.update(environment={'os': 'Linux', 'version': '7.0.0-30-generic',
                             'arch': 'x86_64', 'python': '3.11.15'},
                project_version='0.0.7', source_code_revision='inspected-source-sha',
                impact_scope='仅安装基础 wheel 且未安装 httpx 的 CLI 用户。')
    expected = fixture['body']
    expected = expected.replace('## Triage',
        '## Environment\n\n- **Operating system:** Linux\n- **OS version:** 7.0.0-30-generic\n'
        '- **Architecture:** x86_64\n- **Python version:** 3.11.15\n'
        '- **Project version:** 0.0.7\n- **Project source commit:** inspected-source-sha\n\n## Triage')
    expected = expected.replace('- **Severity:** P1',
        '- **Severity:** P1\n- **Severity status:** provisional\n'
        '- **Severity reason:** 基础安装使整个 CLI 入口不可用，而不是仅影响可选价格功能。')
    expected = expected.replace('- **Component:** Python 打包与 CLI 导入链',
        '- **Component:** Python 打包与 CLI 导入链\n'
        '- **Impact scope:** 仅安装基础 wheel 且未安装 httpx 的 CLI 用户。')
    expected = expected.replace('- **Assessment confidence:** high\n', '')
    expected = expected.replace('- **Assessment:** completed',
        '- **Assessment confidence:** high\n- **Assessment:** completed')
    expected = expected.replace('### 四、其他分析', '### 四、Other analysis')
    expected = expected.replace('\n\n2. **Severity status:** provisional\n\n'
        '3. **Severity reason:** 基础安装使整个 CLI 入口不可用，而不是仅影响可选价格功能。', '')
    # Only hypotheses reorder Status and Support; no other field moves.
    before, hypotheses = expected.split('### 三、Hypotheses', 1)
    hypotheses, after = hypotheses.split('### 四、', 1)
    hypotheses = re.sub(r'\(2\) \*\*Support:\*\* (.*?)\n\n   \(3\) \*\*Status:\*\* supported',
        r'(2) **Status:** supported\n\n   (3) **Support:** \1', hypotheses, flags=re.S)
    expected = before + '### 三、Hypotheses' + hypotheses + '### 四、' + after
    # Only Support/Locations acquire hard breaks, preserving original list syntax.
    expected = re.sub(r'(\*\*(?:Support|Locations):\*\*) (a\.[^\n]*(?:\n   \n   [a-z]\.[^\n]*)*)',
        lambda m: m[1] + '  \n   ' + m[2].replace('\n   \n   ', '  \n   '), expected)
    assert render_publication_markdown(data, marker=fixture['marker']) == expected
    browser_fixture = Path(__file__).parents[1] / 'web/tests/fixtures/portable-issue-workspace/issue-59-template.md'
    assert browser_fixture.read_text().rstrip('\n') == expected


@pytest.mark.parametrize('alias', ['hypothesis', 'statement', 'claim', 'description'])
def test_hypothesis_aliases_and_json_key_order_do_not_change_template(alias):
    record = {'support': [{'evidence_ref': 'E1', 'conclusion': 'Dev tests include httpx.'}],
              'status': 'supported', alias: 'Dev extra masks the defect', 'confidence': 'high'}
    data = {'analysis': {'hypotheses': [record], 'evidence_summary': [
        {'id': 'E1', 'ref': 'tests.json', 'conclusion': 'Tests passed in dev.'}]}}
    body = render_publication_markdown(data, marker='same')
    record['hypothesis'] = record.pop(alias)
    data['analysis']['hypotheses'] = [dict(reversed(list(record.items())))]
    assert body == render_publication_markdown(data, marker='same')
    hypothesis = body.split('### 三、Hypotheses\n\n')[1].split('## Evidence summary')[0]
    assert hypothesis == ('1. **Hypothesis:** Dev extra masks the defect\n\n'
        '   (1) **Confidence:** high\n\n   (2) **Status:** supported\n\n'
        '   (3) **Support:**  \n   a. tests.json：Dev tests include httpx.\n\n')


def test_evidence_ids_stay_internal_across_all_reference_sections():
    evidence = [{'id': 'E1', 'ref': 'build.log', 'conclusion': 'Base dependency missing.'}]
    data = {'analysis': {
        'evidence_summary': evidence,
        'claims': [{'claim': 'Dependency missing', 'confidence': 'high', 'status': 'observed', 'evidence_refs': ['E1']}],
        'conflicts': [{'conflict': 'Import scope', 'evidence_refs': ['E1'], 'resolution': 'CLI only'}],
        'hypotheses': [{'hypothesis': 'Dev masks failure', 'confidence': 'high', 'status': 'supported', 'evidence_refs': ['E1']}],
        'verification_summary': [{'name': 'Dependency', 'evidence_refs': ['E1']}],
        'investigation_gaps': [{'description': 'Build origin missing', 'evidence_refs': ['E1']}],
        'version_context': {'reproduction_artifact_source': {'evidence_refs': ['E1']}},
        'executed_reproduction': {'evidence_refs': ['E1'], 'outcome': 'exit 1'},
    }}
    original = deepcopy(data)
    body = render_publication_markdown(data, marker='test')
    assert not re.search(r'\bE\d+\b', body)
    assert body.count('build.log：Base dependency missing.') == 7
    assert data == original  # No mutation of canonical evidence identity.
