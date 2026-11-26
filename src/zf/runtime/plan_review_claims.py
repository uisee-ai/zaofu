"""Validate current-source citations, not the semantics of Plan criticism."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from zf.runtime.sidecar_refs import hydrate_sidecar_ref

POLICY = 'current-evidence-v1'


def review_claim_briefing(policy: str) -> list[str]:
    if policy != POLICY:
        return []
    return [
        '## Current review evidence', '',
        'Each fix_item, explicitly blocking finding, and blocking owner question must carry '
        '`basis: {obligation, requirement_evidence: [...], current_evidence: [...]}`. '
        'Name the existing requirement or approved decision, observed gap, minimum repair and done_when. '
        'Each citation uses current input-index `source_id`, `artifact_id`, `sha256`, `json_path`, '
        'and either exact `observed_value` (JSON) or `quote` (text, optional character start/end). '
        'current_evidence must cite this candidate’s child inputs; historical sources may support '
        'comparison but cannot establish the current value. Before rejecting, reread disputed current '
        'fields, especially after historical reads or compaction. For an alleged contradiction, cite '
        'the current statement that imposes the disputed constraint and the current statement or graph '
        'it conflicts with. Close old findings whose condition the current candidate already corrected. '
        'Unsupported improvement suggestions '
        'remain nonblocking; missing necessary evidence remains a gap. Mechanical checks validate '
        'citations, not your interpretation. An accurate citation alone never proves a Plan passed.',
        'For full approval, when the current required plan-execution-review contains execution_comparisons, '
        'write one report.execution_assessments entry per key: comparison, conclusion, exact task_ids if listed, '
        'and the same basis with requirement_evidence from a frozen requirement/declared input and current_evidence '
        'from the current candidate. Compare actual stage entry and standalone Candidate commands to the obligation. '
        'A host allowlist grants permission only. Reconcile every still-present comparison again after rework. '
        'Before any rejection, assess every frozen comparison marked before_rework; on approval assess all. '
        'For each marked comparison include disposition (no_repair, repair_required, or needs_clarification), runtime_evidence with '
        'an exact current field beneath $.execution_comparisons.<comparison>.facts, and the requirement/candidate '
        'basis. Unmarked comparisons use conclusion and basis on approval; their optional fields impose no '
        'repair obligation. The runtime derives the comparison from the Task Map and policy. Give the author '
        'the disputed Task Map field, its observed value and the requirement it must meet. Revalidate the '
        'candidate to recompute the facts. Link fixes by fix_items[].comparison (other for a separate obligation) and unresolved questions by owner_decision_items[].comparison. A command owner selects acceptance and commands '
        'within a task; verify_only uses the Task Pipeline Verify entry. Evaluate cross-task ordering separately '
        'against the requirement and direct dependencies. Unresolved execution contracts remain unknown. '
        'Positive assessments are verdict metadata; leave clean findings and Planner-owned gates empty.', '',
    ]


def review_claim_issues(
    state_dir: Path, request: Mapping[str, Any], result: Mapping[str, Any],
) -> list[dict[str, str]]:
    identity = request.get('result_identity') or {}
    if identity.get('plan_review_evidence_policy') != POLICY:
        return []  # Previously frozen requests retain their original contract.
    report = result.get('report') if isinstance(result.get('report'), Mapping) else {}
    def value(key: str) -> Any:
        return report.get(key) or result.get(key)
    if result.get('execution_status') == 'failed' or (
            not result.get('execution_status') and result.get('status') in {'failed', 'failure'}):
        return []  # An execution failure is not a product/Plan rejection.
    issues: list[dict[str, str]] = []
    def error(field: str, message: str) -> None:
        issues.append({'field': field, 'code': 'plan_review_evidence_invalid', 'message': message})
    def items(key: str) -> list[tuple[str, Any]]:
        rows = value(key) or []
        path = f'report.{key}' if report.get(key) else key
        if not isinstance(rows, list):
            error(path, 'Blocking evidence items must be an array')
            return []
        return [(f'{path}[{index}]', row) for index, row in enumerate(rows)]
    rows = [*items('fix_items'),
            *((path, row) for path, row in items('findings') if isinstance(row, Mapping) and row.get('blocking') is True),
            *((path, row) for path, row in items('owner_decision_items') if isinstance(row, Mapping) and row.get('blocking') is not False)]
    rejected = value('verdict') == 'rejected' or value('recommendation') in {'reject', 'needs_rework'}
    if rejected and not rows:
        error('fix_items', 'A rejection requires a blocking item with current evidence and obligation basis')
    if rejected or value("recommendation") == "approve" or value("verdict") in {"passed", "approved"}:
        from zf.runtime.plan_review_comparisons import execution_assessments
        try:
            if scope := identity.get("diagnosis_review"):
                from zf.runtime.candidate_diagnosis_review import diagnosis_assessments
                if not rejected:
                    if rows:
                        error("report", "Diagnosis approval cannot retain blocking issues")
                    rows.extend(diagnosis_assessments(scope, report or result, error=error))
            else:
                rows.extend(execution_assessments(state_dir, request, report or result, rejected=rejected, error=error))
        except (KeyError, ValueError, TypeError, OSError) as exc:
            error("report.execution_assessments", str(exc))
    if not rows:
        return issues
    try:
        from zf.runtime.artifact_read_ledger import _safe_component
        attempt = _safe_component(str(request.get('active_attempt_id') or ''))
        digest = _safe_component(str(request.get('source_manifest_digest') or ''))
        manifest = hydrate_sidecar_ref(state_dir, {
            'ref': f'artifacts/attempts/{attempt}/source-manifests/{digest}.json', 'sha256': digest,
        }).payload
        contract = hydrate_sidecar_ref(state_dir, {
            'ref': identity['plan_synth_contract_ref'], 'sha256': identity['plan_synth_contract_digest'],
        }).payload
        if manifest.get('attempt_id') != attempt:
            raise ValueError('review manifest belongs to a different attempt')
        children = {str(row['child_id']) for row in contract['child_bindings']}
        current = {row['source_id'] for row in contract['source_bindings']
                   if any(row['source_id'] == f'child-result-{child}'
                          or row['source_id'].startswith((f'child-{child}-', f'child-artifact-{child}-'))
                          for child in children)}
        from zf.runtime.previous_preflight_delivery import matches_previous_preflight_binding

        # A source_id groups related inputs. PRD text and RequirementSpec can both
        # be "requirement"; every bound occurrence must resolve exactly once.
        sources = manifest['sources']
        remaining = {row['source_id']: [] for row in contract['source_bindings']}
        for source in sources:
            if source['source_id'] in remaining:
                remaining[source['source_id']].append(source)
        for binding in contract['source_bindings']:
            group = remaining[binding['source_id']]
            matches = [s for s in group if
                       all(binding.get(k) and s.get(k) == binding[k] for k in ('ref', 'sha256', 'artifact_id'))
                       or matches_previous_preflight_binding(state_dir, sources, binding, s)]
            if len(matches) != 1:
                raise ValueError('Review contract and source manifest bindings differ')
            group.remove(matches[0])
        if any(remaining.values()):
            raise ValueError('Review contract and source manifest bindings differ')
        for path, row in rows:
            field = f'{path}.basis'
            basis = row.get('basis') if isinstance(row, Mapping) else None
            if not isinstance(basis, Mapping) or not str(basis.get('obligation') or '').strip():
                error(field, 'Name the existing requirement/approved obligation; advice cannot create one')
                continue
            if 'runtime_evidence' in row:
                try:
                    _check_citation(state_dir, manifest, row['runtime_evidence'], current=None,
                                    role=str(request.get('role_instance') or ''))
                except (ValueError, OSError, KeyError, TypeError) as exc:
                    error(path + '.runtime_evidence', str(exc))
            for name in ('requirement_evidence', 'current_evidence'):
                citations = basis.get(name)
                if not isinstance(citations, list) or not citations:
                    error(f'{field}.{name}', 'Provide exact current manifest source, digest, selector and observed value/quote')
                    continue
                for index, citation in enumerate(citations):
                    try:
                        _check_citation(state_dir, manifest, citation,
                                        current=current if name == 'current_evidence' else None,
                                        role=str(request.get('role_instance') or ''))
                    except (ValueError, OSError, KeyError, TypeError) as exc:
                        error(f'{field}.{name}[{index}]', str(exc))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        error('review_context', str(exc))
    return issues


def _check_citation(
    state_dir: Path, manifest: Mapping[str, Any], citation: Any, *, current: set[str] | None, role: str,
) -> None:
    from zf.runtime.artifact_read_snapshot import open_selection
    if not isinstance(citation, Mapping) or not all(citation.get(key) for key in
            ('source_id', 'artifact_id', 'sha256', 'json_path')):
        raise ValueError('Citation requires source_id, artifact_id, sha256 and json_path')
    if current is not None and citation['source_id'] not in current:
        raise ValueError('Historical/context input cannot prove the current candidate state')
    selection = open_selection(state_dir, manifest=manifest, row=citation,
                               identity={'actor': role, 'role': role})
    if 'observed_value' in citation:
        # Exact structured values only; no inference from arbitrary prose.
        from zf.runtime.artifact_source_versions import read_source_version
        from zf.runtime.artifact_read_ledger import _select_json_path
        from zf.runtime.sidecar_refs import sidecar_path
        raw, _ = read_source_version(state_dir, selection.source,
                                    path=sidecar_path(state_dir, selection.source['ref']))
        actual = _select_json_path(json.loads(raw), citation['json_path'])
        if type(actual) is not type(citation['observed_value']) or actual != citation['observed_value']:
            raise ValueError('observed_value differs from the bound current selection')
    else:
        quote = citation.get('quote')
        if not isinstance(quote, str) or not quote.strip():
            raise ValueError('Cite an exact quote or an observed_value; a path alone is not evidence')
        # Selectors and optional offsets keep the check bounded to the cited evidence.
        start = citation.get('start', 0)
        end = citation.get('end', selection.chars)
        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError('Citation offsets must be integer character positions')
        if quote not in selection.read(start, end):
            raise ValueError('Quoted text is absent from the bound selection')
