# Kanban → Judge 真实链路鲁棒性与需求保真验证 Prompt

你是 ZaoFu 的监工与独立验收 Agent。基于当前 dev 的固定提交，在独立 worktree 和临时项目执行下列测试。用户已授权真实 Provider、浏览器提交、测试用审批和受控恢复。持续执行到获得可复现结论；发现缺陷时先保留失败证据，再做最小修复和复测。不得将部分通过写成整体验证完成。

## 目标与边界

1. 验证 Kanban Agent → Task → Workflow Preview → Approve → Plan Preview/Approve → Impl → Task/Candidate Verify → Discovery → Judge → Kernel 交付的真实闭环。
2. 逐条验证需求在每次交接中是否遗漏、弱化、冲突或缺少验证；定位首次损失的阶段。
3. 验证 Ask User Question、YAML Agent 局部修改、Plan 引用 Agent 修改、审批版本约束、刷新与恢复。
4. 状态由 EventWriter、canonical stores 和受控动作管理。旁路审计只读，不能写 events.jsonl、kanban.json 或伪造结果。
5. 不将输出 digest、CLI 读取、Provider transcript 可见或 Agent 自述理解混为一谈。unknown 保留为证据不足。
6. 使用 Docker mcp/playwright:latest、host networking；API/UI 监听 0.0.0.0:8002+。不影响真实 8001，不安装宿主浏览器。
7. 固定代码 SHA、Provider/model、实际 YAML、项目/Run/attempt/session 身份；运行时数据位于 /tmp/zf-<purpose>-<UTC>/，尊重 project.state_dir。
8. 执行期限按 V5 配置为 0；检查 600 秒无活动窗口和既有 grace，不把累计运行时长误判为 idle。费用和其他安全门禁不绕过。

## 小任务输入

通过真实 Kanban Agent 输入以下需求，不预先在产品仓库实现代码：

实现最小 Python 命令行程序 hello.py，仅使用标准库；只交付 hello.py 与 tests/test_hello.py。
- R01：python hello.py 无参数时，stdout 严格为 "Hello, world!\n"，stderr 为空，退出码 0。
- R02：python hello.py --name ZaoFu 时，stdout 严格为 "Hello, ZaoFu!\n"，stderr 为空，退出码 0。
- R03：--name 张三 时，stdout 严格为 "Hello, 张三!\n"，stderr 为空，退出码 0。
- R04：姓名先去除首尾空白。全空白姓名如何处理尚未决定，请用 Ask User Question 向我确认后再定稿。
- R05：错误信息只写 stderr；失败时 stdout 必须为空，不能偷偷回退为 world。
- R06：不引入第三方依赖，不访问网络，不增加 Web 服务或其他产品功能。
- R07：提供并实际执行 python -m unittest discover -s tests -v；覆盖无参数、英文、中文、首尾空白和全空白错误路径。禁止零测试通过。
选择已注册的 PRD V5 workflow；Workflow YAML 和 Plan Preview 都需要人工确认后才能进入相应受保护阶段。

对 R04 的统一回答：全空白姓名必须拒绝，退出码 2，stderr 精确为 "name must not be blank\n"，stdout 为空。
R04 的正常修剪案例：--name "  ZaoFu  " 必须与 R02 输出一致。
将原始输入及澄清回答原样留存，预先生成独立验收预期；产品自测不是唯一 oracle。

## 审计模型

每条需求标记完整、遗漏、弱化、冲突、未验证或不适用；每个结论绑定原文位置、阶段输入/输出 ref、digest、Run/generation/attempt、证据位置。

分别检查：
- 来源完整：原始用户消息、澄清回答、任务合同、批准快照的来源与版本。
- 交付完整：必读声明、所有分页、游标结束、范围、收据、重试后的当前 attempt。
- Provider 可观察：绑定正确 session/call/time 的完整工具输出；截断、argument echo 和旧输出不能充当见证。
- 语义保真：逐条核对阶段产物与独立产品行为；ID/关键词命中不是语义覆盖。

Task/Preview 中允许通过正确引用承载完整需求，YAML 不必抄写全文。Impl 按任务分区接收其责任及必要的全局约束，Discovery/Judge 必须形成全局覆盖。合法压缩、任务分区和用户批准的变更不算损失。

## 执行 A：干净基线

1. 检查固定代码、构建、Docker、Provider、独立 workspace/token/tmux；保存证据根目录和启动身份。
2. Playwright 新建项目，输入上述需求；回答真实 Ask User Question，核对答案进入后续来源。
3. 由 UI 创建 Task，打开完整 YAML Preview；核对 PRD V5、lane/model/skills、Plan 人工审批、执行期限及项目边界。
4. 批准前确认受保护运行没有启动。通过 UI 批准准确 revision，再确认实际绑定 YAML digest 一致。
5. 等待 Plan Preview；批准前确认 Impl 未启动。阅读完整 PRD/验收/task map，建立 R01–R07 覆盖矩阵，再通过 UI 批准。
6. 观察 Impl、Task Verify、候选 Verify、Discovery、Judge；按实际 operation 时间和身份记录活动，不以请求设置或绿色节点代替运行证据。
7. 验证 ship.completed、run.delivery.settled、run.goal.completed 的当前 Run 绑定。独立导出被冻结候选和最终交付提交，执行 oracle 与实际产品测试。
8. 汇总首次需求损失、输入见证 unknown、失败/重试、用量和阶段耗时。单任务不宣称证明多个 Impl lane 并行。

## 执行 B：修订与恢复

1. 新建独立项目，使用同一需求与澄清答案。
2. 在 YAML Preview 通过 Agent 局部修改：将 lane 8 改为 2；对一个允许字段验证 model/skills 修改，使用现有有效模型/技能，禁止虚构可用性。逐次检查只改变批准字段及必需的编译派生内容。
3. 保存旧 revision；修改后测试旧审批明确拒绝且没有启动，然后批准新版本。重复同一个动作应幂等，不重复 Run/派发。
4. 在 Plan Preview 选择 R01 对应正文，通过引用 Agent 将默认问候改为 "Hello, driver!\n"；R02–R07 保持。检查全文阅读、引用、输入、局部修改和新 revision。记录该批准变更为新 oracle，不把旧要求当成当前标准。
5. 刷新页面后核对状态；在待人工批准的安全检查点受控停止/重启本测试 runtime，确认没有跳过审批、重复任务或丢失来源。若覆盖活动 Provider 中断，另行记录 attempt/session 恢复结果，不把普通 Web 重启冒充 Provider 恢复。
6. 完成新 Plan 批准及后续交付，独立验证 R01 新值和所有未修改要求。

## 故障验证层

先执行相关确定性测试，再选择可安全复现的真实案例：
- 旧审批、重复提交、跨项目/Run/attempt、错误 digest、缺页/截断输入必须拒绝或保持 unknown。
- 测试失败/证据缺失不能获得完成裁决；失败应进入受控返工/阻塞。
- busy 超过 600 秒仍继续；idle 按阈值和 grace 探测/恢复。需分别报告模拟时间证明与真实时间观察。
- 恢复重新绑定当前输入，旧 attempt 收据/结果不能复用冒充新执行。
不通过直接修改运行时 canonical 文件制造真实案例；fixture 中的故障和真实运行分开标注。

## 验收与收尾

- 关键需求未授权遗漏/弱化/冲突为 0；每项有可复现验证证据。
- 关键交接 scope/revision/digest 一致；有 unknown 的关键交付不能宣称已证明无损。
- 两轮各自有终态、浏览器断言和独立产品验收；失败如实列出，修复前后证据都保留。
- 输出需求×阶段矩阵、链路/故障测试表、首个损失点与根因、修复与回归结果、未覆盖项。
- 报告保存 docs/records/，引用本 Prompt；大证据留在私有临时 evidence 目录，不提交凭据或 Provider 私有正文。
- 结束通过 CLI 发 simulation.done，仅清理本次 Web/runtime/tmux/容器；dirty worker 先记录并归档，保留必要 Git bundle、日志、trace 和截图。源码 worktree 保留供 review。
- 不能因一次或两次通过宣称总体可靠率；不得把预算、超时或上下文不足作为虚报完成的理由。
