# Channel Group 角色、完整输出与可靠接力

> 状态：implemented — 本批次实现与验证见 [实施记录](../impl/channel-group-181-lossless-relay.md)。
> 日期：2026-10-19。
> 范围：Channel Group / multi-lens 的角色、Skills、provider 输出、上下文与 handoff。
> 实现位于独立 worktree；此状态不表示生产服务、历史成员绑定已迁移。

## 1. 目标与决策

Channel Group 应让产品经理、架构师、Critic、UI/UX、后端等成员按各自职责
充分回答，同时保持现有 Markdown 展示和结构化协作格式。多 agent 接力时，
原始要求、公开回答、未决问题和异议应可追溯，不因摘要、排队或恢复而静默丢失。

采用以下决策：

1. 角色定义负责身份、专业职责和工作原则；专业 Skills 按需使用。
2. 移除强制短回答的通用模板，同时调整系统提示和响应提示中的对应限制。
3. 保留现有机器合同、受控交接入口和权限边界；不直接删除整个讨论 Skill。
4. 完整正文与摘要分别承担职责：正文是可回读来源，摘要只用于导航和卡片展示。
5. 在现有 EventWriter、sidecar、reply request、provider run 和问题账本上补齐
   可靠交付，不建立平行的 Channel 状态机或第二套 Task contract。
6. 输出、上下文、排队和恢复分别验收，不能以“模型返回成功”代替整个链路成功。

“像 Codex CLI 原生输出”在本文中指保留 provider 的公开回答内容、段落、代码、
表格和换行，允许按任务需要充分展开；不承诺不同提示下与 CLI 逐字生成相同答案，
也不包括私有推理或突破既有权限、脱敏约束。

## 2. 现状证据与边界

以下为实施前证据，来自源码审查及三个无文件写入的内存复现，不是新的真实 provider
或 Playwright E2E 结果。可用 `git log --follow -- <path>` 追溯相关实现。

| 发现 | 代码证据 | 影响与限定 |
| --- | --- | --- |
| 短回答限制存在于多层 | [participant Skill](../../skills/zf-channel-discussion-participant/SKILL.md)、[system prompt](../../src/zf/runtime/channel_adapter.py)、[response contract](../../src/zf/runtime/channel_reply_prompt.py) | Skill 要求 3–5 句摘要，另外两层仍要求 concise；只移除 Skill 不足以解除限制。 |
| 最终结果使用最后一个完成的 agentMessage | [Codex RPC client](../../src/zf/web/headless_agent.py) 的 `_handle_notification` / `wait_turn` | 两个公开回答 item 的复现中，第一段不在最终返回值中；不表示所有实际回复均已截断。 |
| 普通历史上下文使用有限摘录 | [context pack](../../src/zf/runtime/channel_context.py) 的 `_select_messages` | 单条摘录最多 800 字符；复现中早期消息后半段的约束未进入自动上下文。 |
| 触发消息和正式评审来源已有完整读取 | [adapter](../../src/zf/runtime/channel_adapter.py)、[semantic sources](../../src/zf/runtime/channel_semantic_sources.py) | 触发消息严格读取全文；正式综合、交叉评审等已有完整来源清单，不能把摘录限制描述成所有输入的统一上限。 |
| 普通聊天待回复请求可合并 | [reply queue](../../src/zf/runtime/channel_reply_queue.py)、[formal queue tests](../../tests/test_channel_formal_reply_queue.py) | 同成员、同线程普通聊天使用 latest-only；原始消息未删除，但不保证每项输入独立处理。显式 handoff 和正式评审已有独立请求身份。 |
| Web 正文和结构化摘要是不同投影 | [conversation projection](../../src/zf/runtime/channel_conversation_projection.py)、[Markdown guard](../../web/src/components/agent-session/markdownGuard.ts) | 不能用摘要条目数推断全文丢失；长正文还有显示保护阈值，完整存储不等于浏览器已完整展示。 |

现有 [dispatch claim](../../src/zf/runtime/channel_dispatch_claim.py)、
[run owner](../../src/zf/runtime/channel_run_owner.py) 和
[reply remediation](../../src/zf/runtime/channel_reply_remediation.py) 已提供
请求锁、generation 隔离、失败记录与队列恢复。本设计复用并补齐它们。

后续验证区分确定性故障注入与真实 provider：本批次覆盖的窗口、真实五角色接力、
长文浏览器显示和 CLI 对照列于实施记录；未将未覆盖的所有崩溃窗口宣称为已证明。

## 3. Rovai 参考结论

用户提供的 `/tmp/rovai-ai` 不存在；本次实际参考 `/tmp/gm/rovai-ai`。
该路径只是审查来源，不成为 ZaoFu 的运行依赖。以下路径相对于参考仓库根目录。

| 主题 | Rovai 实现与来源 | 采用范围 |
| --- | --- | --- |
| 角色 | `crates/rovai-core/src/context.rs`：`MemberIdentityBootstrapProjection` 含 name、team_role、professional_responsibilities、personality_traits、working_principles 等；Bootstrap 将身份和 session charter 分开 | 借鉴职责与运行协议分离，不复制全部画像字段。 |
| Skills | `docs/architecture/skills.md`：平台操作指南保留，工具箱与原生 Skills 按来源和选择投递 | 借鉴按需方法，不推导出“完全没有 Skills”。 |
| 公开输出 | `crates/rovai-core/src/agent_profile.rs::public_output_mode`：所有已发布适配器均要求显式发送；最终运行输出属于执行证据 | 不照搬其公屏发布策略，ZaoFu 继续保留现有回答展示。 |
| 输出记录 | `crates/rovai-core/src/execution_text.rs`：按 native item 聚合公开文本，完成时保存完整正文，长文本使用 spool/blob | 借鉴 item 身份、增量与完成快照分离；不据此声称所有崩溃窗口或工具输出均无损。 |
| 消息大小 | `crates/rovai-core/src/team_tool.rs::validate_public_send_invocation`：公共发送正文超过 32 KiB 明确拒绝 | 不复制此上限；借鉴超限可见、不静默截断的原则。 |
| 接力 | `crates/rovai-core/src/message_delivery.rs`、`delivery_queue.rs`：消息、逐接收者 Delivery、冻结输入的 AgentRun 分离，FIFO 选择完整连续批次 | 用于检验 ZaoFu 现有请求和事件链，不引入另一套存储权威。 |
| 恢复 | `docs/architecture/public-a2a-message-delivery.md`：持久等待责任、冻结输入、接收回执与 unknown 边界 | 借鉴持久责任和可核实接收，不把已接收等同已理解或业务完成。 |

Rovai 的 Codex 最终回答选择也采用最后一个 agentMessage；其完整执行文本记录
与公屏消息是不同路径。因此它不是可直接复制的“CLI 全输出无损公屏”方案。

## 4. 角色、Skills 与协作协议

### 4.1 角色定义

沿用现有 Channel Profile / role definition，不另建角色配置系统。
`zf.yaml` 继续是唯一控制面；角色不赋予额外工具或写权限。

| 角色 | 主要职责 | 应保留的成果 |
| --- | --- | --- |
| 产品经理 | 用户目标、范围、优先级、验收与待决取舍 | 用户场景、边界、验收条件、推荐决策及理由 |
| 架构师 | 模块边界、状态所有权、依赖、演进与失败路径 | 方案依据、取舍、约束和兼容性 |
| Critic | 检查反例、证据缺口、矛盾与遗漏 | 可定位的质疑、影响、证据与解除条件 |
| UI/UX | 主流程、反馈、异常状态、可访问性与一致性 | 交互行为、用户可感知结果和验收场景 |
| 后端开发 | 接口、数据、并发、幂等、错误处理与可测试性 | 数据与接口约束、失败场景、实现风险 |
| 综合者 | 整理各方原文、问题及决定，形成可追溯产物 | 来源覆盖、未决项、异议及其处置 |

角色可以说明工作原则，但不统一规定句数、固定章节或“只写摘要”。回答长度应由
任务、证据和用户要求决定。充分回答不等于强迫所有成员输出长文。

### 4.2 Skills 的职责

- 移除通用的 3–5 句限制，以及系统提示、响应提示中压缩完整正文的要求。
- 专业 Skill 仅在用户选择、任务明确需要或角色按方法选择规则使用时加载。
- 保留来源核验、独立分析、反例检查等有价值的方法，不将项目语义写进 Kernel。
- `questions`、`freeze`、cross-review outcome、consensus 签收等约定仍需明确。
  移动或精简 Skill 时，将必要协作语义保留在当前阶段的提示/方法中，机械 schema
  校验仍由 Runtime 负责，不能直接删除整份文件后期待流程继续工作。
- 避免向只读讨论角色注入 worker 写文件、提交代码或直接更新状态的指令。

### 4.3 版本与迁移

修改 canonical `skills/` 后使用现有同步机制更新分发副本。运行中成员可能绑定
不可变 Skill/profile 快照；新源码不会自动替换旧绑定。

通过既有受控成员/profile 操作绑定新版本，保留成员 ID、权限和来源；在途请求及
其恢复继续使用原冻结输入，不修改历史 sidecar 或绕过 digest 校验。新版本用于
后续新请求，避免同一次执行混用新旧合同。

## 5. 完整输出与现有格式兼容

### 5.1 保留用户格式与机器合同

本阶段继续使用现有形式：

```text
充分展开的 Markdown 正文，保留段落、列表、表格、代码和换行。

<!-- zf:channel-contract -->
{现有阶段要求的合法 JSON 合同}
```

标记后的合同继续隐藏于普通聊天展示，使用已有解析器和 schema。
摘要字段服务于导航/卡片；不得用摘要覆盖已保存的完整正文。
问题、签收和 handoff 仍通过已有结构化合同或受控入口表达，不能靠猜测自由文本
中的人名、“交给某人”或任意 JSON 代码块执行动作。

本阶段不引入第二次模型“改写/补摘要”调用，也不迁移到全新的 tool 协议。
若以后改为工具提交合同，应独立设计版本兼容与恢复，不能混入本次输出修复。

### 5.2 Provider 公开文本收集

1. 按 run/generation、native session、turn、item 身份维护公开文本记录，记录
   provider 提供的 phase 与顺序；缺少 phase 时不臆造为 final。
2. delta 只扩充对应 item；完成快照确认或修正同一 item，不重复追加全文。
3. 保留所有公开回答 item。进度 commentary 单独投影；partial answer 与 final
   按明确规则组成可回读回答，不再由最后一个 item 覆盖整个回复。
4. 公开正文在应用既有安全策略后保存完整内容及引用/digest；展示投影不得反写原文。
5. 合同流过滤跟随 item/turn 与 continuation 边界，避免前一段合同标记让后续
   合法正文永久不可见；重复完成通知和重放不能制造重复段落。
6. provider 终态与正文保存支持运输层完成；业务完成仍须必要合同验证成功。
   两者不能混用。已收到的部分正文可以保存为不完整证据，不能因非空就标记成功。

完整文本使用既有受控 sidecar 原子写入并绑定事件；不把大正文塞入事件预览。
新记录字段先明确兼容性，再扩展现有合同；上面的身份维度不是已上线 schema。

### 5.3 Web 展示

保持现有消息布局、Markdown 与问题/状态卡片。为了大文本性能，可以分页、折叠
或虚拟化，但必须提供访问剩余正文的入口及明确的完整性状态，复制/导出应覆盖全文。

刷新、断线重连后从持久原文与当前投影恢复，不依赖浏览器此前收到的 SSE 片段。
正文不可读或校验失败时，不得用预览片段伪装完整回答。既有 Markdown 安全与
复杂度保护可以继续存在；保护措施不能成为无法发现的正文丢失。

## 6. Handoff 兼容与可靠交付

### 6.1 保持现有入口

[request_channel_handoff](../../src/zf/runtime/channel_handoff.py) 继续接受
明确的 channel/thread、来源 message、发送成员、目标成员和 reason。
保留既有受控动作、权限校验、请求 ID、因果引用、预算、去重和 generation 规则。
不因借鉴 Rovai 就删除 ZaoFu 现有轮次/深度限制。

取消短摘要模板不改变上述入口。会改变的是交付的正文完整性、上下文范围及等待
责任的保存方式；新增字段应兼容旧请求，旧事件保持可重放。

### 6.2 责任与状态区分

以下是验收语义，不要求新增同名事件或另建状态存储：

```text
完整来源消息持久化
  → 每个明确接收者有可恢复的待处理请求
  → 冻结本次输入、来源引用与版本
  → provider 接收（或明确未接收 / 结果未知）
  → 执行结束并持久化完整结果
  → 当前阶段的合同验证与业务处置
```

`channel.handoff.accepted` 表示本地接受交接意图，不能被解释成 provider 已接收。
provider 接收、模型完成、问题解决及 Owner 签收也是不同事实。

- 显式 handoff 与正式评审继续逐请求保留，不能退化为 latest-only。
- 对需要保留每项工作的普通协作输入，按完整有序批次或逐条处理；合批必须保留
  所有输入 ID、正文和责任，不能只保留最后一条。
- 用户明确替代/取消旧工作时记录原因和原输入关系，不能把隐式合并当作用户撤回。
- 多接收者分别追踪状态；一人失败不能伪装为整条消息成功，也不应重跑已完成目标。
- 本地锁和进程内唤醒只是执行协调手段，持久请求/事件才是恢复依据。

### 6.3 崩溃、重复与未知结果

必须检查“交接意图已记录但 reply request 尚未生成”等写入间隙。沿用既有事件
账本与恢复 owner，通过稳定请求身份和幂等补全闭合这些窗口，而不是只加内存队列。

明确未送达且安全的请求可以重试；provider 已接收或接收结果未知时，先核对会话、
run、generation 与可用回执，不得直接当作未执行重投。旧执行尚未隔离时，恢复
不能让迟到结果覆盖新执行。无法判定的责任应可见，不能默默消失或报告成功。

运输层重试和事件重放以幂等收敛为目标，不宣称外部 provider 的绝对 exactly-once。

## 7. 接力上下文与语义覆盖

### 7.1 本次必须交付的内容

复用并扩展现有 context pack、semantic source manifest 和问题账本：

- 当前目标、原始要求及用户后续修正；
- 本次所有待处理输入的完整正文和稳定消息身份；
- 本次任务依赖的原始证据与完整公开回答，按成员/轮次/来源关联；
- 已确认决定、未解决问题、尚未解除的异议及其责任人；
- 引用、digest、字节数、版本与本次输入的完整性状态。

不能只把上一位的总结转交给下一位，再把总结当成原始证据。附件和大文件继续使用
现有受限引用与校验路径，不能扩大到任意文件读取。

### 7.2 有限窗口下的完整性

“不遗漏”不要求无限量把全部历史塞进每次 prompt。必须区分：

1. 本次执行必需来源：完整交付，或通过受控、可验证的全文读取入口交付。
2. 可选历史：提供可发现的索引、分页/读取入口和明确的范围边界。
3. 当前阶段不可见内容：保持隔离，不能为完整性绕过盲答或权限限制。

multi-lens 的第一轮保留独立盲答；进入允许共享的 relay / review / synthesis
阶段后，才按当前阶段权限共享相关来源。typed phase 的工具限制也保持有效；
若需全文读取，应使用既有完整 pack/受控文件交付，不能无条件开放工具。

达到模型或配置预算时，按完整来源拆批、使用已支持的全文交付机制，或明确阻塞。
无法在同一推理上下文容纳的任务需显式分解；不能截掉尾部后仍声称输入完整。
评估成本时同时计算 bootstrap、历史、来源、合同及输出余量，不把配置窗口当作
模型实际能力保证。

### 7.3 数据保证与语义保证

| 层次 | 可验证保证 | 不能推导出的结论 |
| --- | --- | --- |
| 保存 | 原文可读、digest/大小一致、缺失可发现 | 模型已经读过 |
| 交付 | manifest 与实际投递输入一致，接收状态可核对 | 模型理解了每个细节 |
| 业务覆盖 | 结果关联原始要求、问题、异议及处置证据 | 自报 consumed digest 就等于语义完整 |

语义遗漏由角色方法、覆盖清单与独立 review 降低；Kernel 只检查身份、引用、schema、
状态和证据存在性。复用既有问题与审查账本，不另造一份可竞争的需求/任务真源。

## 8. 兼容性与实施顺序

| 阶段 | 范围 | 完成条件 |
| --- | --- | --- |
| A：角色与提示 | participant / synthesizer 等相关方法、system prompt、response prompt、profile 绑定 | 正文不再受统一句数限制；原 JSON 合同、freeze、提问和签收行为仍通过回归。 |
| B：完整输出 | provider 文本聚合、sidecar、流过滤、API / Web 投影 | 多 item、长文、continuation、刷新后结果一致；原文与摘要职责分离。 |
| C：可靠接力 | context pack、队列、handoff、现有恢复 owner | 所有待处理输入可追踪；来源完整；明确处理超限、未知和崩溃窗口。 |
| D：真实验收 | 同条件 provider 对照与 Docker Playwright | 实际多角色接力完成且逐项覆盖；没有丢段、静默替代、重复执行或悬挂。 |

各阶段使用独立 worktree 实施，遵守显式 pathspec 和影响闭包验证。回滚代码/配置
不删除已产生的原文、事件和旧版本快照。上线前明确在途请求的旧版读取与终态化策略。

Kanban Other 问答、群组创建、Owner 决策、PRD 同版签收和 workflow 控制权限保持
原协议；本设计不借机改变它们的产品含义或审批边界。

## 9. 验收标准

以下均为待实施验收，使用 `step → verify`，不将文档生成视作测试通过。

| 场景 | step → verify |
| --- | --- |
| 充分回答 | 让每个角色回答包含多项约束的任务 → verify：正文允许充分展开、合同合法，不以长度作为唯一质量指标。 |
| 多公开 item | 依次发送 commentary、partial answer、工具活动、final answer → verify：进度独立，回答段落完整，顺序正确，最终结果不只剩末段。 |
| 增量与完成 | 输入 Unicode/换行分片、重复完成快照及可识别的重放 → verify：按 native item 收敛，无重复、乱码或被覆盖的前段。 |
| 长正文 | 超过现有 Markdown/纯文本展示阈值 → verify：完整存储、API 回读、浏览器全文访问及导出一致；保护阈值可见。 |
| 合同与续写 | 在合法终端标记附近分片，并触发续写/重试 → verify：合同不泄露成普通正文，后续正文不被永久过滤，无重复动作。 |
| 原始约束接力 | A→B→C，分别在早期长消息末尾放置要求和异议 → verify：依赖来源实际交付，最终结果逐项保留或显式处置，不只复述中间摘要。 |
| 连续输入 | 成员忙碌时发送三个不同任务，含普通协作和显式 handoff → verify：所有输入进入有序执行或完整合批，没有隐式 latest-only 丢责任。 |
| 多接收者 | 一条消息交给多个角色，令一个目标失败 → verify：状态分别可查，其他目标结果保留，恢复不重复已完成执行。 |
| 崩溃恢复 | 在消息保存、意图接受、请求生成、provider 接收、结果保存等边界退出并恢复 → verify：责任不消失、结果不覆盖、补全幂等。 |
| 接收未知 | 模拟 provider 已接收但 ACK 丢失 → verify：保留未知状态并核对执行，不盲目重发。 |
| 来源异常 | 删除测试 sidecar、改变 digest 或设置不足预算 → verify：明确失败/阻塞，不以摘要冒充完整来源。 |
| 旧版兼容 | 回读旧合同、旧 profile 与历史消息，恢复在途请求 → verify：冻结输入不漂移，新版本仅作用于新请求。 |
| 盲答与权限 | 第一轮及跨线程/项目读取 → verify：不泄漏其他成员盲答，不突破原权限、附件与文件范围。 |
| 已有流程 | Other→创建 multi-lens→提问→Owner 回答→综合→签收 → verify：流程仍可收敛，无悬挂，不自动跨越原审批。 |

真实 CLI / Channel 对照须记录同一模型、reasoning effort、CLI 版本、项目指令、
权限、可用工具、输入材料和相关会话条件。角色与协作协议是显式差异变量；比较
事实正确性、要求覆盖、证据、异议保留与完成度，不要求逐字相同，也不以字数定优劣。

### 9.1 验证层次

- 模块回归覆盖 provider 收集、合同解析、来源验证、队列和直接调用方。
- Event/Store/schema/orchestration 变更运行影响闭包、pre-merge sentinel 与相关
  deterministic mock E2E；共享 headless 修改还需覆盖 Kanban Agent 调用方。
- 前后端边界变更运行 frontend build/typecheck、对应 API 和浏览器测试。
- 真实 provider + Playwright 使用 Docker `mcp/playwright:latest`、host networking，
  API/UI 监听 `0.0.0.0`；测试状态使用 `/tmp/zf-<purpose>-<utc>/`，临时端口 8002+。
- 真实 dev 的 8001 不用于临时模拟。执行结束按仓库规则清理临时服务、会话和状态，
  不把 UI fixture 或 mock provider 成功当作真实端到端证据。

实施时先运行 `python scripts/dev-verify.py plan --base dev` 确定影响闭包，随后执行
对应验证层次。没有通过原文完整性、恢复与业务覆盖检查前，不标记本设计 implemented。

## 10. 相关入口

- [设计索引](00-index.md)
- [181 实施与验证记录](../impl/channel-group-181-lossless-relay.md)
- [Channel 协作使用说明](../manual/15-channel-collaboration.md)
- 本地历史参考：`docs/impl/channel-setup-other-recovery.md`、
  `docs/impl/channel-prd-file-publication.md`（公开导出不包含这些历史文档）。
- [Channel handoff](../../src/zf/runtime/channel_handoff.py)
- [完整语义来源](../../src/zf/runtime/channel_semantic_sources.py)
- [回复恢复](../../src/zf/runtime/channel_reply_remediation.py)
