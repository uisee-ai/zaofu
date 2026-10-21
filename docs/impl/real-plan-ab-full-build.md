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
