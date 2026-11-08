"""Prove a reader's exact plan admission incident has recovered."""
from zf.core.events.model import ZfEvent
from zf.runtime.plan_artifact_package import (
    admitted_plan_artifact_package_for_generation,
    reduce_plan_artifact_packages,
)


def recovered_plan_admission(
    events: list[ZfEvent], *, source: ZfEvent, trigger: ZfEvent, workflow_run_id: str,
) -> ZfEvent | None:
    incident = str(trigger.payload.get('plan_admission_incident_id') or '')
    digest = str(source.payload.get('task_map_digest') or '')
    ref = str(source.payload.get('task_map_ref') or '')
    if (not incident or not ref or not digest or source.type != 'task_map.ready'
            or source.origin != 'kernel'):
        return None
    trusted = [event for event in events if event.origin == 'kernel']
    projection = None
    for admitted in trusted:
        p = admitted.payload
        if (admitted.type != 'task_map.admitted'
                or p.get('schema_version') != 'task-map.admission.v1'
                or p.get('plan_admission_incident_id') != incident
                or p.get('trigger_event_id') != source.id
                or p.get('task_map_ref') != ref or p.get('task_map_digest') != digest):
            continue
        for generation in trusted:
            g = generation.payload
            if (generation.type != 'task.pipeline.generation.admitted'
                    or g.get('schema_version') != 'task-pipeline-generation.v1'
                    or g.get('workflow_run_id') != workflow_run_id
                    or g.get('task_map_admitted_event_id') != admitted.id
                    or g.get('trigger_event_id') != source.id
                    or not all(g.get(key) for key in ('task_map_generation',
                              'plan_artifact_package_ref', 'plan_artifact_package_digest'))):
                continue
            # Writer/WRC admission can precede a later package rejection. Only
            # the package reducer can prove a subsequent valid readmission.
            if projection is None:
                projection = reduce_plan_artifact_packages(trusted, workflow_run_id=workflow_run_id)
            package = admitted_plan_artifact_package_for_generation(
                projection, task_map_generation=str(g.get('task_map_generation') or ''))
            if (package and package.get('package_ref') == g.get('plan_artifact_package_ref')
                    and package.get('package_digest') == g.get('plan_artifact_package_digest')):
                return admitted
    return None
