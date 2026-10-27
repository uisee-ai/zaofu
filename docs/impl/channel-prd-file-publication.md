# Channel 文件预览与 PRD 草案发布

群组设置、自定义 Other 回答及问题整理恢复见 [Channel 设置的 Other 回答与恢复](channel-setup-other-recovery.md)。

群聊中的本地 Markdown 文件是交付来源，不是已登记或已批准的 PRD。对应实现为
`channel_document_source.py`、`channel_prd_file.py` 和既有 `channel-synthesis-request`。

## 浏览器行为

群内 Markdown 的本地文档链接使用受限文件预览，不再导航到网站同名路径。
后端只接受当前项目、当前线程明确引用的文件；保留摘要、大小、UTF-8、敏感信息、
隐藏路径和 symlink 防护。普通外部 HTTP 链接保持原行为。

文件预览提供“生成正式 PRD 草案”。这是一项显式、token-gated 操作，不是打开预览
的副作用。请求携带 `source_file: {ref, sha256}`，摘要必须与刚预览的文件一致。
当前问题未解决、仍有回复在执行或有成员待批准时不接受发布。

## 同一条 PRD 管线

受控动作读取完整文件，原子保存到配置 state_dir 中的 `channels/prd-sources/`，
将 descriptor 绑定到 `channel.synthesis.requested` 及正式 synthesis 消息。
Agent 得到不可变来源和完整 `full.v1` 合同；可以用 `prd_file: {ref, sha256}` 引用
这个快照，或输出修订后的 `prd_markdown`。不能引用任意作者路径或其他线程的快照。

Runtime 展开正文后沿用来源覆盖、需求—验收映射、readiness、版本、同版 review、
Owner 确认和 Task/Workflow 权限门禁。没有新建另一套 PRD 状态机。read_only agent
可通过结构化回复交付；不需要为了 Runtime 保存文件而提升其文件写权限。

显式文件发布把已存在的讨论进入 `phase3_synthesis`，用于恢复“文件已生成，但旧的
盲答请求曾取消”的人工交付。保留原 roster/review_roster、问题台账及失败事件，不补造
盲答贡献或签名。后续按该讨论已有的 review 策略复审，仍不得自动批准或启动 Workflow。
不带 `source_file` 的自动 synthesis、普通聊天和原有 Synthesize 路径保持原语义。

只有成功通过原有合同的回复才会发布 PRD revision；源文件或链接出现不算完成。
草案登记后可预览，显示 Draft/In review；Ready 与可规划 Task 仍由当前版本签收、
Owner 确认和 readiness 决定。修复回合保留原来源快照，延迟/重复回复继续遵守
既有 currentness 和幂等规则。

## 跨层质量合同

质量增强版 synthesis 使用 `quality_contract=channel-prd-quality.v1`。Runtime 在发布和
独立评估前执行同一组机械校验：`source_attestation` 必须与来源读取状态及附件可读状态
一致；`requirement_coverage` 每行必须有直接 `evidence_refs`；研究行必须写明采用结论和
适用边界；`consumed_contribution_refs` 必须由互斥的 `canonical_cross_review_refs` 与
`supporting_contribution_refs` 完整分账；权限审计必须声明拒绝默认、事件字段、查询字段、
留存策略和责任人。校验失败不会进入 provider 评估，避免评估器在冻结清单与目标 PRD
互相矛盾时产生误导性结果。

Owner 未决问题仍可发布可评审草案，但 synthesis 会标记 `owner_gate=blocked`、
`canonical_status=draft`，不会自动提出同版 consensus；问题解决后必须重新生成当前版本。

文件快照减少重复输出长正文，但不降低语义来源上下文预算，也不替代独立内容评审。
如果历史讨论超出已配置来源预算，仍应报告具体 context 拒绝原因，不能忽略来源门禁。

## 验证

- `tests/test_channel_prd_file.py`：真实受控动作、快照、完整版本、同版评审与重放防护。
- `tests/test_channel_file_preview.py` / `tests/test_web_channel_delivery.py`：路径和 HTTP 读取边界。
- 原有 PRD composition、synthesis repair、discussion、consensus、Task/Workflow 合同测试。
- Docker Playwright：群内链接预览、明确提交草案、同版 PRD 预览及未批准的 Process 禁用。

## 路由拒绝、重试与长上下文

来源预算或权限校验拒绝路由时，`channel-synthesis-request` 返回 409 与具体原因，
记录 `channel.synthesis.blocked`，不再返回成功已调度。历史 `channel.context_pack.rejected`
按其 trigger message、目标成员和 synthesis request 绑定重放为 blocked，不修改旧事件。
显式重试创建新 request；只退役它所替代的生成请求的上下文拒绝，其他线程/成员的独立
拒绝仍生效。正在运行的生成仍不可重复提交。PRD 面板在刷新后继续显示拒绝原因；
文件预览提交失败保留预览，修正原因后可再次提交同一文件。

选定文件的完整、摘要校验后的正文进入 `semantic_source_documents`，kind 为
`channel_prd_source`；正文计入来源字符预算。模型无需突破 typed phase 的禁止工具约束。
快照必须绑定当前线程的持久生成请求，损坏或不匹配时失败关闭。

两种预算不同：`channel.semantic_source_max_chars` 是完整来源字符上限，
`channel.codex_context_window_tokens` 是本项目 Channel 原生 Codex 的上下文 token 配置。
后者默认 0（保留模型默认），允许 0–1000000 的整数；需由操作者根据实际模型能力设置，
不能把该值解释为模型必定支持的容量。它仅传给 Channel 创建的 Codex app-server，
对应 `model_context_window`，并将 `model_auto_compact_token_limit` 设为窗口的 90%。
其他 provider、Kanban Agent、全局 Codex 设置保持原配置。实际覆盖值写入 reply.started 事件。
`channel.provider_timeout_seconds` 单独约束一次 Channel headless provider 调用的总期限，
允许 0–7200 的整数秒；0 保留既有运行时回退（未覆盖时1800秒），非零项目配置优先。
同一个值传给provider并写入reply.started，供超时watchdog采用相同期限及已有回执宽限。
增大期限不改变来源完整性、模型窗口、审批或重试次数，也不保证provider能返回结果。
配置依据：[Codex 官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。

长文档需分别核算完整生成输入和后续同版 review 输入，为输出、推理和系统提示留余量。
不得截断来源、删除异议、伪造消耗声明或为通过测试放宽 Owner/Workflow 审批。
新增回归：`test_channel_synthesis_routing.py`、`test_channel_provider_config.py`、
PRD 刷新后错误提示的 Docker Playwright 用例。

历史重放同时检查 synthesis requested reactor 与通用消息路由入口；旧的失败请求及其消息不能在新请求之后重新派发。

实机运行还需核对 tmux 子进程的网络环境。Web 启动器将显式设置的大小写 HTTP/HTTPS/ALL/NO_PROXY 写入受限权限运行环境文件；不会输出代理值。继承该文件的控制进程使用相同网络配置。

## 暂缓决定与协作恢复

`channel-question-resolve` 新增显式 `resolution=deferred`，必须带非空 `answer`。
Owner 问答卡“本轮暂缓”提交此处置；范围排除仍使用独立 `out_of_scope` 操作。
已处置问题的回调重放和跨 thread 更新不得覆盖第一次决定；已注册 agent 不能代表 Owner
答复或暂缓。投影保留 `resolved_event_id`、回答和风险，完整台账进入后续综合上下文。
暂缓不是批准、删除原始需求或执行授权；具体语义、影响与重开条件由 participant /
synthesizer 技能整理进 PRD，不在 Runtime 中匹配“暂不考虑”等关键词推断业务结论。

综合提出问题后回退 `synthesis_questions_opened` 不会启动盲答去重，因此无去重请求时
允许 Owner 处理；存在实际去重请求时仍遵守其 pending/blocked gate。盲答阶段转换后
崩溃遗漏的初始去重请求由下一次 discussion tick 补发，仍沿用稳定 ID 与有界 retry。

全部问题处置后，缺少 roster 成员冻结贡献仍阻止自动综合。attention 暴露
`missing_frozen_members`、`contribution_missing` 和受控重启入口，不虚构角色贡献。
重启补齐贡献沿用原有讨论路径；此前 Owner 决定保留。角色齐备、交叉核验完成后由
现有 Kernel 路径触发新 PRD；同版本独立 review、Owner 确认、Task/Workflow 门分别保留。
PRD 可带暂缓项进入 review，受影响工作的 `implementation_start=false` 不被自动提升。

显式重启使用当前配置的有效参与者重新冻结 `participants` 与 `review_roster`，不会复制
旧讨论的成员快照。普通定向续轮仍受当前讨论冻结名单约束，不会隐式纳入新成员。
新讨论只有当前 trigger/request/generation 的冻结贡献才计入完成；旧贡献和已回答的
Owner 决定继续作为历史证据保留。`next_round.proposed/rejected` 纳入 Channel 投影，
当前 discussion/revision/synthesis 的续轮拒绝显示具体原因和重启入口，旧轮次拒绝不污染
新轮次。有正式 PRD 但尚无评审时显示评审待启动，不再显示 `synthesis_missing`。
最终回复把盲答推进到 relay 后，真实 reactor 立即再检查一次该阶段的正常收敛门禁。
零开放问题的定向补充不再等待最长五分钟的 sweep；有问题、待交叉核验或待成员批准时
仍停在对应门禁。重放最后一条回复不会重复创建综合请求。
轻量聊天投影保留原始需求标记、review/restart 来源引用与附件元数据，并从 sidecar
恢复完整用户消息；否则前端重启选择器会错选旧需求，长需求还可能被摘要截断。

同线程上传的文本附件（包括重启前上传的原始需求）经既有附件读取器验证归属、sidecar
与内容摘要后，以 `channel_attachment` 纳入完整语义来源包。正文严格 UTF-8 解码并计入
既有来源预算；损坏、错线程或超预算失败关闭。重复附件只计一次，二进制附件沿用引用
路径。对应回归见 `tests/test_channel_restart_roster.py`、
`tests/test_channel_semantic_attachments.py` 和 `web/tests/channel-review-restart.spec.ts`。
重启前的同线程用户消息也保留完整正文，不要求手动标记；历史显式标记仍兼容。Agent 在
完整上下文中区分需求、补充更正、候选草案和操作指令，不能只依赖后续转述。
完整来源超过 `channel.semantic_source_max_chars` 时保持拒绝，不自动截断或放宽预算。
操作员可在 `zf.yaml` 按实际来源规模及 provider 容量调整有界预算，再重试绑定原 ref/digest
的评审。相同线程、成员和文档 ref/digest 的后续完整 context 构建成功后，旧拒绝仍留在
历史中，但不再覆盖当前 attention；其他成员、版本或不完整重试不能清除该阻塞。
对应回归见 `tests/test_channel_context_attention.py`。
当前 PRD 存在 `revision_request` 评审阻塞时，Owner 确认动作返回 409，即使该版本曾有
Owner 确认也不能通过幂等重放掩盖后来发现的缺陷。新版仍需独立评审与新的 Owner 确认；
Owner 决定不替代角色签名。回归见 `tests/test_channel_owner_revision_gate.py`。

同一确认 PRD 可创建多个独立 Task；`task_created` 回执的幂等键包含 Task id，不能仅按
PRD 摘要和版本合并。旧键只对其原 Task 继续去重，重启 reconciliation 会补回其他 Task
缺失的回执，不改写历史事件。回归见 `tests/test_channel_result_receipts.py`。

长文件修改可返回 `prd_composition: {source_ref, source_digest, edits, appendix_markdown}`。
ref/digest 必须匹配本次不可变上下文的 `channel_prd_source` manifest；不接受 anchors，
始终保留完整文件再应用唯一匹配、不重叠的精确 edits。此模式减少重复输出，不截断必读
输入，也不等价于测得真实模型成本下降。消息来源继续使用原有 source_message_id / anchors。

语义验收由 `skills/zf-channel-discussion-synthesizer/SKILL.md` 与 participant 协议承担：
逐项核对决定继承、暂缓语义、需求章节及验收、异议处置、重问依据、评审版本。
自动化验证见 `tests/test_channel_collaboration_closure.py`、`tests/test_channel_prd_file.py`
（包括带暂缓草案进入真实 review reactor / fake provider 后仍被 Owner 风险门拦住），
以及 `web/tests/channel-owner-deferral.spec.ts`。真实模型语义质量与长期成本仍需独立实跑评估。

隔离浏览器复现（依赖 dev/web 环境、已构建 `web/dist`，无需真实 provider）：

```bash
PYTHONPATH=src:. .venv/bin/python tests/e2e/channel_collaboration_browser_fixture.py \
  --root /tmp/zf-channel-collaboration-<新的UTC时间> --port 8002
```

另一个终端用 `mcp/playwright:latest`、`--network host` 挂载 repo 与前端依赖，在
`web/` 运行 `node node_modules/@playwright/test/cli.js test tests/channel-owner-deferral.spec.ts`；
设置 `ZF_WEB_BASE_URL=http://127.0.0.1:8002`、`ZF_COLLABORATION_BROWSER_FIXTURE=1`，
`ZF_E2E_CHROMIUM_EXECUTABLE_PATH` 指向容器内 `/ms-playwright/chromium-*/chrome-linux64/chrome`。
测试使用固定的隔离测试 token，不读取生产 token。结束时 SIGTERM fixture 进程，
fixture 会写入 `simulation.done`；保留需要的证据后删除临时 state/workspace。

## 大型完整来源的配置预算

`channel.semantic_source_max_chars` 限制一次完整语义来源集合的字符数，包含讨论正文、
文本附件、选中文件和复审工件，不只是单个 PRD 的大小。全局默认为 `2000000`，可配置
范围为 `1..10000000` 的整数；YAML loader 与直接构造 `ChannelConfig` 使用相同上限。
较大的项目可按实际 rejection 中的来源总量设置，例如 `2000000`；复审还会携带完整
PRD 工件，需为其预留容量。超过项目配置值仍拒绝整次请求，不截断正文或省略来源。

未显式设置该字段的所有项目、直接构造的配置和无配置运行时路径均采用同一默认值；
项目显式覆盖值仍优先，不自动重写已有配置。提高此值不会提高 provider 的 token 窗口、取消
文件预览大小限制、跳过摘要校验、独立复审或 Owner 门禁。字符预算不是 token 预算；
较大上下文可能增加内存、模型读取时间和费用，应按实际业务量配置。修改允许范围的
代码后须重启使用该代码的 Web/运行时，再加载项目配置；仅更新磁盘文件不代表常驻
进程已经更新。

回归覆盖：YAML/typed 边界与默认值、超过100万字符的完整来源保留，以及大文件经过
受控发布、不可变快照、review reactor/fake provider、Owner 风险门和幂等重放的流程。

Codex 实测另有单次文本输入长度限制。Channel 完整上下文超过750000字符时，本地
headless provider 使用已有的不可变 context-pack 文件与 SHA256 读取全文，避免把整个
JSON 塞入 RPC 文本。文件通过现有 sidecar 路径、大小及摘要校验后才交付；普通输入仍
内联。typed phase 仅允许只读读取该指定文件，不允许探索其他文件或修改运行状态。
agent 必须完整读取来源后才能声明消费完成；文件丢失、篡改或未读完整均不得声明通过。
这不增加模型 token 窗口，也不取消已有 sidecar 大小限制或同版复审门禁。

## 独立评估的原始文本附件

同版 PRD 评估的来源阶段包含原始需求消息及历史上显式标记的来源；其他本版绑定的
Owner 消息及其文本／JSON 附件作为未预分类的决定证据交给独立评估者，识别补充需求、
更正、候选草案和操作指令。新增需求的标准 ID 必须在最终矩阵中覆盖。生成本版之后的
新消息不混入旧版评估；更正需经过讨论与重新综合产生新版。
附件沿用 Channel 的线程范围、存储摘要、字节数与正文摘要校验；缺失、损坏、
跨线程或无法按 UTF-8 解码时，评估失败，不以消息中的文件名或摘要代替正文。
来源和决定证据摘要绑定消息与附件身份。普通讨论消息和候选附件不自动成为产品需求，
仍保持先读取来源、再读取决定、最后评估目标的阶段隔离。PRD 预览从同版不可变来源
清单展示已验证的 Owner 消息、文本附件与 Agent 需求映射；不可读取时明确提示。用户可
返回讨论补充或修正，不直接修改已发布 PRD。
