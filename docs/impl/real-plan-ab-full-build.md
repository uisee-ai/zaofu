# 真实规划对照与完整产品构建记录

本记录承接 [Plan 执行一致性改进](plan-execution-readiness.md)。
证据目录：`/home/min/workspace/public/zaofu-evidence/20261021-plan-ab`。

## 评估设计

- 基线 `5191c9e6`；候选 `eafd941d`（包含 `ab01d9db`）。同一模型 `gpt-6-astra`、同一 prompt、完整来源与既有计划、相同运行配置。候选额外接收真实 CLI 生成的 execution-review。
- 这是 Plan Critic 真实 provider 对照，不是两套完整产品的端到端 A/B，也不是随机化统计结论。候选运行不包括本轮新增的 skill 部署核对段落。
- 两组独立只读 Docker 根文件系统/输入；不挂载产品工作目录或 Docker socket。镜像固定 digest，完整依赖分别从各 commit 的 `yoke/` 与 `skills/` 导出。超时每组 600 秒。
- 原始输出保留在 `qualified-results/<round>-<arm>/{trace.jsonl,result.json,answer.txt}`；manifest 记录输入/版本/镜像摘要。provider 报告的 input_tokens 含累计及缓存读取，不等于唯一 PRD 长度。
- 初次 pilot 漏了 yoke 依赖，被主动中断，原始证据保留在 `results/`，不计入 A/B 结论。
- 评审关注切片/前置真实性、SDD/TDD可证伪性、实际独立 Verify 时序、冷启动、局部输入及完整产品验收。人工核对实际来源；不以关键词命中或任务数量自动判优。

运行方式：

```bash
python scripts/eval-plan-execution.py \
  --inputs /path/to/inputs --output /new/evidence/path \
  --baseline 5191c9e6 --candidate eafd941d \
  --candidate-facts /path/to/execution-review.json \
  --model gpt-6-astra --docker-image mcr.microsoft.com/playwright:v1.62.0-noble
```

inputs 包含 `prompt.md` 与只读 `project/`。使用前检查输入，认证文件只作为容器运行依赖，不进入证据包。该命令不会批准 Plan。

## 实际部署发现与修正

产品仓库跟踪了旧版同名 skills，优先级高于新 skill source。两份旧技能逐字与基线一致；在独立产品 worktree 更新其完整目录，保留其他项目内容。新 provider codex-home 的 product-scan、tech-scan、planner、plan-critic 已核对候选执行路线引用。旧 dispatch 通过正常停止、重启和 Kernel reader redrive 留存，不改写旧冻结工件。

原 root 是规划目标且排除 `app/**`；新建 TASK-D9818E 全产品目标，允许 app、脚本、产品说明及验收文件，保留全部 149 要求、36 指标及未决 CR/Q/D。新配置仍是默认 v3，不伪称逐任务 Verify；开启自检、取消默认 AC 数量上限、接线冷启动脚本，Plan 人工审批保持开启。

产品配置与目标提交：`23b675c chore: prepare isolated full-product delivery with plan approval`；技能部署提交：`413d874 fix: load candidate planning methods in the product worktree`。只在产品独立分支提交，未修改原 cargo 与 8001。

产品初始化时 intake 已先创建状态目录，`zf init --force` 会截断任务/账本，未采用。使用 sanctioned SessionStore/EventWriter 完成缺失 session 初始化并注册 Workspace；保留现有提交与事件。首次 TUI 启动出现 account/read routing timeout，保留失败/重派发事实，不算产品失败。

## 验证与未完成项

- 评估脚本与 review package 定向测试 25 passed；加入技能分发检查后 30 passed。
- 自动 dev-verify 42 passed / 1 failed：public checkout 缺 `.gitlab-ci.yml`。在原 dev 上单独复现同一失败；未伪造 CI 文件或削弱测试。
- 产品配置 cold-start validation 通过（有既有事件/来源碰撞 warnings）；flow submit accepted，正式 workflow 已启动。
- app 尚未构建，未声明真实产品、浏览器或全量验收通过。外部输入与 Plan 审批仍按原要求保留。

## 实际 A/B 结果（单对运行，不作统计显著性推断）

| 指标 | 基线 | 候选 |
| --- | ---: | ---: |
| 正常完成 / 输入变更 | 是 / 0 | 是 / 0 |
| 秒 | 318.825 | 457.472 |
| 累计 input tokens | 2,656,196 | 3,072,183 |
| 其中 cached input tokens | 2,485,504 | 2,854,272 |
| 非缓存 input tokens | 170,692 | 217,911 |
| output tokens | 7,425 | 6,746 |
| 最终 verdict | rework | rework |

候选耗时 +43.49%，累计输入 +15.66%，非缓存输入 +27.66%。没有证明效率提升。
这些是 provider 回传的使用量；缓存、采样和串行顺序会影响结果，没有价格或统计显著性结论。

执行一致性方面，基线建议 ADL-01 后独立 Verify、逐片验证再放行依赖，但没有说明
当前 v3 路线所需变更。候选明确识别 task_pipeline=off、candidate.ready 后 Verify、
自检未强制及 setup 未接线；提出阶段批次或经验证后显式适配 Task Pipeline。
候选也明确区分当前配置事实与冻结在途配置，不把字节数当模型 token。
这支持“本案例的执行时序建议更准确”，不支持“任何 Plan 或最终产品更好”。

语义检查存在互补而非全面占优：两者都指出字段数量驱动切片、过长依赖与泛化TDD。
基线额外指出 AC-AT18 浏览器证据未绑定、AC-R040 局部动作与全集断言冲突；候选强调
Q14 决定责任字段不足和局部输入分区。候选未重复全部基线发现，不能宣称审查质量
全面提高。这些发现是修订输入，需 Planner/独立 Critic 复核，不能由本报告批准产品。

初次无效 pilot、两个完整原始轨迹以及 manifest 均保留。没有筛除不利结果或用合成
测试替代此次真实模型证据。完整产品仍按任务 C/D 单独验收。

## 真实交接回归：结果补正与旧结束信号

真实事件链：`evt-66db7a5ffe20` 技术扫描成功报告 → `evt-8c27852c15fe`
要求补齐 input_consumption 读取凭证 → `evt-89643b723311` 原 provider turn 结束 →
`evt-a17f0d192916` 被判 terminal missing/recovery exhausted。
扫描报告的产品缺口没有被本修复降低严重性；缺读取凭证仍不能准入。

修正后，成功向 transport 发送补正才追加
`workflow.call.result.repair.dispatched`（`call-result-repair-dispatch.v1`）。
正文绑定 workflow、operation/request hash、dispatch、role、原结果事件和 correction ref。
Reader 与 synth 的丢失检测只在四项身份完整匹配时，把本次补正派发位置作为新的
信号起点；更早的 turn close 不再误杀补正，更晚的 close/transport loss 仍按原重试
额度恢复。该事件不表示结果已准入，不替代 read ledger，不改变业务 verdict。

补正等待支持同 role 的 durable provider.turn.closed（backend 与 turn id 具备）；
每次轮询重新从原结果事件之后寻找停止信号，避免第二次轮询误用更早的 stop。
等待超时不发送补正、不记派发成功。无新增语义状态机或 cargo 判断。

- 原实现回归 RED：1 failed / 4 passed，确实发生旧 close 导致额外派发。
- 补正等待 RED：2 failed / 9 passed，分别复现不识别 durable close、第二轮误用旧 stop。
- 修正后的直接 caller / reader fanout / Plan synth / 准入 / Event 合同及 4 个 mock E2E：262 passed。
- 加入发送失败与等待超时断言，相关新测试和评估/技能检查：23 passed。
- 最终增加真实 synth caller 路径后，补正竞争回归文件独立运行：13 passed。
- sentinel：51 passed，manual docs currentness 通过。实际全产品验收仍不由这些测试代替。

实际产品流程通过第二轮扫描后进入 Planner。该轮规划期间 Python 内存仍是前次重启时
加载的版本，本条新增修复在安全重启前不记为已部署；不能把在线后续进展误归因于
尚未加载的补丁。

自动层级补充：`dev-verify run --base eafd941d --skip-tier deterministic` 的 serial 10、sentinel 51、flow-smoke 10 全部通过。deterministic 以本节记录的直接影响闭包替代；未执行全量 pytest。完整选择器给出 340 个候选文件且无 unmapped 错误。

## 真实 Plan 的重复正文回归

Critic briefing 已通过 Source Manifest 绑定完整报告，但随后渲染 admitted envelope 时
又将 hydrated `control_result` 全文内嵌，重新引入了大包。补充真实准入路径的回归后
出现有效 RED（1 failed / 1 passed，150KB 哨兵正文意外出现在 briefing）。
仅当已有 call payload 且 control-result ref/digest 完整时改为渲染 ref；完整 sidecar
仍在 Source Manifest，原 child result 必读规则不变，legacy 路径继续内嵌正文。

- 直接 reader/synth、Source Manifest 和 CLI/capability 读取闭包：190 passed。
- 补充 legacy/准入四组合、artifact relocation 及 mock E2E：11 passed。
- sentinel：51 passed；源码仅改 briefing 渲染，不改准入、结果语义或运行状态。
- `replay-briefing.py` 在临时目录回放真实 `evt-2937bd6f83d8` 的工件，比较
  `9a765e31` 与本补丁：1,261,498 → 9,507 bytes（-99.25%）。全部来源摘要及
  必读清单保持一致；发出 simulation.done 并清理临时目录。证据在 `briefing-replay/`。
- 该字节结果不是 token 或质量结果；新增真实模型对照在 `briefing-ab/` 单独记录。
  两组使用相同完整来源、相同技能、相同模型/隔离环境，仅 briefing.md 不同；
  与首次实验相反，先 candidate 后 baseline。该实验不执行 workflow 提交或产品构建。

首轮完整产品规划形成 22 任务、273 AC、49 命令草稿。冻结配置预检通过，但独立 Critic
在 `evt-ac68873cee41` Reject：仲裁职责、前置片依赖后继实现、确定性测试与真实资格
混绑、装配自检依赖未来 Verify 四项。Kernel 已自动派发同一 Planner 修订。
当时 C/D 尚未完成，不把结构预检或一次真实模型运行视作完整交付。

第二组真实对照完成（`briefing-ab/manifest.json`、`evaluation.json`）：

| 指标 | 旧渲染 9a765e31 | 新渲染 bc1593b1 |
| --- | ---: | ---: |
| 正常完成 / 输入变更 | 是 / 0 | 是 / 0 |
| 秒 | 155.949 | 180.375 |
| 累计 input tokens | 818,084 | 1,202,999 |
| cached input tokens | 711,552 | 1,088,768 |
| 非缓存 input tokens | 106,532 | 114,231 |
| output tokens | 4,575 | 5,183 |
| verdict | rework | rework |

对照输入逐文件核对，唯一差异为 `briefing.md`。新版耗时 +15.66%、累计输入 +47.05%、
非缓存输入 +7.23%，同样不能证明端到端效率提升。两组都发现原 Critic 的四类问题；
旧版另指出 crop/embedding 的动作生产者缺口，新版更具体指出 RUNS 逐算子恢复的
前置依赖，仍是互补覆盖。单对采样、顺序和缓存存在影响；不声称全面质量占优。
本修复的已证实收益限于消除重复大包及保持来源完整性，不能据此宣称模型降本，
也没有用追加实验覆盖或隐藏第一组不利结果。

## 当前可审批 Plan 与读取一致性

第二轮 Planner 修订后，Critic 在 `evt-88d5d35b562f` 正式 Approve，确认 PC-01–04
关闭且未发现新增阻断；保留 22 任务、273 mandatory、149 要求/36 指标、69 具名用例、
54 片内步骤，以及全部 CR/Q/D/外发未决。Plan Package 已准入，
`evt-477684e8999c` 为 task_map.ready；`evt-2b4626167dbf` 请求人工 Plan 批准。
预览 API 返回 full_plan，22 任务和完整工件可读取，无正文/工件错误。此时没有产品
writer 派发，也没有 app build/产品 Verify/浏览器验收通过收据。

检查 `/plan/pending` 发现它用 project_root 解释 state-relative ref，与预览/CLI 不一致，
错误给出不可读/reject 建议；旧路径还未阻止项目外绝对引用。改用已有
`preview_artifact_path`，路径不合法仍给出非绑定的 reject 建议，不影响人工裁决。
有效 RED：2 failed / 1 passed（自定义 state、越界）；GREEN：29 项 API、CLI、Inbox、
controlled action 定向测试通过。此修复不批准 Plan、不更改事件账本。

人工审批保持用户此前指定的 approve 模式。完整产品仍需该批准，并在对应阶段取得
真实数据、GT、训练脚本、设备/仿真与相关业务决定；不能用规划通过结清任务 D。

## 候选部署收据

在全部 worker 空闲、Plan 待批时，仅对新产品执行 `zf stop --fast` 后重启。
2026-10-21 04:08 UTC 起运行候选 `3e665b54`，保留原账本与 Plan；Impl 派发数仍为 0。
原 8001/dev `5191c9e6` 和原 cargo 会话没有重启。8002 已被其他进程使用，候选
dashboard 使用 8003 和既有 access-token 文件，不打印或复制凭证正文。
`web/` 相对 dev 无变化，仅复用已有 dist；这不是产品 app 的 build 证据。

新服务 `/health` 回报 `3e665b54`；真实 `/plan/pending` 返回 22 任务、零 checklist
警告，预览为 full_plan 且正文与工件错误均为空。
[打开本轮 Plan Preview](http://10.9.100.143:8003/?project=full-build-20261021-c2f1cf22&page=inbox&plan=evt-477684e8999c)。
运行与证据目录继续保留以等待人工审批，非临时模拟；两组 A/B 容器及隔离回放目录
已清理。收据为 `deployment-receipt.json` 与 `plan-preview-pending.json`。

## 继续验证：完整来源读取方法

用户要求验证更好后再暂停。前两组结果仍保留为负向效率结果，不能通过多次抽样挑选赢家。
本轮在 `eda8eb34` 上仅增加通用来源导航/去重/分块方法，保留全部原始来源、必读渠道、
逐任务/未映射条款审查；三份 skill 同步，实际角色依赖物化包含新 reference。

评估预注册：证据根 `reading-eval/registration.json`，固定 cargo 三轮配对与库存业务
有缺陷/合法两例。主要成功标准为 cargo 非缓存输入配对比中位数 ≤0.85、耗时 ≤1.10，
每轮候选识别四类历史 Critic 缺陷，无严重误报/假通过，跨业务核心原因与合法对照判断正确。
基线独有严重有效发现需要复核，不能只以四类召回宣称质量相等。超时/错误不替换；
下一候选另立批次。库存是人工构造、真实 provider 执行的迁移探针，不是另一个完整产品 build。
质量 rubric 不挂载给模型；原始轨迹、全部 usage 和来源摘要保留。三轮不足以证明统计显著性。
该批只验证 Critic 读取方法的增量效果，不代表相对原 dev 的整个产品生命周期改善。

Plan 人工审批仍待处理，当前实验不会改变已冻结产品 Plan 或代替 Impl/Verify/build 验收。

### 读取方法试验的负结果与撤销

冻结候选 `42fb2032` 对基线 `eda8eb34` 的三轮 cargo 已完成。配对非缓存输入比值
中位数 1.4071，耗时比值中位数 1.5030，均未通过事先标准；第三轮候选漏报装配等待
后置独立 Verify 的问题，四类关键发现仅覆盖3类，基线三轮均覆盖4类。新增 reference
与入口指导已撤销，原有方法/角色/源码保留；冻结提交、原始轨迹及负结果不删除。
跨业务两例完成原定运行；预设合法对照本身缺少浏览器实现者与边界用例，其 pass oracle 无效，不能把合理 rework 计成误报。此缺陷不影响 cargo 三轮成本/关键发现的负结果。

### 验证真实任务范围输入

后续试验改为检验此前已实现的 task-scoped 输入能力，不能把 Critic 全局评审成本
当作 Impl 输入裁剪效果。证据根 `scoped-eval/registration.json` 在调用前冻结：同一
原始工件，执行旧版/新版真实 required-read 选择函数，以原样选取的完整值作为模型
输入；共享与本任务逐值相等，最终 Candidate 仍全量。cargo IMPORT两对、EDIT一对，
仓库 TRANSFER一对；baseline/candidate顺序交替，所有运行串行。

该实验隔离 execution_spec 端口，衡量真实模型对完整用例、TDD命令、关键行为和共享
约束的理解以及真实usage，不是完整worker dispatch或产品build；其他必读端口仍需
原有入口测试与正式产品运行验证。成本门槛：两个cargo任务场景非缓存输入配对比
中位数≤0.70、cargo整体耗时配对比中位数≤1.10；候选所有关键语义和结构必须保留。
小输入仓库用作跨业务质量探针，固定system prompt成本使其不设30%下降门槛，实际
成本仍全部报告。这项区分在任何本批provider调用前明确，修改记录保留。

### 当前待批Plan的补充发现

`pending-plan-followup-findings.json` 绑定 `evt-477684e8999c` 的冻结来源摘要。
`AC-M14-01` 声明LIVE验证但 evidence_contract仍指向IMPORT本片runtime收据，
与LIVE输出目录不一致；需要按命令绑定输出及聚合消费关系。
另有T12-11保留在candidate原文义务中，但具体动作生产者/具名用例仍需独立语义复核。
这两项没有通过更改canonical运行态“修复”，此前Critic批准不替代新发现的处理。

本轮验证层级：新方法依赖/输入测试14 passed；指令/来源/结构闭包45 passed、1 skipped；
撤销后实际角色分发与task-scoped输入9 passed。累计diff的dev-verify选择了465个测试文件
对应的全套确定性测试；该宽泛运行已主动终止，未计为通过。改用本次变更显式paths
执行上述闭包，收据为 reading-focused-verification.json。未重复宣称full pytest或产品E2E。
