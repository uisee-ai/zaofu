import {test, expect} from '@playwright/test';

// The live API fixture is seeded with the real Proposal compiler and kernel approval.
test('read-only dashboard and CLI observe the same approved team on the live API', async ({page, request}, info) => {
  const project = process.env.ZF_E2E_STAGE_TEAM_PROJECT;
  test.skip(!project, 'Requires an isolated project with an approved PRD stage team');
  const api = `/api/projects/${encodeURIComponent(project!)}/workflow-requests/req-1/graph`;
  const structure = await request.get(api + '/structure');
  expect(structure.ok()).toBeTruthy();
  const snapshot = await structure.json();
  const exec = await request.get(api + '/execution', {headers:{'X-ZF-Structure-Generation': snapshot.generation}});
  const execution = await exec.json();
  const team = execution.nodes.find((n: {id:string}) => n.id === 'workflow:stage:prd-plan').data.team;
  expect(team.members.map((m:{role:string}) => m.role)).toEqual(['product-scan','tech-scan','planner']);
  expect(team.required_total).toBe(3);
  await page.goto('/tests/inbox-workflow.html?surface=graph-refresh&project=' + encodeURIComponent(project!));
  await page.locator('#root').evaluate(el => {el.style.height='100vh';});
  const card = page.locator('.react-flow__node[data-id="workflow:stage:prd-plan"]');
  await expect(card).toContainText('已准入 0/3 项义务');
  for (const m of team.members) await expect(card.getByText(m.role,{exact:true})).toBeVisible();
  await card.click();
  const detail = page.getByRole('complementary', {name:'图节点详情'});
  await expect(detail.getByLabel('阶段分工')).toContainText('plan-author');
  await detail.getByText(/check-tech-scan · prd-scan · 查看交接/).click();
  await expect(detail).toContainText('预定接收人：planner');
  await expect(detail).toContainText('Findings from tech-scan');
  await page.screenshot({path:info.outputPath('live-plan-team.png')});
});
