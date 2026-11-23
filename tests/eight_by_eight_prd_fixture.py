"""Independent product contracts for real 8+8 capacity qualification.

Preparation writes an isolated project; it never starts or approves a Run.
"""
from pathlib import Path
import hashlib
import json

import yaml

from tests.continuity_prd_fixture import configuration


# Each surface owns one module and one test file; the UI is frozen acceptance input.
FEATURES = [
    ("normalize", "名称整理", "首尾去空白，内部连续空白折为单个空格；保留中文。", [("  洛书   Team  ", "洛书 Team"), ("   ", "")]),
    ("reverse", "字符反转", "按 Unicode 码点反转完整输入，保留空白；不按 UTF-16 单元反转。", [("A😀中", "中😀A"), ("", "")]),
    ("word_count", "单词计数", "首尾去空白后按连续空白分词，返回词数；空输入返回0。", [("  alpha\n洛书 beta ", 3), ("   ", 0)]),
    ("unique_words", "去重词序", "按空白分词，大小写敏感去重，保留首次顺序，以单空格拼接。", [("a b a 中 b", "a b 中"), ("  ", "")]),
    ("slug", "ASCII短名", "转小写；连续非ASCII字母数字替换为连字符，再去首尾连字符。", [(" Hello, World! ", "hello-world"), ("中文", ""), ("", "")]),
    ("mask", "尾四位脱敏", "按Unicode码点，保留最后4个字符，前面每个字符替为*；不足4位原样返回。", [("ab😀1234", "***1234"), ("中😀", "中😀"), ("", "")]),
    ("lines", "非空行编号", "按LF拆行，每行trim，去掉空行，依次返回1: 内容并用LF连接；兼容CRLF。", [(" a\r\n\n b ", "1: a\n2: b"), ("\n ", "")]),
    ("initials", "词首缩写", "按空白分词，取每词首个Unicode码点、转大写并拼接；空输入返回空串。", [(" alice bob 中华 😀wow", "AB中😀"), (" ", "")]),
    ("sum", "数列求和", "CSV数字列表按输入顺序求和；空输入返回0。", [("1,-2,4.5", 3.5), (" ", 0)]),
    ("min", "数列最小值", "CSV数字列表最小值；空输入返回null。", [("3,-2,4.5", -2), (" ", None)]),
    ("max", "数列最大值", "CSV数字列表最大值；空输入返回null。", [("3,-2,4.5", 4.5), (" ", None)]),
    ("median", "数列中位数", "CSV数字列表按数值排序；偶数个取中间两项算术平均，奇数取中间项；空返回null。", [("9,1,4,2", 3), ("10,2,3", 3), ("", None)]),
    ("unique_numbers", "数列稳定去重", "CSV数字列表按数值去重，保留首次顺序，返回数字数组；空返回[]。", [("1,1.0,-2,1", [1, -2]), ("", [])]),
    ("ascending", "数列升序", "CSV数字列表按数值升序返回数组，保留重复；空返回[]。", [("10,2,-1,2", [-1, 2, 2, 10]), ("", [])]),
    ("positive_count", "正数计数", "CSV数字列表统计严格大于0的项数；零和负数不计，空返回0。", [("-1,0,2,0.5", 2), ("", 0)]),
    ("average", "数列均值", "CSV数字列表按输入顺序求和后除以数量；空返回null。", [("1,2,6", 3), ("", None)]),
]


def prepare(root: Path, *, harness: Path, image: str) -> dict:
    """Freeze sixteen independent contracts and a reviewed v5 test configuration."""
    root.mkdir(parents=True)
    for name in ("app/src", "app/tests", "tests", "docs/prd", "tmux", "evidence"):
        (root / name).mkdir(parents=True)
    prd = """# 十六工具 PRD：真实 8 Impl + 8 Verify 验收

复用冻结的静态 TypeScript 页面，完成下列16个独立功能。每个模块导出 run(value: string)。
每项独立用户行为分别一个有界Task，拥有其app/src/<id>.ts及app/tests/<id>.test.mjs，
这些Task无依赖、无共享写文件；不要合并成巨型Task，也不要另拆基础设施Task。
先写失败断言并保存RED，再实现GREEN；不改UI、包配置、浏览器脚本、验收输入或其他模块。
每Task执行 npm run build && node --test app/tests/<id>.test.mjs 和独立Task Verify。
集成rolling smoke必须先build再test，干净集成工作区不保证有dist；最终冻结Candidate执行
npm test、npm run build、npm run build && node tests/browser.cjs，由独立Candidate Verify审查。
浏览器只通过既有预授权Docker宿主采证机制运行，不能要求沙箱Impl访问Docker。
桌面和移动端逐项断言，保存截图、trace、WebM及镜像/浏览器/源码身份。Judge负责最终验收。
浏览器命令的 producer_paths 应与冻结脚本一致：在 ZF_EVIDENCE_DIR/ 下逐项声明
desktop.png、mobile.png、desktop-trace.zip、mobile-trace.zip、desktop.webm、mobile.webm、environment.json。
键盘验收为按钮聚焦后按Enter触发；textarea内Enter保留换行，不改冻结页面。
规范命令注册表仅含上述3条全局命令和16条逐模块build+test命令；额外边界断言写入模块测试，
浏览器断言沿冻结脚本执行。不得额外生成未授权宿主命令或要求监工临时补采证。

后8个数列模块共享语义但不共享源文件：全空白为[]；否则逗号分段、每段trim，拒绝空段，
只允许有可选正负号的十进制整数/小数及可选指数（如1、.5、1.、-2e3）。拒绝NaN、Infinity、
0x等进制前缀、尾缀文本及转换后非有限数，抛可读Error。Number算术，测试在安全有限范围内。
不得用parseFloat接受尾缀；所有返回值应可JSON序列化。

运行使用v5.1 task-scoped-v1：完整共享约束放入 execution_spec 的 shared 分区，
每Task的实现/验收/test/real-E2E义务放入对应tasks.<task_id>，声明plan_input_selection。
共享数值语法、TDD、独立验证和宿主采证责任不可因分区遗漏；完整全局矩阵供Candidate审阅。
可用task_input_policy.candidate_paths绑定完整验收正文，避免Candidate重复读取实现步骤；
Critic须从本PRD反向审查所有共享规则、异常、来源与命令，缺少正文不得批准。
各plan_ports使用ref/sha256交付一次完整工件，不在多个结果正文重复内嵌全部矩阵。
配置8 impl、8 verify、最多16 active pipelines，maxUnverifiedTasks=16，
maxIntegrationQueue=16；集成仍单写者CAS。Plan Preview审阅批准后才实施。
业务成功不自动证明满载；必须从真实operation生命周期单独统计两个阶段峰值及重叠。
不添加sleep、假任务、假终态或直接改运行状态来凑并发。

"""
    for key, title, contract, _ in FEATURES:
        prd += f"- {key}（{title}）：{contract} 范围仅 app/src/{key}.ts、app/tests/{key}.test.mjs。\n"
    html = '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>十六工具</title><style>body{max-width:760px;margin:auto;padding:16px;font-family:system-ui}section{padding:10px 0}textarea{max-width:100%;box-sizing:border-box}output{display:block;white-space:pre-wrap;overflow-wrap:anywhere}</style><h1>十六工具</h1>'
    scripts = []
    for key, title, _, _ in FEATURES:
        html += f'<section><label>{title}<textarea id="{key}-input"></textarea></label><button id="{key}-run">运行{title}</button><output id="{key}-output"></output><div role="alert" id="{key}-error"></div></section>'
        scripts.append(f"import {{run as {key}}} from './dist/{key}.js';document.querySelector('#{key}-run').onclick=()=>{{document.querySelector('#{key}-error').textContent='';try{{document.querySelector('#{key}-output').textContent=JSON.stringify({key}(document.querySelector('#{key}-input').value))}}catch(e){{document.querySelector('#{key}-error').textContent=e.message}}}};")
    html += '<script type="module">' + '\n'.join(scripts) + '</script></html>'
    browser = BROWSER.replace("__CASES__", json.dumps([[f[0], f[3]] for f in FEATURES], ensure_ascii=False))
    files = {
        "docs/prd/TODO.md": prd,
        "tests/browser.cjs": browser,
        "AGENTS.md": "# 隔离8×8验收项目\n遵循docs/prd/TODO.md。只写当前Task分配的模块及测试；先RED后GREEN。\n仅允许按briefing原地更新当前operation result scratch；不得直接改事件/Store或伪造passed。\n保持workspace-write沙箱，浏览器由预授权Docker采证。\n",
        ".gitignore": ".zf/\n.codex/\n.claude/\nnode_modules/\napp/dist/\nartifacts/\ntmux/\nevidence/\nworkspace-registry/\n",
        "package.json": json.dumps({"name": "sixteen-tools-qualification", "private": True, "scripts": {"build": "node_modules/.bin/tsc -p app/tsconfig.json", "test": "npm run build && node --test app/tests/*.test.mjs"}}),
        "app/tsconfig.json": json.dumps({"compilerOptions": {"target": "ES2022", "module": "ES2022", "strict": True, "outDir": "dist", "rootDir": "src"}, "include": ["src/**/*.ts"]}),
        "app/package.json": '{"type":"module"}\n',
        "app/index.html": html,
    }
    owned = []
    for key, _, _, _ in FEATURES:
        module, test = f"app/src/{key}.ts", f"app/tests/{key}.test.mjs"
        owned.extend([module, test])
        files[module] = 'export function run(value: string): unknown { throw new Error("TODO"); }\n'
        files[test] = f"import {{test}} from 'node:test'; import assert from 'node:assert/strict'; test('scaffold import',async()=>assert.equal(typeof (await import('../dist/{key}.js')).run,'function'));\n"
    for name, body in files.items():
        (root / name).write_text(body)
    docs = configuration(root, harness=harness)
    flow, cfg = docs[0]["spec"], docs[1]["spec"]
    # An unattended expiry must retain Run Manager-owned redrive, not shadow failure.
    cfg["workflow"].setdefault("task_attempt", {})["mode"] = "enforce"
    flow["lanes"] = 8
    pipeline = flow["taskPipeline"]
    pipeline["maxActiveTaskPipelines"] = 16
    pipeline["backpressure"].update(maxUnverifiedTasks=16, maxIntegrationQueue=16)
    for stage, prefix in (("impl", "dev"), ("verify", "verify")):
        pipeline["pools"][stage].update(capacity=8, roleInstances=[f"{prefix}-lane-{i}" for i in range(8)])
    for role in cfg["roles"]:
        if role.get("instance_id") in {"planner", "plan-critic"}:
            role["model"] = "gpt-6-astra"
    cfg["runtime"]["run_manager"]["resident_agent"] = {"enabled": True, "model": "gpt-6.1-terra", "model_reasoning_effort": "high"}
    cfg["runtime"]["autoresearch_resident"].update(enabled=True, worktree_root=str(root / '.zf/repair-worktrees'), on_parent_terminal="continue")
    approval = "owner:eight-by-eight-20261118"
    cfg["runtime"]["host_verification"] = {
        "enabled": True, "approval_ref": approval, "image": image, "network": "host",
        "protected_files": {"tests/browser.cjs": hashlib.sha256(browser.encode()).hexdigest()},
        "commands": ["npm test", "npm run build", "npm run build && node tests/browser.cjs",
                     *[f"npm run build && node --test app/tests/{key}.test.mjs" for key, *_ in FEATURES]],
        "max_executions_per_run": 100, "timeout_seconds": 180,
    }
    cfg["runtime"]["task_publication"] = {"enabled": True, "approval_ref": approval, "paths": owned, "max_publications_per_run": 32}
    cfg["runtime"]["git"]["auto_ship_on_judge_passed"] = True
    (root / "zf.yaml").write_text(yaml.safe_dump_all(docs, sort_keys=False))
    return {"root": str(root), "feature_count": len(FEATURES), "owned_paths": owned,
            "prd_sha256": hashlib.sha256(prd.encode()).hexdigest(),
            "browser_sha256": hashlib.sha256(browser.encode()).hexdigest(),
            "seed_files": [*files, "zf.yaml"]}


BROWSER = r'''const {chromium}=require('/app/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),http=require('node:http'),assert=require('node:assert/strict');
const cases=__CASES__,out=process.env.ZF_EVIDENCE_DIR,root=path.resolve('app');
if(!out)throw new Error('ZF_EVIDENCE_DIR required');
const server=http.createServer((req,res)=>{const rel=new URL(req.url,'http://localhost').pathname;
const file=path.resolve(root,'.'+(rel==='/'?'/index.html':rel));if(!file.startsWith(root+'/')){res.writeHead(403);res.end();return;}
try{res.setHeader('Content-Type',file.endsWith('.html')?'text/html; charset=utf-8':'text/javascript');res.end(fs.readFileSync(file));}catch{res.writeHead(404);res.end();}});
(async()=>{await new Promise(r=>server.listen(0,'0.0.0.0',r));
const browser=await chromium.launch({channel:'chromium',args:['--no-sandbox']});
try{fs.writeFileSync(path.join(out,'environment.json'),JSON.stringify({browser:browser.version(),node:process.version,source_commit:process.env.ZF_CANDIDATE_COMMIT,image:process.env.ZF_BROWSER_IMAGE_ID}));
for(const [name,width]of [['desktop',1280],['mobile',390]]){const ctx=await browser.newContext({viewport:{width,height:800},recordVideo:{dir:out}});await ctx.tracing.start({screenshots:true,snapshots:true,sources:true});
const page=await ctx.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));const video=page.video();
try{await page.goto('http://127.0.0.1:'+server.address().port+'/');
for(const [key,samples]of cases){for(const [input,expected]of samples){await page.locator('#'+key+'-input').fill(input);await page.locator('#'+key+'-run').focus();await page.keyboard.press('Enter');
assert.equal(await page.locator('#'+key+'-error').textContent(),'');assert.equal(await page.locator('#'+key+'-output').textContent(),JSON.stringify(expected));}
if(cases.findIndex(x=>x[0]===key)>=8){for(const bad of ['1,,2','NaN','Infinity','0x10','2x']){await page.locator('#'+key+'-input').fill(bad);await page.locator('#'+key+'-run').click();assert.ok((await page.locator('#'+key+'-error').textContent()).length>0);}}}
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);assert.deepEqual(errors,[]);await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});console.log(name+': sixteen contracts, invalid inputs, keyboard and layout passed');
}finally{await ctx.tracing.stop({path:path.join(out,name+'-trace.zip')});await ctx.close();await video.saveAs(path.join(out,name+'.webm'));}}
}finally{await browser.close();server.close();}})().catch(e=>{console.error(e);server.close();process.exitCode=1;});
'''
