# 模块接口与执行规格（随本版 Plan 审批）

## 边界与所有权

浏览器本地单人应用，不引入服务器业务、账号、数据库或Provider。
RACE-FOUNDATION 提供package/lockfile、Vite/TS/ESLint配置、src/contracts/types.ts、runner和启动骨架。
RACE-DRIVE独占输入、时钟、运动与RaceState单一写入者；RACE-TRACK独占世界定义、扫掠碰撞与路线进度查询。
二者只导入src/contracts，禁止互相导入，因此在共享契约完成后可并行。
RACE-ASSEMBLY是唯一正式入口/接线负责人，src/main.ts将真实TrackPort注入RaceController，绑定DOM操作并把快照交给UI；不反向改兄弟目录。
类型变更若影响兄弟必须受控replan或回到原owner，不以新增重复文件规避唯一所有权。

## 数据和函数契约 v1

- InputState={throttle:boolean,brake:boolean,steer:-1|0|1}；normalizeKeys(set):InputState由RACE-DRIVE实现。
- Pose={x:number,y:number,headingRad:number,speedMps:number}；所有数必须有限。无效dt（负数/NaN/Infinity）与无效输入拒绝推进，返回原状态和diagnostic，不损坏终局。
- TrackDefinition={center:{x,y},innerRadiusM,outerRadiusM,centerRadiusM,startPose,checkpoints:[{id,angleRad}],obstacles:[{id,x,y,radiusM}]}；固定PRD坐标/单位，render也读这份数据。
- RouteState={lastAngleRad,unwrappedProgressM,highWaterM,nextCheckpointIndex,completedLaps}。
- MotionQuery={from:Pose,proposed:Pose,dtSeconds,route:RouteState,contactIds:string[]}。
- TrackPort.resolve(query):MotionResolution={pose:Pose,route:RouteState,validDistanceDeltaM:number,contactIds:string[],collisionStarted:boolean,collisionIds:string[],diagnostics:string[]}。纯函数，返回值不修改输入；裁剪实际轨迹后计算距离；collisionStarted遵循episode去重。
- createRace(trackPort,config):RaceController；config.durationMs在生产固定90000，单测构造不改变正式入口。
- RaceController.dispatch(action:'start'|'pause'|'resume'|'restart',nowMs):Readonly<RaceSnapshot>；tick(input,nowMs)只由浏览器单调时钟适配调用。
- RaceSnapshot={phase,vehicle:Pose,elapsedActiveMs,remainingMs,distanceM,collisionCount,score,route,contactIds,result:RaceResult|null,finishCount}。
- RaceResult={distanceM,collisionCount,score,elapsedActiveMs:90000}。
- tick返回{snapshot,events:RaceEvent[]}；RaceEvent为collision.started（ids、count、penalty）或race.finished（结果）。只消费一次事件，不能用事件监听器再次改分数。
- render(snapshot,trackDefinition,view):void只绘制；bindControls(controller):dispose管理键盘/按钮/blur/visibility。dispose释放监听器/rAF，重开不创建重复loop。
- window.__racingSnapshot()仅供只读观察，返回深复制冻结数据；无setter、快进、自动驾驶或缩短赛程入口。所有用户操作经实际键盘与HTML按钮。

数值边界与更新顺序以PRD为准。几何计算误差阈值1e-6m，分离阈值0.01m；同一测试输入应产生相同状态。未知action为无操作并诊断，UI不得暴露不支持操作。
RACE-DRIVE可用其自有开放平面TrackPort夹具做本片演示；RACE-TRACK可用自有车辆代理调用resolve做几何演示。两者不声称最终产品验收通过。
最终入口必须导入真实两片，不得保留fixture路由；构建包含根index.html，demo不是默认首页。

## 工程约定和初始化

package_root=target_root='.'，test_path_prefix='tests/'，ES模块TypeScript + Vite + Canvas2D/HTML；Vitest确定性测试、Playwright Chromium。
具体依赖版本在获批后的FOUNDATION核对Node兼容性并锁入package-lock.json；未声称现在已安装或验证任何版本。
package.json从首片预先提供dev、build、preview、lint、test脚本，后续片不得改它。
build输出artifacts/racing/assembly/build/（归assembly生成输出），preview绑定0.0.0.0并strictPort。
test只选择tests/*/unit.test.ts等明确单元测试模式，不误跑*.spec.ts浏览器文件；片内runner精确选定tests/<suite>/unit.test.ts。
lint只检查存在的源码/测试，不要求未来片文件先存在。首片先验证自己的静态检查与类型合同。
project.scripts.setup目前未配置，不能声称运行时自动安装。FOUNDATION先生成lockfile，后续每个干净工作树由worker在自检前明确执行 env PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --ignore-scripts。
安装是环境初始化，不复制进并行切片validation；安装失败留日志并分类为setup失败。必要的本地构建依赖初始化要逐项审查，仅允许非浏览器工具，禁止随意开启全量生命周期脚本。

## runner、端口及证据契约

scripts/racing-runner/unit.mjs <scaffold|race|track|assembly>：校验suite白名单，原子创建artifacts/racing/<suite>/runs/<UTC+随机id>/，运行本片单测并保留stdout.bin/stderr.bin/result.json/receipt.json；同一命令重跑不覆盖。
scripts/racing-runner/container.sh由FOUNDATION实现：容器内校验环境，timeout 180s ./node_modules/.bin/playwright install chromium，调用scripts/racing-runner/browser.mjs。禁止依赖容器不存在的curl；HTTP探测用Node fetch。
scripts/racing-runner/docker.sh <scaffold|race|track|acceptance> 由FOUNDATION一次性交付全部suite白名单和映射，未知suite非零；不访问其他suite的config/spec，也不因未来配置尚未生成使scaffold失败。race/track在本片实现后即可运行，不要求根playwright.config.ts或正式index.html。
scripts/racing-runner/browser.mjs读取RACING_SUITE：scaffold→tests/scaffold/playwright.config.ts，race→tests/race/playwright.config.ts，track→tests/track/playwright.config.ts，acceptance→playwright.config.ts。每个config只选择本片browser.spec.ts（acceptance选择tests/acceptance/**），并将输出定向本次独立run；不能扫描未来suite。
选择可用端口从8004向上，真正listen成功才算占用，启动失败重选且最多20次；使用strictPort防止Vite静默跳端口；元数据记录实际bind_host、port、service_url和browser_origin。
acceptance服务使用构建后的preview，scaffold/race/track使用dev且直接访问各自HTML入口。所有suite均0.0.0.0，browser_origin/service_url=http://127.0.0.1:<实际端口>；默认8004只为首选不是已预留。
只清理本runner启动的服务/容器；finally和trap保存失败状态后清理，不用成功的清理覆盖子命令exit code。
每个run保存commit、精确command/id、镜像tag+digest、UID/GID、端口、URL、阶段状态、退出码、原始日志、report.json、browser-errors.json、截图和trace。
所有日志/报告产生于新目录；源码只读消费。前置片输出不可由assembly覆盖；assembly只聚合其ref/digest并产生自己的整体验收证据。
浏览器执行前每个worker核对git HEAD与该次candidate绑定目标；本Plan target_commit是初始实现基线，后续验证应绑定组装后的候选commit，不能继续验证初始空仓库。
无需WebCrypto或LAN origin；验收声明仅为本机127.0.0.1 origin，不宣称其他网络地址已通过。

## 人工流程证据

scripts/audit-racing-workflow.mjs读取编排方通过RACING_WORKFLOW_EVIDENCE提供的只读证据bundle路径；不写Kernel。
bundle须包含当前run/plan/package摘要、受控配置/加载证明、YAML Preview/Workflow启动授权引用、plan.approved原始事件以及实际writer fanout.child.dispatched事件。
核对Plan批准为人工且auto不为true，身份/版本/digest一致，批准时间先于该Plan全部writer派发；原始类型已对照runtime/orchestrator_fanout.py确认。
空查询、缺配置、旧Plan批准、仅writer完成记录均不算证明。缺bundle非0并明确环境/编排证据阻塞，不能伪造。
本命令在candidate阶段只验证已发生前置；不消费自身未来verify/judge结果。
独立验证lane及Judge/Kernel完成由各后续阶段生成自己的原始报告和事件引用，Kernel核查后方可交付；不会把“配置里有角色”当成实际验证发生。


## 第二波浏览器入口与suite闭包（PC-01修订）

| suite / command | 入口 | 独占config/spec/证据owner | 最早执行 |
| --- | --- | --- | --- |
| race / VC-DRIVE-BROWSER | /src/race/demo.html | RACE-DRIVE：tests/race/playwright.config.ts、tests/race/browser.spec.ts、artifacts/racing/race/runs/ | FOUNDATION + DRIVE 后，波次2 writer自检 |
| track / VC-TRACK-BROWSER | /src/track/demo.html | RACE-TRACK：tests/track/playwright.config.ts、tests/track/browser.spec.ts、artifacts/racing/track/runs/ | FOUNDATION + TRACK 后，波次2 writer自检 |

FOUNDATION独占四个共享runner文件；两片只读调用，无须修改共享runner。其单元测试覆盖四个suite映射、未知suite拒绝、当前suite缺config非零、未选择的未来suite缺文件不报错。
DRIVE演示显示开放世界夹具标签；使用真实页面按钮和两组按键经RaceController更新画面，比赛duration仍90000，局部smoke无需等待终局。
TRACK演示代理用真实页面“相对路程”输入框与“前进/后退”“外移/内移”“重置代理”“障碍路线”控件产生MotionQuery并调用真实TrackPort；前进/后退请求有限路程0..20m，按≤1/120秒及≤0.25m步进播放，最后一步裁到目标，不直接写resolve结果。外移/内移为径向运动。重置按钮只重置演示，不暴露正式产品状态setter。0→10→5→10→12的测试用fill/click请求+10/−5/+5/+2，等待只读代理状态显示播放完成，允许1e-6几何误差并报告每段增量；精确数值夹具另由VC-TRACK确定性验证。
两片均在1280×800记录Canvas非空、车辆前后帧差、DOM/位姿/输入反馈，捕获console.error/pageerror且断言为空；完整CASE-DRIVE-DEMO/CASE-TRACK-DEMO通过execution_spec和test_matrix交接。
局部smoke不声称完成最终驾驶赛道组合，正式入口四条mandatory旅程及90秒完整比赛无排除项。

docker.sh内部唯一Docker调用形状如下；只有经白名单验证的RACING_SUITE传入，所有suite保留相同边界：
```bash
docker run --rm --pull never --network host --user "$(id -u):$(id -g)" --entrypoint bash -v "$PWD:/work" -w /work -e PLAYWRIGHT_BROWSERS_PATH=0 -e RACING_SUITE="$RACING_SUITE" mcp/playwright:latest -lc 'bash scripts/racing-runner/container.sh'
```
该共享脚本是明确生产者和复用协议，不改变Docker边界；command registry执行的是bash scripts/racing-runner/docker.sh <suite>，内部命令记录在receipt。容器安装必须timeout 180s；任何失败保留日志后非零；不运行宿主浏览器。

## 防刷分独立夹具（PC-02修订）

使用PRD真实TrackDefinition。L=2π×180，P(s,r)=(300+r·sin(s/180),300−r·cos(s/180))；lastAngleRad=atan2(y−300,x−300)。合法路线单步|Δs|≤0.25m、dt=1/120秒、speedMps=45，故r=200时弦长<45dt；该半径避开三个半径8m障碍且在143..217内。每个场景新建contactIds=[]、completedLaps=0和独立累加器；不依赖其他场景输出。
CASE-AC-T03的子场景SC-T03-FIRST/RETURN/LAP/MISSING-CP/BOUNDARY/SPEED-JUMP/CORRECTION/STATIONARY具有各自完整初态；不得把highWater=10套入首次前进。非法检查点历史作为明确负例，未确认的CP不能由新位置猜补；位置纠正无真实运动便不能奖励。
原始highWater公式、碰撞扣分、正式游戏时长均保持不变。

## 收据归属

同一AC关联的runtime与e2e命令分别保存自己的运行目录。单元收据不能冒充浏览器截图；最终VC-UNIT/VC-BROWSER/VC-DELIVERY的输出归assembly，不覆盖race/track原始证据。
候选执行器对直接安装/lint/build命令逐条捕获原始stdout/stderr与exit，记录到自己的assembly运行目录；统一verify-racing.sh对子阶段使用相同采集规则。每条receipt记录实际command_id、command、commit和产物哈希。
