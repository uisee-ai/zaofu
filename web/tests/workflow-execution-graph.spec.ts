import { expect, test } from '@playwright/test';

const scope = { request_id: 'A', revision: 1, run_id: 'run-A', proposal_digest: 'A', source_seq: 10, head_seq: 10, historical: false, execution_started: true };
const stage = (id: string) => ({ id: `workflow:stage:${id}`, kind: 'workflow', label: id, status: 'running', views: ['workflow'], data: { stage_id: id, roles: [{ name: 'configured-only' }] } });
const op = (id: string, role: string, task: string, ts: string) => ({ id: `operation:${id}`, kind: 'operation', label: role, status: 'running', views: ['workflow', 'timeline'], data: { role_instance: role, workflow_run_id: 'run-A', task_id: task, parent_stage_id: 'impl', last_event_at: ts, active_attempt_id: id, recent_activity: [{ id, type: 'codex.hook.pre_tool_use', ts, payload: { tool_name: 'Bash', tool_input: { file_path: 'src/login.ts', api_key: 'secret-command' } } }] } });
const budgetObservation = {
  source_event_id: 'budget', total_tokens: 1510948, input_tokens: 51770,
  cache_read_tokens: 1456256, output_tokens: 2922, total_usd: null, unpriced_entries: 21,
  elapsed_seconds: 334, limits: { token_budget: 1500000, timeout_seconds: 600 },
};
const graph = { scope, diagnostics: [], capabilities: {}, versions: [], timeline: [], plans: [], analysis: { intervals: [] }, nodes: [
  stage('plan'), stage('impl'),
  { id: 'run:run-A', kind: 'run', label: 'run-A', status: 'running', views: ['workflow'], data: {} },
  { id: 'workflow:role:orchestrator', kind: 'workflow', label: 'orchestrator', status: 'settled', views: ['workflow'], data: {} },
  { ...op('orchestration', 'orchestrator', 'TASK-1', '2026-01-01T02:30:00Z'), data: { ...op('orchestration', 'orchestrator', 'TASK-1', '2026-01-01T02:30:00Z').data, parent_stage_id: 'checkpoint' } },
  { id: 'goal', kind: 'requirement', label: '交付目标', status: 'confirmed', views: ['modules'], data: {} },
  { id: 'claim', kind: 'claim', label: '验收声明', status: 'mapped', views: ['modules'], data: {} },
  { id: 'task:1', kind: 'task', label: '实现登录', status: 'in_progress', views: ['modules'], data: { task_id: 'TASK-1' } },
  { id: 'task:2', kind: 'task', label: '验证登录', status: 'pending', views: ['modules'], data: { task_id: 'TASK-2' } },
  { id: 'task:3', kind: 'task', label: '独立任务', status: 'pending', views: ['modules'], data: { task_id: 'TASK-3' } },
  { id: 'module:1', kind: 'module', label: '隐藏模块', status: 'pending', views: ['modules'], data: {} },
  op('old', 'dev-1', 'TASK-1', '2026-01-01T01:00:00Z'), { ...op('new', 'dev-1', 'TASK-1', '2026-01-01T02:00:00Z'), data: { ...op('new', 'dev-1', 'TASK-1', '2026-01-01T02:00:00Z').data, plan_id: 'P1', budget_observation: budgetObservation, context_observation: { context_usage_ratio: 0.31, context_window_tokens: 258400 } } },
  op('second', 'dev-2', 'TASK-2', '2026-01-01T03:00:00Z'), op('third', 'dev-3', 'TASK-3', '2026-01-01T04:00:00Z'),
], edges: [
  { id: 'role-operation', source: 'workflow:role:orchestrator', target: 'operation:orchestration', relation: 'executes', views: ['workflow'] },
  { id: 'stage-link', source: 'workflow:stage:plan', target: 'workflow:stage:impl', relation: 'next', views: ['workflow'] },
  { id: 'declares', source: 'goal', target: 'claim', relation: 'declares', views: ['modules'] },
  { id: 'implements', source: 'claim', target: 'task:1', relation: 'implemented_by', views: ['modules'] },
  { id: 'depends', source: 'task:1', target: 'task:2', relation: 'depends_on', views: ['modules'] },
  { id: 'goal-run', source: 'goal', target: 'run:run-A', relation: 'proposes', views: ['lifecycle'] },
] };

const executionSummary = { ...graph, schema_version: 'sdlc-workbench-execution.v1', partial: true, execution_summary: true, generation: 'one' };

test('Workflow does not display Plan documents or preview even after loading the full graph', async ({ page }) => {
  let fullReads = 0;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: 'one', scope, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    if (path.endsWith('/graph/execution')) return route.fulfill({ json: { ...executionSummary, pending_plan_ids: ['P1'] } });
    if (path.endsWith('/graph')) { fullReads++; return route.fulfill({ json: { ...graph, plans: [{ plan_id: 'P1', status: 'approved', artifacts: [], refs: {}, action_payload: {} }] } }); }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  await expect(page.locator('.execution-context-run')).toBeVisible();
  for (const text of ['数据读取正常', 'Run 状态：', '最近执行观测', 'Plan 待审阅', '最近失败执行', '阶段连线表示', '其他阶段：']) {
    await expect(page.getByText(text, { exact: false })).toHaveCount(0);
  }
  expect(fullReads).toBe(0);
  await page.getByRole('button', { name: 'Graph filters' }).click();
  await expect.poll(() => fullReads).toBe(1);
  await expect(page.locator('.sdlc-plans')).toHaveCount(0);
  await expect(page.getByText('PRD / Plan documents', { exact: false })).toHaveCount(0);
  await expect(page.getByRole('region', { name: 'Workflow PRD Plan Preview' })).toHaveCount(0);
  await expect(page.getByTestId('actions')).toHaveText('[]');
});

test('single task offers expandable declared Claims without drawing an empty dependency graph', async ({ page }) => {
  const one = { ...graph, nodes: graph.nodes.filter(node => !['task:2', 'task:3'].includes(node.id)), edges: graph.edges.filter(edge => edge.id !== 'depends') };
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ status: 404 });
    if (path.endsWith('/graph')) return route.fulfill({ json: one });
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  await page.getByRole('button', { name: '任务 Graph' }).click();
  const single = page.getByRole('region', { name: '单任务概览' });
  await expect(single).toContainText('当前仅有一个任务');
  await single.getByText('关联 Claim · 1').click();
  await expect(single).toContainText('验收声明');
  await expect(single).toContainText('任务映射不代表验收通过');
  await single.getByRole('button', { name: /实现登录/ }).click();
  await expect(page.getByRole('complementary', { name: '图节点详情' })).toContainText('TASK-1');
});

test('observed worker records, task relations and cross-view navigation', async ({ page }, info) => {
  let fullReads = 0;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [{ request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' }] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: 'one', scope, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    if (path.endsWith('/graph/execution')) { expect(route.request().headers()['x-zf-structure-generation']).toBe('one'); return route.fulfill({ json: executionSummary }); }
    if (path.endsWith('/graph')) { fullReads++; return route.fulfill({ json: graph }); }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' }, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  const nav = page.getByRole('navigation', { name: 'Workflow graph views' });
  await expect(nav.getByRole('button', { name: '执行 Graph' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.react-flow__node[data-id="goal"]')).toContainText('交付目标');
  await expect(page.locator('.react-flow__node[data-id="run:run-A"]')).toContainText('run-A');
  await expect(page.locator('.react-flow__node[data-id="ui:run-result:A"]')).toContainText('尚未观测到结束');
  await expect(page.getByText('运行归属', { exact: true }).first()).toBeVisible();
  await expect(page.locator('.react-flow__edge[data-id^="ui:run-scope:"] .react-flow__edge-path').first()).not.toHaveAttribute('marker-end');
  await expect(page.locator('.react-flow__edge[data-id^="ui:result:"]')).toHaveCount(0);
  const role = page.locator('.react-flow__node[data-id="workflow:role:orchestrator"]');
  await expect(role).toContainText('orchestrator');
  await expect.poll(async () => { const card = await role.boundingBox(); const canvas = await page.getByTestId('workflow-graph-canvas').boundingBox(); return Boolean(card && canvas && card.x >= canvas.x && card.x + card.width <= canvas.x + canvas.width); }).toBe(true);
  const impl = page.locator('.react-flow__node[data-id="workflow:stage:impl"]');
  await expect(impl).toContainText('3 个已观测角色 · 4 条执行记录');
  await expect(impl).toContainText('TASK-1 · 实现登录');
  await expect(impl).toContainText('Bash');
  expect(fullReads).toBe(0);
  await expect(impl).not.toContainText('secret-command');
  await expect(impl).not.toContainText('configured-only');
  await page.getByRole('button', { name: '查看全图', exact: true }).click();
  await impl.getByRole('button', { name: /dev-1/ }).click();
  const inspector = page.getByRole('complementary', { name: '图节点详情' });
  await expect(inspector).toContainText('operation:new');
  await expect(inspector.getByLabel('执行预算用量')).toContainText('1,510,948 / 1,500,000 tokens');
  await expect(inspector.getByLabel('执行预算用量')).toContainText('非缓存输入：51,770 · 缓存读取：1,456,256 · 输出：2,922');
  await expect(inspector.getByLabel('执行预算用量')).toContainText('金额：未知（21 条未定价）');
  await expect(inspector.getByLabel('执行预算用量')).not.toContainText('$0');
  await expect(inspector).toContainText('最近上下文占用：31.0%');
  await expect(inspector.getByRole('button', { name: '打开 PRD / Plan Preview' })).toHaveCount(0);
  await inspector.getByRole('button', { name: '在任务 Graph 查看 Task' }).click();
  await expect(nav.getByRole('button', { name: '任务 Graph' })).toHaveAttribute('aria-pressed', 'true');
  await expect(inspector).toContainText('实现登录');
  await expect(page.locator('.react-flow__node[data-id="module:1"]')).toHaveCount(0);
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);
  await expect(inspector).toContainText('关联执行 · 3 条');
  await inspector.getByRole('button', { name: '聚焦任务上下游' }).click();
  await expect(page.locator('.react-flow__node[data-id="task:3"]')).toHaveCount(0);
  await page.getByRole('button', { name: '显示全部任务' }).click();
  await expect(page.locator('.react-flow__node[data-id="task:3"]')).toHaveCount(1);
  await page.getByRole('button', { name: 'Claim 覆盖' }).click();
  await expect(page.locator('.react-flow__edge')).toHaveCount(2);
  await inspector.getByRole('button', { name: /impl · dev-1/ }).first().click();
  await expect(nav.getByRole('button', { name: '执行 Graph' })).toHaveAttribute('aria-pressed', 'true');
  await expect(inspector).toContainText('活动轨迹');
  await expect(inspector).toContainText('调用工具：Bash');
  await expect(inspector.getByLabel('Worker 执行详情')).toContainText('src/login.ts');
  await expect(inspector.getByLabel('Worker 执行详情')).not.toContainText('secret-command');
  const traces = inspector.getByRole('link', { name: '查看 Traces 列表' });
  await expect(traces).toHaveAttribute('href', '/?project=a&page=traces');
  await expect(inspector).toContainText('当前执行记录未提供 Trace ID');
  await expect(page.getByLabel('更多视图')).toHaveCount(0);
  await page.getByRole('button', { name: 'Close node details' }).click();
  await impl.getByRole('button', { name: /展开全部/ }).click();
  await expect(impl).toContainText('dev-3');
  await page.getByRole('button', { name: '展开记录', exact: true }).click();
  await expect(impl).toContainText('记录：old');
  await expect(impl).toContainText('记录：new');
  await page.screenshot({ path: info.outputPath('execution-graph.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await nav.getByRole('button', { name: 'Timeline' }).click();
  await expect(page.getByTestId('workflow-execution-timeline')).toBeVisible();
});

test('started execution loads automatically and retains a retryable structure on failure', async ({ page }) => {
  let reads = 0;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: 'one', scope, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    if (path.endsWith('/graph/execution')) { reads++; return reads === 1 ? route.fulfill({ status: 503 }) : route.fulfill({ json: executionSummary }); }
    if (path.endsWith('/graph')) return route.fulfill({ json: graph });
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  await expect(page.getByRole('alert')).toContainText('503');
  await expect(page.locator('.execution-graph-stage').first()).toContainText('执行记录未加载');
  expect(reads).toBe(1);
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.locator('.react-flow__node[data-id="workflow:stage:impl"]')).toContainText('3 个已观测角色');
  expect(reads).toBe(2);
});

test('full graph remains reachable when the execution summary fails', async ({ page }) => {
  let fullReads = 0;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: 'one', scope, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    if (path.endsWith('/graph/execution')) return route.fulfill({ status: 503 });
    if (path.endsWith('/graph')) { fullReads++; return route.fulfill({ json: graph }); }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await expect(page.getByRole('alert')).toContainText('503');
  await page.getByRole('button', { name: '读取完整运行图', exact: true }).click();
  await expect.poll(() => fullReads).toBe(1);
  await expect(page.getByRole('alert')).toHaveCount(0);
});

test('operation details show only recorded parent-child execution links', async ({ page }) => {
  const edges = [...graph.edges,
    { id: 'child', source: 'operation:new', target: 'operation:second', relation: 'delegates', views: ['workflow'] },
    { id: 'missing', source: 'operation:missing', target: 'operation:new', relation: 'delegates', views: ['workflow'] }];
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ status: 404 });
    if (path.endsWith('/graph')) return route.fulfill({ json: { ...graph, edges } });
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  await page.locator('.react-flow__node[data-id="workflow:stage:impl"]').getByRole('button', { name: /dev-1/ }).click();
  const details = page.getByRole('complementary', { name: '图节点详情' });
  await expect(details).toContainText('已记录的父子执行');
  await expect(details.getByRole('button', { name: /父执行/ })).toHaveCount(0);
  await details.getByRole('button', { name: '子执行 · dev-2' }).click();
  await expect(details).toContainText('operation:second');
  await expect(details.getByRole('button', { name: '父执行 · dev-1' })).toBeVisible();
});

test('an explicitly bound Trace ID opens the matching trace in the project', async ({ page }) => {
  const nodes = graph.nodes.map(node => node.id === 'operation:new' ? { ...node, data: { ...node.data, trace_id: 'trace / one' } } : node);
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ status: 404 });
    if (path.endsWith('/graph')) return route.fulfill({ json: { ...graph, nodes } });
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh&request=A&node=operation%3Anew');
  const details = page.getByRole('complementary', { name: '图节点详情' });
  await expect(details.getByRole('link', { name: '查看关联 Trace' })).toHaveAttribute('href', '/?project=a&page=traces&trace_id=trace+%2F+one');
  await expect(details).not.toContainText('当前执行记录未提供 Trace ID');
});

test('stale execution summary refreshes structure and remains generation-bound', async ({ page }) => {
  let structures = 0;
  let summaries = 0;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) {
      structures++;
      const seq = structures === 1 ? 10 : 11;
      return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: structures === 1 ? 'one' : 'two', scope: { ...scope, source_seq: seq, head_seq: seq }, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    }
    if (path.endsWith('/graph/execution')) {
      summaries++;
      if (summaries === 1) return route.fulfill({ status: 409 });
      expect(route.request().headers()['x-zf-structure-generation']).toBe('two');
      return route.fulfill({ json: { ...executionSummary, generation: 'two', scope: { ...scope, source_seq: 11, head_seq: 11 } } });
    }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  await expect(page.getByRole('alert')).toContainText('409');
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.locator('.react-flow__node[data-id="workflow:stage:impl"]')).toContainText('3 个已观测角色');
  expect(structures).toBe(2);
  expect(summaries).toBe(2);
});

test('unidentified tasks are never linked to unrelated execution records', async ({ page }) => {
  const unbound = { ...graph, nodes: [stage('impl'),
    { id: 'task:missing', kind: 'task', label: '不相关任务', status: 'pending', views: ['modules'], data: {} },
    { ...op('unbound', 'dev-unbound', '', '2026-01-01T00:00:00Z'), data: { ...op('unbound', 'dev-unbound', '', '2026-01-01T00:00:00Z').data, task_id: '' } }], edges: [] };
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ status: 404 });
    if (path.endsWith('/graph')) return route.fulfill({ json: unbound });
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  const worker = page.getByRole('button', { name: /dev-unbound/ });
  await expect(worker).toContainText('Task：未关联');
  await expect(worker).not.toContainText('不相关任务');
  await worker.click();
  const inspector = page.getByRole('complementary', { name: '图节点详情' });
  await expect(inspector).toContainText('Task：未关联');
  await expect(inspector.getByRole('button', { name: '在任务 Graph 查看 Task' })).toHaveCount(0);
  await page.getByRole('button', { name: '任务 Graph' }).click();
  await page.getByRole('region', { name: '单任务概览' }).getByRole('button', { name: /不相关任务/ }).click();
  await expect(inspector).toContainText('关联执行 · 0 条');
});

test('opening full workflow structure loads the complete graph', async ({ page }) => {
  let fullReads = 0;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: 'one', scope, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    if (path.endsWith('/graph/execution')) return route.fulfill({ json: executionSummary });
    if (path.endsWith('/graph')) { fullReads++; return route.fulfill({ json: graph }); }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  await expect(page.locator('.execution-context-run')).toBeVisible();
  expect(fullReads).toBe(0);
  await page.getByRole('button', { name: 'Graph filters' }).click();
  await page.getByRole('button', { name: '展开角色、汇聚与事件结构' }).click();
  await expect.poll(() => fullReads).toBe(1);
});

test('worker deep link waits for the bound execution summary without loading the full graph', async ({ page }) => {
  let fullReads = 0;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: 'one', scope, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    if (path.endsWith('/graph/execution')) return route.fulfill({ json: executionSummary });
    if (path.endsWith('/graph')) { fullReads++; return route.fulfill({ json: graph }); }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh&request=A&node=operation%3Anew');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  await expect(page.getByRole('complementary', { name: '图节点详情' })).toContainText('operation:new');
  expect(fullReads).toBe(0);
});

test('live execution refresh preserves worker selection and viewport without requesting the full graph', async ({ page }) => {
  let structures = 0;
  let summaries = 0;
  let fullReads = 0;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) {
      structures++;
      const seq = structures === 1 ? 10 : 11;
      return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: structures === 1 ? 'one' : 'two', scope: { ...scope, source_seq: seq, head_seq: seq }, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    }
    if (path.endsWith('/graph/execution')) {
      summaries++;
      const updated = route.request().headers()['x-zf-structure-generation'] === 'two';
      return route.fulfill({ json: { ...executionSummary, generation: updated ? 'two' : 'one', scope: { ...scope, source_seq: updated ? 11 : 10, head_seq: updated ? 11 : 10 } } });
    }
    if (path.endsWith('/graph')) { fullReads++; return route.fulfill({ json: graph }); }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  const impl = page.locator('.react-flow__node[data-id="workflow:stage:impl"]');
  await expect(impl).toContainText('dev-1');
  await page.getByRole('button', { name: '查看全图', exact: true }).click();
  await impl.getByRole('button', { name: /dev-1/ }).click();
  await page.getByRole('button', { name: /zoom in/i }).click();
  await page.waitForTimeout(350);
  const viewport = page.locator('.react-flow__viewport');
  const before = await viewport.getAttribute('style');
  await page.getByRole('button', { name: 'Graph connection' }).click();
  await page.getByRole('button', { name: 'Graph connection' }).click();
  await expect.poll(() => summaries).toBe(2);
  await expect(page.getByRole('complementary', { name: '图节点详情' })).toContainText('operation:new');
  await page.waitForTimeout(300);
  expect(await viewport.getAttribute('style')).toBe(before);
  expect(fullReads).toBe(0);
});

test('Run result never claims completion without matching terminal evidence', async ({ page }) => {
  let withEvidence = true;
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ status: 404 });
    if (path.endsWith('/graph')) {
      const nodes = graph.nodes.map(node => node.id === 'run:run-A' ? { ...node, status: 'done', data: withEvidence ? { terminal_event_id: 'evt-done' } : {} } : node);
      nodes.push({ id: 'workflow:terminal:done', kind: 'workflow', label: 'terminal done', status: 'done', views: ['workflow'], data: withEvidence ? { terminal_event_id: 'evt-done' } : {} });
      return route.fulfill({ json: { ...graph, nodes } });
    }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  const result = page.locator('.react-flow__node[data-id="ui:run-result:A"]');
  await expect(result).toContainText('已完成');
  await expect(result).toContainText('内核终态事件');
  await expect(page.locator('.react-flow__edge[data-id^="ui:result:"]')).toHaveCount(1);
  await page.getByRole('button', { name: '查看全图', exact: true }).click();
  await result.click();
  await expect(page.getByRole('complementary', { name: '图节点详情' })).toContainText('run:run-A');
  withEvidence = false;
  await page.reload();
  await expect(result).toContainText('结束证据未观测');
  await expect(page.locator('.execution-context-run')).toContainText('终态证据未观测');
  await expect(page.locator('.react-flow__edge[data-id^="ui:result:"]')).toHaveCount(0);
});

test('unstarted preview shows goal and pending Run without inventing execution', async ({ page }) => {
  let fullReads = 0;
  const previewScope = { ...scope, run_id: '', execution_started: false };
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: '准备目标', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ json: { schema_version: 'sdlc-workbench-structure.v1', partial: true, generation: 'one', scope: previewScope, nodes: graph.nodes.slice(0, 2), edges: [graph.edges[1]], diagnostics: [] } });
    if (path.endsWith('/graph')) { fullReads++; return route.fulfill({ json: graph }); }
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: { objective: '准备目标' }, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  await expect(page.locator('.execution-context-goal')).toContainText('准备目标');
  await expect(page.locator('.execution-context-run')).toContainText('尚未启动');
  await expect(page.locator('.execution-context-result')).toContainText('尚未启动');
  await expect(page.getByText('运行归属', { exact: true })).toHaveCount(0);
  expect(fullReads).toBe(0);
});

test('observed workers without a compiled stage remain visible without inventing one', async ({ page }) => {
  const orphan = { ...op('orphan', 'external-worker', 'TASK-1', '2026-01-01T02:00:00Z'), data: { ...op('orphan', 'external-worker', 'TASK-1', '2026-01-01T02:00:00Z').data, parent_stage_id: 'missing-stage' } };
  const nodes = graph.nodes.filter(node => node.id === 'goal' || node.id === 'run:run-A');
  const orphanGraph = { ...graph, nodes: [...nodes, orphan], edges: [{ id: 'owned', source: 'run:run-A', target: orphan.id, relation: 'executes', views: ['workflow'] }] };
  await page.route('**/api/projects/**', route => {
    const path = new URL(route.request().url()).pathname;
    const request = { request_id: 'A', objective: 'A', status: 'proposed', revision: 1, proposal_digest: 'A' };
    if (path.endsWith('/workflow-requests')) return route.fulfill({ json: { items: [request] } });
    if (path.endsWith('/graph/structure')) return route.fulfill({ status: 404 });
    if (path.endsWith('/graph')) return route.fulfill({ json: orphanGraph });
    if (path.includes('/workflow-requests/')) return route.fulfill({ json: { result: request, requirement: {}, proposal: {}, artifacts: {}, lifecycle: {}, links: {} } });
    return route.fulfill({ json: { ok: true, fields: {} } });
  });
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh');
  await page.locator('#root').evaluate(el => { el.style.height = '100vh'; });
  const unassigned = page.locator('.react-flow__node[data-id="ui:unassigned-workers:A"]');
  await expect(unassigned).toContainText('未归属阶段');
  await expect(unassigned).toContainText('external-worker');
  await expect(page.getByText('运行归属', { exact: true })).toBeVisible();
  await unassigned.getByRole('button', { name: /external-worker/ }).click();
  await expect(page.getByRole('complementary', { name: '图节点详情' })).toContainText('operation:orphan');
});
