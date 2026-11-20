"""Bound startup evidence for failure-time and current diagnostic source review."""
from pathlib import Path
from typing import Any

from zf.core.events.model import ZfEvent
from zf.runtime.sidecar_refs import hydrate_sidecar_ref


def diagnosis_runtime_sources(
    state_dir: Path, events: list[ZfEvent], source_event_ids: list[str],
) -> dict[str, Any]:
    wanted = set(source_event_ids)
    latest = None
    at_sources = {}
    for event in events:
        if event.type == 'loop.started':
            latest = event
        if event.id in wanted:
            at_sources[event.id] = latest

    def describe(event: ZfEvent | None) -> dict[str, Any]:
        unavailable = {'status': 'unavailable', 'event_id': event.id if event else ''}
        if event is None:
            return unavailable
        ref = event.payload.get('runtime_load_ref')
        if not isinstance(ref, dict) or ref.get('kind') != 'runtime-load':
            return unavailable
        try:
            hydrated = hydrate_sidecar_ref(state_dir, ref)
            body = hydrated.payload
            if (not hydrated.ok or not isinstance(body, dict)
                    or body.get('schema_version') != 'runtime-load.v1'
                    or not isinstance(body.get('state_dir'), str)
                    or Path(body['state_dir']).resolve() != state_dir.resolve()
                    or not isinstance(body.get('source_root'), str)
                    or not Path(body['source_root']).is_absolute()
                    or not body.get('source_revision')):
                return unavailable
        except (OSError, ValueError, TypeError):
            return unavailable
        return {'status': 'observed', 'event_id': event.id, 'at': event.ts,
                'receipt_ref': ref, 'source_root': body['source_root'],
                'source_revision': body['source_revision'], 'pid': body.get('pid')}

    return {
        'latest_startup': describe(latest),
        'at_source_events': {key: describe(at_sources.get(key)) for key in source_event_ids},
        'limits': 'Startup receipts identify loaded harness source, not product target or process liveness. '
                  'Compare the experiment baseline with the failure-time and latest receipts; '
                  'do not infer deployment or repair success from checkout HEAD.',
    }
