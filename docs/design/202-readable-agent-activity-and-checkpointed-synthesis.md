# 可读 Agent 活动与可恢复的分段 PRD 综合

> 状态: implemented
>
> 用户于 2026-11-12 批准效果图、所有 Agent 的可读输出与综合超时修复，并要求实际验收。
> 已接入真实调用入口；验证层级、首轮格式拒绝和恢复后的正式发布见配套实施记录。

## 1. 问题与边界

狼人杀原群的一次文件发布读取了 952,883 字符的上下文；历史综合占语义来源正文的
70.5%。约七分钟核对后，Provider 输出四段未完成合同，合计 216,345 字符；重连后
从合同根重新开始，最终触及 1,800 秒总期限。公开流过滤合同，用户只看到 Working。
正文的 `prd_file` 复用已经生效，不能将这个问题归为再次输出 Markdown。

本批沿用当前工作分支、既有 Channel 与审批路径。共享展示覆盖 Channel、Kanban Agent
及共享时间线的各角色；分段综合首先接入新发起的 libre_discuss 正式请求。历史请求与
旧模式保留原协议，不重放已经取消的生成，也不修改用户模型、全局网络或超时配置。

效果参考：`output/imagegen/libre-discuss-agent-process-{collapsed,expanded}-v1.png`。
图片中的时间、计数是示例；产品必须来自真实活动和持久凭据。

## 2. 可读输出

- 正文以 Markdown 呈现，问题、邀请、审批继续使用已有结构化卡片。
- 每个 Agent 共享一个默认收起的工作过程：当前公开进展、耗时、已保存部分／活动数。
- 展开后显示有序步骤、文件读取／检索／执行活动、失败与恢复；长参数和原始输出再折叠。
- 原生 commentary 按消息归并，不把逐字 delta 渲染成零散状态；不要求提供私有推理。
- 原始事件 ID、内部运行路径、JSON 合同不占据常规聊天；显式诊断入口保留证据。
- 当前工作只有已有数据时才描述具体阶段；无阶段数据时使用自然语言通用进展，
  不伪造百分比、预计剩余时间或“已保存”。
- 完成、失败、取消后停止计时；失败有明确原因与当前可用动作。
- 这些是一次调用的执行进展，不是新的整群状态门禁。生成期间可继续聊天，后续输入
  不混入已冻结材料。文档阅读器不重新放入原材料编辑表单。

## 3. 来源整理

分段请求保留全部原始 Owner 输入、题卡决定、有效角色贡献、异议与研究证据。
`synthesis_attempt` 是模型生成的派生产物，在新请求中保留身份、摘要、字节数、原始
ref/digest 与审计索引，不把失败或旧版合同全文反复作为新的需求正文传给模型。
不能将用户自己提供的 PRD 草稿、纠正或明确引用当成上述派生产物删除。

相同正文按内容摘要复用：每个来源身份仍在 manifest 中，完整正文只存一份，读取
合同明示内容索引的解析方式。缺失、损坏、来源绑定错误仍失败关闭，不截断要求。
活跃 required 来源和历史派生审计分账；`all_required_sources_read` 只指本次完整的
required 集，不声称已读被排除的历史正文。新输入／答案／来源／角色合同变化会使
旧检查点失效；保留审计并重新生成。

## 4. 分段交付

Runtime 只分组、保存和校验 Agent 的输出，不替 Agent 判断需求含义。分组共用本次
不可变材料快照、来源 manifest、问题台账、角色与权限合同。

| 部分 | Agent 交付 | 展示 |
| --- | --- | --- |
| document | title、summary、spec/document_control、正文三选一、assumptions/out_of_scope、NFR/metrics | 核对材料与整理文稿 |
| requirements | requirement_coverage、user_stories、edge_cases、key_entities、acceptance_criteria | 保存需求与验收映射 |
| decisions | decision_log、decisions、dissent、risks、open_questions | 整理已确认决定 |
| implementation | implementation_companion、verification_commands、prd_quality_checklist | 补齐开发交接 |
| finalize | 来源声明、readiness、classification、permission_audit、workflow/next_round 等 | 复核并组装草案 |

各部分采用单独的有界 Provider turn，输出 `channel_synthesis_part`，不在每轮重发
完整合同。后续部分收到已保存结果与稳定 ID；需要核对完整来源时仍可读取当前包。
原生 commentary 仅是简短工作说明，机器字段不作为聊天正文。

每部分必须完整解析、符合字段归属与基本类型，再由 Runtime 原子保存 sidecar。
文稿中的 NFR 与成功指标复用原 schema 校验，避免缺字段到最后才被拒绝；完整但不符合
当前 schema 的缓存只重做该部分，摘要或绑定错误仍失败关闭。
字段不重叠；非法／截断输出只重试当前部分，次数有界，所有部分共用原总执行预算。
未知投递结果保留已有保护，不自动重投，不把迟到输出当成功。

最后由 Runtime 机械合并各部分为原 `channel_synthesis`，依次走既有正文展开、
来源消费、质量合同、PRD schema、currentness、评估和 Owner 门禁。部分保存不是
正式发布；不产生 Task、Workflow 或批准。只有原发布路径成功才增加 revision。
分组名仅用于暂存，正式合同仍为原扁平结构。开发交接不得以 `requirements.acceptance_criteria`
等暂存路径引用正式字段；保存前拒绝这些明确不存在的路径，只重做当前部分。

## 5. 检查点与恢复

- 使用配置的 state_dir 与 sanctioned atomic sidecar writer，不直接改 canonical store。
- 检查点绑定 channel、thread、请求／重试谱系、冻结输入摘要、来源和题卡摘要、
  profile／permission／skill 合同与分组版本；不能跨群、跨材料或跨角色复用。
- 同一请求的运输重试可以读取经验证的已完成部分；显式“沿用原材料重试”可以
  沿其持久谱系查找相同基线的部分。新一轮正常生成不读取无关旧缓存。
- 最终来源声明必须绑定本次上下文；跨请求的最终复核重新执行，不能改写模型声明
  冒充读过新来源。只有内容基线完全一致的业务部分可复用。
- 业务部分若包含其原上下文摘要／身份引用（例如开发交接的当前来源基线），也按上下文
  绑定部分处理；跨上下文重试时重新生成该部分，不能仅因语义来源相同就直接复用。
- 保存后才报告完成计数。重复保存同样内容幂等；摘要错误、阶段越权、依赖缺失、
  内容冲突必须失败。取消／退役请求仍由既有 request/run fence 阻止发布。
- 中断后保留检查点，前端先说明失败原因和已保存部分；沿用原材料重试时仅补未完成
  部分。停止生成保留原 PRD、聊天和材料。
- Web 与 Harness 不共享进程注册表。Web 写入绑定当前 run/thread/generation 的取消意图，
  实际持有 Provider 子进程的 Adapter 从现有 EventLog 增量读取并终止自己的调用；
  只有实际终止回执才能展示已停止，旧 generation 和迟到结果不能发布。

## 6. 预览与进度

进度通过已有 Agent session 的结构化 status refs 投影，不再创建另一套 UI 状态机。
每个阶段有稳定 ID、中文标题、状态、已保存计数及可核验检查点引用。新页面、刷新与
完成回放从持久进度恢复；活动中的工具／公开说明由现有 live stream 更新。

PRD 阅读区明确区分已发布文档和生成中草稿；生成中／未通过最终校验的部分不得显示
为正式 revision、通过评估或可开发。草稿预览仍是只读，不自动创建新生成请求。

## 7. 验收合同

1. 来源回归：重复附件／文件保留各自身份且正文只交付一次；历史综合全文退出新
   required 正文，Owner 输入与异议不丢；旧模式和历史协议仍可重放。
2. 分组回归：部分截断、格式错误、重连重复起头、进程中断、不同材料、跨群、
   篡改摘要、未知交付、取消和重复完成；保存部分可恢复且最终只发布一次。
3. 合同闭环：最终通过原来源、schema、质量、评估与审批入口；缺部分／冲突不能
   产生正式 PRD，不能借检查点绕过 Task／Workflow 批准。
4. 共享 UI：Channel 多角色、Kanban、普通文本／工具／公开进展／错误、收起／展开、
   完成／失败／取消、桌面和窄屏，均能阅读；常规视图不泄漏原始合同或零散 delta。
5. Docker 浏览器：验证实际组件，模拟写入全部拦截，保留截图并逐张检查；构建、
   受影响单测、边界 sentinel 与相关 mock E2E 通过。
6. 真实使用：通过 Playwright 原前端在原项目发起一次正式综合，等待完整交付，核对
   Preview／完整正文／版本绑定与独立评估；不创建开发 Task／Workflow。
   模拟断线恢复与真实 Provider 正常完成分别报告，不把正常实测冒充真实断网复现。

## 8. 资料

- [libre_discuss 产品合同](201-libre-discuss-channel.md)
- [现有文件发布与恢复](../impl/channel-prd-file-publication.md)
- 本地问题证据：`output/playwright/werewolf-prd-synthesis-timeout-diagnosis.html`
- [OpenAI Docs：App Server](https://learn.chatgpt.com/docs/app-server)

## 实施追踪

见 [实施与验证记录](../impl/readable-agent-activity-and-checkpointed-synthesis.md)。真实 Provider 验收结果单独记录，不将模拟通过等同于真实网络故障验证。
