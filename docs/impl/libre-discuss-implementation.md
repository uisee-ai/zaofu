# libre_discuss 实施与验证

设计：[201 libre_discuss](../design/201-libre-discuss-channel.md)。
工作分支：`wip/codex-20261021-onboarding`。日期：2026-11-11 UTC。

## 交付行为

新创建的 Channel 默认采用 `libre_discuss`，创建本身不发送种子、不调用 Agent。
历史记录没有模式时仍按 conversation 解释；显式选择的三种旧模式保留原流程。
Kanban Agent 的计划合同支持仅创建，旧 create-and-start 是显式旧模式路径。

普通发言仅由主持人接话，点名只唤起指定成员，@all 固定本轮成员范围。
主持人可提交一次必要接力；普通成员的文字提及不会触发自动广播。
用户发送新消息、主持人变更后，不再派发旧轮尚未执行的接力。
邀请、profile 创建与成员批准保留，新成员批准后安静加入。
接力上下文被拒绝时按局部失败结算；采用独立 spine 报告/提案协议的审阅角色
沿用该协议，不被误当作必须等待的原生聊天回复。

每条原始用户消息界定一轮。回复和结构化问题按原轮收集；至少两名不同成员
完成回复且有两道开放问题时，先调用综合角色进行语义去重，再发布原生题卡。
未达到阈值直接发布原题。去重失败、明确取消或到期后以未去重标识发布原题。
每道候选题必须有保留或合并去向，选项需完整一一映射；合并在一个事件中原子生效。
模型完成传输不等于语义结果已应用，避免过早回退；回退发布后拒绝迟到的去重改写。
独立场景、不同决策、复合题和少数意见的辨别由 Skill 指导模型，运行时不猜语义。

回答最后一道题不再自动生成 PRD、评审或开下一轮。定向事实问题允许被指派成员在
读取相应问题、提供答案及证据后更新为已核实；不能代替用户回答产品取舍。

PRD 按钮始终可进入，可显式采用项目简介与补充材料。服务器受理时固化聊天、答案、
贡献、发现和材料快照，后续对话不进入当前稿。进行中重复点击返回原请求；失败后可
沿原快照重试，或按最新材料生成新稿。格式修复保持原快照。取消只停止采用本次结果，
迟到结果不能发布，旧 PRD 与聊天保留。前端还会尝试取消对应的运行中 Provider 请求。
正式发布和取消使用同一请求锁，避免两者交错时在取消完成后又发布。

不合格候选稿通过只读接口按需加载，校验原 sidecar 的摘要、大小与群/线程绑定，
界面明确显示“未通过校验”，不会取得正式产物或创建任务权限。
正式 PRD 继续使用原生完整文档合同、有限格式修复、评估及 Owner 确认。
父 Task 和 Plan Workflow 仍须用户明确推进。

## 代码与所有权

| 范围 | 主要模块 |
| --- | --- |
| 创建边界 | `channel_creation_policy.py`、模板动作、Kanban Plan 提取与应用 |
| 本轮协调与投影 | `channel_libre.py`、router、discussion、orchestrator/reaction/wake |
| 自由回复、事实核验、邀请提案 | `channel_libre_reply.py`、member admission |
| 前置问题归并 | `channel_libre_dedup.py`、question dedup/graph/gate |
| 生成隔离与取消 | `channel_prd_snapshot.py`、`channel_synthesis_cancel.py`、原生 synthesis 链 |
| 可读候选接口 | `web/channel_prd_candidate.py` |
| 界面 | `LibreDiscussion.tsx`、ChannelPage、原生 PRD 预览和题卡 |
| 语义规则 | 两个 canonical Channel Skill，`.codex` / `.claude` 同步镜像 |

canonical 状态仍由 EventWriter / ControlledActionService 写入。
没有增加常驻主持人服务或第二套全局阶段机。系统协调消息使用真实 system/runtime
来源并以主持人显示，不伪造 operator 用户发言；手动生成动作保留真实用户意图。
聊天 DTO 仅带局部轮次和生成进度，不复制整份历史、候选稿或诊断 sidecar。

## Channel 页面显示兼容调整

按用户体验反馈恢复原有 Brief → Discussion → PRD → Tasks → Workflow 导航、摘要及详情抽屉，
撤下自由讨论专用的三按钮栏和常驻说明栏。使用说明、问题整理及原题回退提示收进 Discussion
抽屉，成员管理、原生题卡、聊天和 PRD 预览沿用现有组件。

这次仅调整前端显示与入口：自由讨论不依赖旧阶段状态，空群即可聊天；PRD 导航始终可进入，
Synthesize 打开材料选择后再由用户提交。生成指示读取本次请求，取消后不继续显示生成中。
没有恢复自动开聊、答完问题自动综合或旧 Start/Restart/Drain 按钮。

PRD 阅读页面随后按用户反馈进一步恢复：移除 Preview / Source 顶部的生成材料表单，
`ChannelPrdPreview` 恢复原有组件合同。Synthesize 改为打开独立的材料抽屉，保留补充材料、
项目简介、请求进度、取消与失败候选稿入口；其中 Preview PRD 返回原生阅读页面。
本次仅调整阅读与生成入口，不改正文、受众章节筛选、独立评估或任务准入逻辑。

阅读页恢复的定向验证：生产构建/typecheck、bundle 预算及五个相关单测文件通过；
Docker 浏览器九项通过，覆盖空稿/已有稿的材料分离、显式提交、手机阅读、Source、
下载、评估请求、版本绑定和失败重试。写操作由测试路由拦截，不会影响真实项目。
8001 狼人杀项目另做只读检查，Preview / Source 无材料表单，Synthesize 独立面板可见；
仍读到 49,292 字符及六种身份模板，记录到的写请求为零。
日志和实际页面截图为 `output/playwright/prd-preview-restore-*`、
`prd-preview-restored-live.png`、`prd-source-restored-live.png` 和 `prd-materials-separate-live.png`。

本次验证：六个相关前端单测文件、生产构建/typecheck 和 bundle 预算通过。Docker 浏览器
首轮 51 项中 49 项通过；包含安静群→主持人聊天→手动 PRD→继续补充、手机导航、失败候选稿
两条自由讨论流程。题卡初始滚动检查首轮失败，定向复验通过，保留其不稳定记录。
窄屏长 PRD 全屏工具栏检查在候选版及修改前 HEAD `34551f1b` 的独立构建中均失败，属于
已验证的既有问题；本次不宣称整个浏览器套件全绿。基线构建在隔离临时目录完成，未覆盖工作区源码。

已查看 8001 狼人杀实际页面的旧版导航、Discussion 和 PRD 抽屉截图，以及隔离测试的手机截图。
浏览器测试只使用 8033/8034 隔离环境，实际项目仅进行只读页面检查。日志与截图位于本地
`output/playwright/channel-ui-restore-*` 和 `channel-ui-baseline-*`。SceneProof CLI 仍不可用，
因此采用实际页面截图核对；未声称运行该 CLI。

## 验证证据

以下日志和截图保存在本地 `output/libre-discuss-implementation/`，不入 Git。

| 层级 | 范围及结果 |
| --- | --- |
| 关键合同回归 | 相关运行 67 项通过；候选只读、完整性及生成相关运行 33 项通过；最后合并复验 206 项通过，包含启动环境问题对应测试及接力/去重格式异常用例 |
| 前端 | typecheck、生产构建与 bundle 预算通过；最大 chunk 低于 500000 字节；新增题卡/身份单测通过 |
| Docker Playwright | 两条 UI 流程通过：真实 API + 模拟 Provider 的安静群→主持人聊天→空材料错误→手动完整 PRD→继续补充；读取夹具预置的失败候选，验证只读预览及正式产物隔离 |
| 视觉检查 | 已查看 1280×720 桌面 PRD、390×844 手机聊天、失败候选稿截图 |
| 真实模型去重样本 | 14 道合成问题，预期两组合并；实际两组，无额外合并，机械应用通过 |
| 仓库自动验证 | 广泛确定性测试 15113 通过、18 失败、20 跳过；serial 10、契约哨兵 53、模拟工作流 10 通过，前端 typecheck/unit 通过。18 项失败的处理见下文，原始 receipt 保留 failed 状态 |

全量失败中，6 项旧模式测试改为在创建时显式选择原模式，并等待贡献落盘后再请求综合；
保留原有业务断言。另 6 项本地 HTTP 测试在测试进程内加入 loopback 代理绕过后通过；
没有修改系统代理配置或相关产品代码。最终影响范围复验 **164 项全部通过**，包含
三个旧模式、原有 Task/Workflow 端到端、去重异常、长消息、投影、路由及本地 HTTP。
日志为 `final-affected-closure-confirmed.log`。这是修正后的专项闭环，不是重新跑一次全仓；
保留全量失败记录及上述基线问题，不将其改写为全量通过。

其余 **6 项基线失败仍保留**，因此不宣称全仓测试全绿：

- `test_export_public.py` 的 4 项依赖缺失的 `tools/export-public.sh`。
- `test_prompt_source_closure.py` 的 1 项依赖缺失的
  `prompt/2026-08-05-1559-kanban-agent-five-workflow-real-e2e.md`。
- `test_feishu_channel_progress_card.py::test_confirm_click_is_wired_to_real_controlled_action`
  的夹具未提供 readiness，确认被拒为 `readiness_unassessed`。加载原始 HEAD 的确认
  处理函数复现了相同拒绝；本批没有放宽该确认条件。

两个缺失文件在初始 HEAD 与工作目录中均不存在，证据保存在
`baseline-missing-assets.json`；飞书对照结果在 `baseline-feishu-confirm.json`。
本批不补写无关的对外发布脚本或历史 prompt。

真实模型样本覆盖等价改述、不同场景/能力、复合子问、相关但独立问题与少数意见。
这是有限语义样本，不保证以后模型不会误判；机械校验只保证结构、完整覆盖、权限和版本。
浏览器与原生正式 PRD 合同测试使用模拟 Provider，未执行完整真实模型 PRD→评估→开发
体验验收，也未宣称 36 条 AC 均完成真人或真实模型端到端验收。

## 运行与兼容边界

测试使用独立临时状态及 8031 端口，用户的 8001 项目/运行配置未修改。
没有把旧群原地迁移为新模式；新建一个自由群即可采用新交互。
源代码与 Web 生产资源已更新，既有 Web/Runtime 进程需重新启动后才加载新后端。
本批仅本地提交，不推送远端。

SceneProof CLI 未安装，因此使用它的截图核对纪律及实际浏览器截图，未宣称执行该 CLI。
Potpie daemon 不可用，未写图谱。没有以此跳过代码与原生调用入口验证。
