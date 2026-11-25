import {expect, test, type Page} from '@playwright/test';

const project = process.env.ZF_E2E_MEMBER_SESSION_PROJECT;
const token = process.env.ZF_E2E_MEMBER_SESSION_TOKEN;
const sample = 'Reviewed the current interface contract.';
test.skip(!project || !token, 'Requires an opt-in workflow and an isolated operator credential');

function taskUrl(task: string) {
  return `/?page=workflows&request=req-1&node=task%3A${task}&project=${encodeURIComponent(project!)}`;
}
function details(page: Page) { return page.getByRole('complementary', {name:'图节点详情'}); }
async function expand(page: Page, authorized: boolean, task='T-B') {
  if (authorized) await page.addInitScript(value => localStorage.setItem('zf.webActionToken', value), token!);
  await page.goto(taskUrl(task));
  await details(page).getByText(`verification · ${task} · 查看交接`).click();
}

test('a live operator opens only the selected attempt’s shared reply', async ({page}, info) => {
  const resources: boolean[] = [];
  page.on('request', req => {
    if (!req.url().endsWith('/member-session')) return;
    const data = req.postDataJSON();
    expect(data).toMatchObject({scope:{project_id:decodeURIComponent(new URL(req.url()).pathname.split('/')[3]),request_id:'req-1',historical:false},
      operation_id:'op-T-B',attempt_id:'attempt-T-B',instance:'verify-lane-1',stage_id:'prd-lanes-impl',obligation_id:'verification'});
    expect(data.generation).toBeTruthy();
    resources.push(data.read);
  });
  await page.addInitScript(value => localStorage.setItem('zf.webActionToken', value), token!);
  await page.goto(taskUrl('T-B'));
  const detail = details(page);
  const summary = detail.getByText('verification · T-B · 查看交接');
  await expect(summary).toBeVisible();
  expect(resources).toEqual([]);
  await summary.click();
  const open = detail.getByRole('button', {name:'查看会话摘录'});
  await expect(open).toBeVisible();
  expect(resources).toEqual([false]);
  await expect(detail.getByText(sample)).toHaveCount(0);
  await open.click();
  await expect(detail.getByRole('region', {name:'会话摘录'})).toContainText(sample);
  await expect(detail).not.toContainText('private instruction');
  expect(resources).toEqual([false,true]);
  await page.screenshot({path:info.outputPath('authorized-reply.png')});
});

test('unprivileged and suspended member views reveal no session entry', async ({page}, info) => {
  const reads: boolean[] = [];
  page.on('request', req => {if (req.url().endsWith('/member-session')) reads.push(req.postDataJSON().read);});
  const denial = page.waitForResponse(r => r.url().endsWith('/member-session'));
  await expand(page, false);
  expect((await denial).status()).toBe(403);
  await expect(details(page).getByRole('button',{name:'查看会话摘录'})).toHaveCount(0);
  await expect(page.getByText(sample)).toHaveCount(0);
  await page.screenshot({path:info.outputPath('unprivileged-member.png')});
  await expand(page,true,'T-A');
  await expect(details(page)).toContainText('transport lost');
  await expect(details(page).getByRole('button',{name:'查看会话摘录'})).toHaveCount(0);
  expect(reads).toEqual([false]);
});

test('an excerpt in flight cannot appear after switching to historical replay', async ({page}, info) => {
  let release!: () => void;
  const gate = new Promise<void>(done => release = done);
  let signal!: () => void;
  const returned = new Promise<void>(done => signal = done);
  const probes: boolean[] = [];
  await page.route('**/member-session', async route => {
    const read = route.request().postDataJSON().read;
    probes.push(read);
    if (!read) {await route.continue(); return;}
    const native = await route.fetch();
    expect(native.ok()).toBeTruthy();
    signal();
    await gate;
    await route.fulfill({response:native}).catch(() => {});
  });
  await expand(page,true);
  const detail = details(page);
  await detail.getByRole('button',{name:'查看会话摘录'}).click();
  await returned;
  try {
    await detail.getByRole('button',{name:/回看状态之前/}).click();
    await expect(detail.getByRole('button',{name:'返回实时分工'})).toBeVisible();
  } finally {release();}
  await detail.getByText('verification · T-B · 查看交接').click();
  await expect(detail.getByRole('button',{name:'查看会话摘录'})).toHaveCount(0);
  await expect(page.getByText(sample)).toHaveCount(0);
  expect(probes).toEqual([false,true]);
  await page.screenshot({path:info.outputPath('historical-member.png')});
});

test('full graph can open the same generation-bound stage member', async ({page}) => {
  await page.addInitScript(value => localStorage.setItem('zf.webActionToken', value), token!);
  await page.goto(taskUrl('T-B').replace('task%3AT-B','workflow%3Astage%3Aprd-lanes-impl'));
  await expect(details(page).getByText('verification · T-B · 查看交接')).toBeVisible();
  await page.getByRole('button', {name:'Graph filters'}).click();
  const fetched = page.waitForResponse(r => new URL(r.url()).pathname.endsWith('/graph'));
  await page.getByRole('button', {name:'展开角色、汇聚与事件结构'}).click();
  const full = await fetched;
  const generation = full.request().headers()['x-zf-structure-generation'];
  expect(full.ok() && generation).toBeTruthy();
  const probe = page.waitForRequest(r => r.url().endsWith('/member-session'));
  await details(page).getByText('verification · T-B · 查看交接').click();
  expect((await probe).postDataJSON().generation).toBe(generation);
  await expect(details(page).getByRole('button', {name:'查看会话摘录'})).toBeVisible();
});
