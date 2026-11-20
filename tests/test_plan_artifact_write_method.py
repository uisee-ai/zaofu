"""Opaque shell data stays conservative; explicit artifact targets remain usable."""
from zf.cli.hook_workdir_guard import evaluate_workdir_write_guard
from zf.core.events import EventLog, EventWriter


def test_source_paths_in_artifact_data_do_not_require_widening_write_scope(tmp_path):
    state = tmp_path / '.state'
    workdir = state / 'workdirs' / 'planner' / 'project'
    workdir.mkdir(parents=True)
    log = EventLog(state / 'events.jsonl')
    writer = EventWriter(log)
    source = str(tmp_path / 'docs/source.md')
    def check(tool, inputs):
        return evaluate_workdir_write_guard(
            state_dir=state, project_root=tmp_path,
            event_type='codex.hook.pre_tool_use', actor='planner',
            event_payload={'cwd': str(workdir), 'tool_name': tool, 'tool_input': inputs},
            event_log=log, event_writer=writer, causation_id=None,
            should_check_actor=lambda _: True, active_task_id_for_actor=lambda *_: '',
        )
    # Reproduces the real rejection: project paths are data in a Python program,
    # but arbitrary code cannot safely be classified as an explicit-target write.
    shell = f"python - <<'PY'\nfrom pathlib import Path\nPath('plan.md').write_text({source!r})\nPY"
    assert check('Bash', {'command': shell}) == 2
    # The revised author method uses the supported target-aware tool instead.
    assert check('Write', {'file_path': 'plan.md', 'content': source}) == 0
    assert check('apply_patch', {'command': f'*** Begin Patch\n*** Add File: plan.md\n+{source}\n*** End Patch'}) == 0
    assert check('Write', {'file_path': str(tmp_path / 'outside.md'), 'content': source}) == 2
    (workdir / 'escape').symlink_to(tmp_path, target_is_directory=True)
    assert check('Write', {'file_path': 'escape/outside.md', 'content': source}) == 2
