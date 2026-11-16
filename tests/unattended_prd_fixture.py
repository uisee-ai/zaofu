"""Minimal TypeScript product for real qualification; preparation never starts it."""
from pathlib import Path
import hashlib
import json
import subprocess

import yaml
from tests.continuity_prd_fixture import configuration

PRD = '''# 双工具表单 PRD

在现有静态 TypeScript 页面上完成两个独立用户功能。复用 HTML/UI 壳，不新建框架。
1. 名称整理：normalizeName(string) 去掉首尾空白，把连续空白折成一个空格，保留中文。
空白输入返回空字符串；整理后的名称按 Unicode 字符计数超过 40 时抛出可读错误。
2. 数字摘要：summarizeNumbers(string) 接受逗号分隔的有限数字，保留小数和负数。
空白输入返回 count=0,total=0,average=null；非空输入每段必须是完整有效数字，空段/NaN/Infinity/文本均报错。
返回 count,total,average；已有 UI 渲染摘要与错误。

两个模块没有依赖，可按各自模块及测试拆两个有界 Task，禁止把 host 故障注入作为业务 Task。
范围分别为 app/src/normalize.ts + app/tests/normalize.test.mjs；app/src/numbers.ts + app/tests/numbers.test.mjs。
UI 壳、package.json、tsconfig 和 tests/browser.cjs 为预设验收输入，不要改动。
每个 Task 必须 RED→GREEN，npm test 和 npm run build。保留独立 Task Verify。
最终冻结 Candidate 额外执行 npm run build && node tests/browser.cjs，真实 Docker Playwright 桌面和移动验收，保存截图、trace、WebM、镜像/浏览器/源码身份。
最后一条仅由 Candidate Verify 负责，不能要求 Impl 在沙箱内访问 Docker。

运行使用 v5、2 Impl + 2 Verify，Plan Preview 需要本操作员审阅批准；角色 workspace-write。
预授权容器采证由 Run Manager 执行原合同命令。若沙箱监听/浏览器不可用，提交真实的 failed/abstained
基础设施结果及错误，不将其写成产品拒绝；恢复后读取绑定的容器回执并独立审查验收条件。
'''

BROWSER = r'''const { chromium } = require('/app/node_modules/playwright');
const fs = require('node:fs'); const path = require('node:path');
const http = require('node:http'); const assert = require('node:assert/strict');
const root = path.resolve('app'); const output = process.env.ZF_EVIDENCE_DIR;
if (!output) throw new Error('ZF_EVIDENCE_DIR required');
const server = http.createServer((req, res) => {
  const relative = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(root, '.' + (relative === '/' ? '/index.html' : relative));
  if (!file.startsWith(root + '/')) { res.writeHead(403); res.end(); return; }
  try { res.setHeader('Content-Type', file.endsWith('.html') ? 'text/html; charset=utf-8' : 'text/javascript'); res.end(fs.readFileSync(file)); }
  catch { res.writeHead(404); res.end(); }
});
(async () => {
  await new Promise(resolve => server.listen(0, '0.0.0.0', resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  const browser = await chromium.launch({executablePath:'/ms-playwright/chromium-1226/chrome-linux64/chrome',args:['--no-sandbox']});
  try {
    fs.writeFileSync(path.join(output,'environment.json'),JSON.stringify({browser:browser.version(),node:process.version,
      source_commit:process.env.ZF_CANDIDATE_COMMIT,image:process.env.ZF_BROWSER_IMAGE_ID}));
    for (const [name, width] of [['desktop',1280],['mobile',390]]) {
      const context=await browser.newContext({viewport:{width,height:800},recordVideo:{dir:output}});
      await context.tracing.start({screenshots:true,snapshots:true,sources:true});
      const page=await context.newPage(); const video=page.video(); const errors=[];
      page.on('pageerror', error => errors.push(error.message));
      try {
        await page.goto(url);
        await page.getByLabel('名称').fill('  洛书   Team  ');
        await page.getByRole('button',{name:'整理名称'}).click();
        await page.waitForFunction(()=>document.querySelector('#name-output').textContent==='洛书 Team');
        await page.getByLabel('名称').fill(' '.repeat(4));
        await page.getByRole('button',{name:'整理名称'}).click();
        assert.equal(await page.locator('#name-output').textContent(),'');
        await page.getByLabel('名称').fill('😀'.repeat(40));
        await page.getByRole('button',{name:'整理名称'}).click();
        assert.equal(await page.locator('#name-output').textContent(),'😀'.repeat(40));
        await page.getByLabel('名称').fill('x'.repeat(41));
        await page.getByRole('button',{name:'整理名称'}).click();
        assert.ok((await page.locator('#name-error').textContent()).length>0);
        await page.getByLabel('数字').fill('1, -2, 4.5');
        await page.getByRole('button',{name:'计算'}).focus(); await page.keyboard.press('Enter');
        assert.equal(await page.locator('#numbers-output').textContent(),'3 / 3.5 / '+(3.5/3));
        for(const bad of ['1,,2','NaN','Infinity','2x']) {
          await page.getByLabel('数字').fill(bad); await page.getByRole('button',{name:'计算'}).click();
          assert.ok((await page.locator('#numbers-error').textContent()).length>0);
        }
        await page.getByLabel('数字').fill(' '); await page.getByRole('button',{name:'计算'}).click();
        assert.equal(await page.locator('#numbers-output').textContent(),'0 / 0 / —');
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
        assert.deepEqual(errors,[]);
        await page.screenshot({path:path.join(output,name+'.png')});
        console.log(name+': normalization/numeric summary/invalid inputs/keyboard/mobile passed');
      } finally {
        await context.tracing.stop({path:path.join(output,name+'-trace.zip')});
        await context.close(); await video.saveAs(path.join(output,name+'.webm'));
      }
    }
  } finally { await browser.close(); server.close(); }
})().catch(error=>{console.error(error);server.close();process.exitCode=1;});
'''


def prepare(root: Path, *, harness: Path, compiler_root: Path) -> dict:
    root.mkdir(parents=True)
    for name in ('app/src','app/tests','tests','docs/prd','tmux','evidence'):
        (root/name).mkdir(parents=True)
    files = {
        'docs/prd/TODO.md': PRD,
        'tests/browser.cjs': BROWSER,
        'AGENTS.md': '''# 隔离 PRD 验证项目
遵循 docs/prd/TODO.md；只修改当前任务分配的产品文件。产品 checkout 之外，允许原生 Edit/apply_patch
原地更新 Kernel 在 briefing 中明确指定的当前 operation result scratch。不得创建/移动其它运行文件。
源码/证据输入只读；事件和 Store 通过受控 CLI 管理。不要绕过 Provider 沙箱；不自行发 passed。
测试环境已预置 TypeScript。浏览器在固定 Docker 镜像里验收，不安装宿主浏览器。
''',
        '.gitignore': '.zf/\n.codex/\n.claude/\nnode_modules/\napp/dist/\napp/test-results/\nartifacts/\ntmux/\nevidence/\nworkspace-registry/\n',
        'package.json': json.dumps({'name':'two-tools-qualification','private':True,'scripts':{'build':'node_modules/.bin/tsc -p app/tsconfig.json','test':'npm run build && node --test app/tests/*.test.mjs'}}),
        'app/tsconfig.json': json.dumps({'compilerOptions':{'target':'ES2022','module':'ES2022','strict':True,'outDir':'dist','rootDir':'src'},'include':['src/**/*.ts']}),
        'app/src/normalize.ts': 'export function normalizeName(value: string): string { return value; }\n',
        'app/src/numbers.ts': 'export function summarizeNumbers(value: string): {count: number; total: number; average: number|null} { return {count:0,total:0,average:null}; }\n',
        'app/tests/normalize.test.mjs': "import {test} from 'node:test'; import assert from 'node:assert/strict'; test('scaffold import',async()=>assert.equal(typeof (await import('../dist/normalize.js')).normalizeName,'function'));\n",
        'app/tests/numbers.test.mjs': "import {test} from 'node:test'; import assert from 'node:assert/strict'; test('scaffold import',async()=>assert.equal(typeof (await import('../dist/numbers.js')).summarizeNumbers,'function'));\n",
        'app/package.json': '{"type":"module"}\n',
        'app/index.html': '''<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>双工具</title>
<style>body{max-width:600px;margin:24px auto;padding:16px;font-family:system-ui}input{max-width:100%;box-sizing:border-box}section{padding:20px 0}output{display:block;overflow-wrap:anywhere}</style>
<h1>双工具</h1><section><label>名称<input id="name"></label><button id="normalize">整理名称</button><output id="name-output"></output><div role="alert" id="name-error"></div></section>
<section><label>数字<input id="numbers"></label><button id="calculate">计算</button><output id="numbers-output"></output><div role="alert" id="numbers-error"></div></section>
<script type="module">import {normalizeName} from './dist/normalize.js';import {summarizeNumbers} from './dist/numbers.js';
const q=s=>document.querySelector(s);q('#normalize').onclick=()=>{q('#name-error').textContent='';try{q('#name-output').textContent=normalizeName(q('#name').value)}catch(e){q('#name-error').textContent=e.message}};
q('#calculate').onclick=()=>{q('#numbers-error').textContent='';try{const x=summarizeNumbers(q('#numbers').value);q('#numbers-output').textContent=x.count+' / '+x.total+' / '+(x.average===null?'—':x.average)}catch(e){q('#numbers-error').textContent=e.message}};</script></html>''',
    }
    for name, body in files.items():
        (root/name).write_text(body)
    docs=configuration(root,harness=harness)
    cfg=docs[1]['spec']; spec=docs[0]['spec']
    # Native worker environments copy the installed compiler from a stable local source.
    cfg['project']['scripts']['setup']=cfg['project']['scripts']['setup'].replace(str(harness/'web/node_modules/typescript'),str(compiler_root))
    for role in cfg['roles']:
        if role.get('instance_id') in {'planner','plan-critic'} or role.get('name') in {'planner','plan-critic'}:
            role['model']='gpt-6-astra'
    cfg['runtime']['run_manager']['resident_agent']={'enabled':True,'model':'gpt-6.1-terra','model_reasoning_effort':'high'}
    cfg['runtime']['run_manager'].setdefault('source_repair',{}).update(enabled=True, backend='codex',model='gpt-6.1-terra',model_reasoning_effort='high',
        publish_candidate=True,apply_policy='verified_checkpoint_apply',allow_paths=['src/zf/runtime/host_verification.py','tests/test_host_verification.py'])
    cfg['runtime']['autoresearch_resident'].update(enabled=True,worktree_root=str(root/'repair-worktrees'),on_parent_terminal='continue')
    cfg['runtime']['host_verification']={'enabled':True,'approval_ref':'owner:unattended-qualification-20261116',
        'image':subprocess.check_output(['docker','image','inspect','mcp/playwright:latest','--format','{{.Id}}'],text=True).strip(),
        'network':'host','protected_files':{'tests/browser.cjs':hashlib.sha256(BROWSER.encode()).hexdigest()},
        'commands':['npm test','npm run build','npm run build && node tests/browser.cjs'],'max_executions_per_run':12,'timeout_seconds':180}
    cfg['runtime']['git']['auto_ship_on_judge_passed']=True
    (root/'zf.yaml').write_text(yaml.safe_dump_all(docs,sort_keys=False))
    return {'root':str(root),'prd_sha256':hashlib.sha256(PRD.encode()).hexdigest(),'browser_sha256':hashlib.sha256(BROWSER.encode()).hexdigest()}
