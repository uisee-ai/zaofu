"""Parent shutdown disposition; retained evidence is never garbage-collected here."""
from zf.runtime.autoresearch_resident_sidecar import stop_autoresearch_resident_sidecar


def parent_completed(events) -> bool:
    relevant = [e for e in events if e.type in {
        'run.goal.completed', 'run.goal.started', 'workflow.invoke',
        'loop.shutdown_requested', 'runtime.stop.requested', 'run.cancelled', 'dispatch.paused',
    }]
    return bool(relevant and relevant[-1].type == 'run.goal.completed')


def finish_resident_on_parent_exit(sidecar, *, config, event_log):
    policy = getattr(config.runtime.autoresearch_resident, 'on_parent_terminal', 'preserve')
    if policy == 'continue' and parent_completed(event_log.read_all()):
        # The child inherited the launcher lock. Closing (not LOCK_UN) leaves
        # it held by that child, so another watcher cannot duplicate the owner.
        sidecar.log_handle.close()
        if sidecar.lock_handle is not None:
            sidecar.lock_handle.close()
        return
    stop_autoresearch_resident_sidecar(sidecar, event_log=event_log)
