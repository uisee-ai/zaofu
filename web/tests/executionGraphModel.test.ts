import { stageWorkers, workerActivity, unmappedRoleOperationIds } from '../src/components/workflow/executionGraphModel.js';
import type { SdlcNode } from '../src/components/workflow/sdlcGraphModel.js';
const node = (id: string, role: string, task: string, time: string, stage = 'impl'): SdlcNode => ({
  id, label: id, kind: 'operation', status: 'running', views: ['workflow'], data: {
    role_instance: role, parent_stage_id: stage, task_id: task, last_event_at: time,
    recent_activity: [{ type: 'codex.hook.pre_tool_use', ts: time, payload: { tool_name: 'Bash', tool_input: 'SECRET' } }],
  },
});
const nodes = [node('old', 'dev', 'TASK-1', '2026-01-01'), node('new', 'dev', 'TASK-1', '2026-01-02'),
  node('other-task', 'dev', 'TASK-2', '2026-01-03'), node('other-stage', 'dev', 'TASK-1', '2026-01-04', 'verify'),
  node('parent', '', 'TASK-1', '2026-01-05')];
const workers = stageWorkers(nodes, 'impl');
if (workers.length !== 2 || workers[0].latest.id !== 'new' || workers[0].records.length !== 2) throw new Error('execution records were not grouped by observed role and task');
const activity = workerActivity(workers[0].latest);
if (activity?.label !== 'Bash' || JSON.stringify(activity).includes('SECRET')) throw new Error('activity must not include tool input');
if (workerActivity({ ...nodes[0], data: {} }) !== null) throw new Error('missing activity must remain unknown');
const compiledStage: SdlcNode = { id: 'workflow:stage:impl', kind: 'workflow', label: 'impl', status: 'running', views: ['workflow'], data: { stage_id: 'impl' } };
const roleEdges = ['old', 'other-stage'].map((target, index) => ({ id: String(index), source: 'workflow:role:dev', target, relation: 'executes', views: ['workflow' as const] }));
const unmapped = unmappedRoleOperationIds('workflow:role:dev', [...nodes, compiledStage], roleEdges);
if (unmapped.length !== 1 || unmapped[0] !== 'other-stage') throw new Error('role view must not duplicate records already shown in a compiled stage');
console.log('executionGraphModel tests passed');
