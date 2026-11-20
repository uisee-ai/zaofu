"""Deterministic source producer for Controller's isolated real-Keycloak browser check.

Run only inside the checker-created /tmp/zf-controller-auth-*/project directory.
Does not call any model provider or use developer credentials.
"""

from pathlib import Path

from zf.core.config.project_context import resolve_project_context
from zf.core.events import EventWriter
from zf.core.events.factory import event_log_from_project
from zf.runtime.agent_session_stream import AgentSessionIdentity, AgentSessionStreamEmitter


def main():
    root = Path.cwd().resolve()
    if root.name != 'project' or not root.parent.name.startswith('zf-controller-auth-') or root.parent.parent != Path('/tmp'):
        raise SystemExit('Requires the isolated Controller qualification directory')
    context = resolve_project_context(require_config=True)
    log = event_log_from_project(context.state_dir, config=context.config)
    writer = EventWriter(log)
    try:
        for number in range(2):
            stream = AgentSessionStreamEmitter(writer=writer, identity=AgentSessionIdentity(
                run_id=f'qualification-turn-{number}', thread_id='qualification-session', source='channel',
                actor='fixture-helper', task_id=f'qualification-task-{number}', backend='codex', provider='openai'))
            stream.start()
            stream.complete(usage={'input_tokens': 100, 'output_tokens': 20, 'cached_input_tokens': 30})
        writer.emit('simulation.done', actor='qualification', payload={'purpose': 'controller-observation'})
    finally:
        log.close()


if __name__ == '__main__':
    main()
