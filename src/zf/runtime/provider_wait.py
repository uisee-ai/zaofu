"""Replay explicit Provider unavailability without probing or inferring accounts."""
from zf.core.events import ZfEvent


def provider_external_wait(events: list[ZfEvent], *, backend: str, instance_id: str) -> dict:
    """Only bound kernel facts can establish or release this dispatch fence.

    Quota has no guessed expiry. A healthy fact clears only its exact recorded
    account/model scope; availability never grants task execution permission.
    """
    waits: dict[tuple[str, str], dict] = {}
    for event in events:
        body = event.payload
        if (event.origin != 'kernel' or event.type not in {
                'provider.health.changed', 'provider.stop.recovery', 'provider.account.exhausted'}
                or not backend or not instance_id
                or body.get('backend') != backend or body.get('instance_id') != instance_id):
            continue
        scope = (str(body.get('account_alias') or body.get('provider_profile') or body.get('profile') or ''),
                 str(body.get('model') or ''))
        reason = body.get('reason') or body.get('provider_stop_reason')
        if reason in {'quota_exhausted', 'auth_error', 'tool_permission_blocked'} or (
                event.type == 'provider.account.exhausted'):
            waits[scope] = {'reason': reason or 'quota_exhausted', 'source_event_id': event.id,
                'backend': backend, 'instance_id': instance_id,
                'resume_condition': 'bound_kernel_availability_and_existing_action_admission',
                'authorizes_execution': False}
        elif event.type == 'provider.health.changed' and (
                body.get('status') == 'healthy'
                or not body.get('status') and reason in {'healthy', 'recovered'}):
            waits.pop(scope, None)
    return next(iter(waits.values()), {})
