"""Read the author Plan bound by the exact reviewed candidate, never by filename."""
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from zf.runtime.plan_content_profile import reviewed_author_profile
from zf.runtime.plan_output_lineage import reviewed_plan_generation
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def _field(event: Any, key: str) -> Any:
    return event.get(key) if isinstance(event, Mapping) else getattr(event, key, None)


def has_reviewed_plan_contract(events: Sequence[Any], source: Any) -> bool:
    """A typed review must not downgrade to an unverified legacy path on failure."""
    current = _field(source, 'payload') or {}
    review_ids = {_field(source, 'causation_id'), current.get('synth_event_id')} - {None, ''}
    for event in events:
        review = _field(event, 'payload') or {}
        if _field(event, 'type') != 'fanout.synth.completed':
            continue
        same_scope = all(current.get(k) and current[k] == review.get(k)
                         for k in ('workflow_run_id', 'fanout_id'))
        if (_field(event, 'id') in review_ids or same_scope) and any(review.get(k) for k in (
                'plan_synth_contract_ref', 'plan_synth_contract_digest', 'reviewed_plan_candidate_digest')):
            return True
    return False


def reviewed_plan_markdown(
    state_dir: Path, events: Sequence[Any], source: Any, *, task_map_ref: str,
) -> tuple[str, str] | None:
    """Repair only a read projection; this grants no approval or new authority."""
    if source is None or not task_map_ref:
        return None
    reviews = [e for e in events if _field(e, 'id') == _field(source, 'causation_id')
               and _field(e, 'type') == 'fanout.synth.completed']
    if len(reviews) != 1:
        return None
    try:
        review, current = _field(reviews[0], 'payload'), _field(source, 'payload')
        if (any(not current.get(k) or current[k] != review.get(k)
                for k in ('workflow_run_id', 'fanout_id'))
                or current.get('plan_revision') != reviewed_plan_generation(SimpleNamespace(payload=review))
                or current.get('task_map_ref') != task_map_ref):
            return None
        contract = hydrate_sidecar_ref(state_dir, {
            'ref':review['plan_synth_contract_ref'], 'sha256':review['plan_synth_contract_digest'],
        }).payload
        sources = contract['source_bindings']
        candidates = []
        for child in contract['child_bindings']:
            matches = [row for row in sources if all(child.get(k) and row.get(k) == child[k]
                       for k in ('source_id', 'artifact_id', 'sha256'))]
            if len(matches) != 1:
                return None
            author = hydrate_sidecar_ref(state_dir, matches[0]).payload
            if author.get('child_id') != child.get('child_id') or author.get('task_map_ref') != task_map_ref:
                return None
            plan_ref = author.get('plan_artifact_ref') or author.get('plan_ref')
            plans = [row for row in sources if plan_ref and row.get('ref') == plan_ref]
            maps = [row for row in sources if row.get('ref') == task_map_ref]
            if len(plans) != 1 or len(maps) != 1:
                return None
            if any(row.get('allowed_paths') not in (None, ['$']) for row in [matches[0], plans[0], maps[0]]):
                return None
            hydrate_sidecar_ref(state_dir, maps[0])
            # Reuse existing contract/Run/stage/revision, author and Plan digest checks.
            if not reviewed_author_profile(state_dir, review, plan_ref=plan_ref,
                                           plan_digest=plans[0]['sha256']):
                return None
            body = hydrate_sidecar_ref(state_dir, {**plans[0], 'content_type':'text/markdown'}).payload
            if not isinstance(body, str) or not body.strip():
                return None
            candidates.append((body, str(plan_ref)))
        return candidates[0] if len(candidates) == 1 else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
