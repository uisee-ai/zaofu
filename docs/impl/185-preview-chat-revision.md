# 185 · Workflow Preview 引用讨论与局部修订

关联：[185 设计](../design/185-agent-routing-delegation-and-workflow-recovery.md)、[实施任务](../../tasks/done/2026-10-24-1423-185-preview-chat-revision.md)。

## 已接入入口

独立 Workflows Preview 页复用 `KanbanHeadlessAgent`，默认 Codex headless/read_only。
页面可选择讨论字段，也可鼠标或键盘选中文字，通过浮动“引用讨论 / 修改”进入聊天。
引用保存字段、原文、Unicode 字符位置和请求 revision/proposal digest；服务端重新读取规范内容，
拒绝伪造、越界、跨字段、歧义或过期引用。浏览器提供的内容不成为规范计划。

```text
Preview rN -> 选中文字 / 选择字段 -> Chat Agent
                |                     |
                |                纯讨论：保持 rN
                |                     |
                +-------------- 局部 replacement
                                      |
                              before / after 差异
                                      |
                         Owner 应用修改（不启动）
                                      |
                   既有 Request revision / 编译 / 预检
                                      |
                               Preview rN+1
                                      |
                    配置变化：既有 Apply config 路径
                                      |
                           既有 Approve & Run
```

模型只返回一个选定字段的 replacement；引用模式只替换所选片段，其他字符由确定性代码保留。
未选字段不接收模型输出。纯讨论即使返回 replacement 也不产生修改草稿。
草稿以不可变 sidecar 保存并绑定摘要；应用前重新核对版本、配置摘要与可编辑范围。
同一草稿重放返回既有回执；编译阶段中断可在同一次 revision 上续行。
讨论/草稿待确认期间阻止当前卡片启动，避免把旧版按钮当作新版批准。

## 修改范围与授权边界

- 独立 Request：目标及已有验收/约束条目，可局部文字修订。
- Task / Channel 绑定的需求：允许引用讨论；需求变更必须回到规范 Task/PRD 修订，不能绕过来源授权。
- 当前控制器已有的 lanes、strictness、backend、verifyBackend、topology 标量字段：生成配置候选及新版 Preview；不直接覆盖 zf.yaml。具体效果仍由已注册 profile 决定，例如 light 固定单 lane。
- 已存在的配置候选和 Synthesis 角色、profile、完成合同继续保留。不会以默认流程替换 Research 等已注册路线。
- 任意 DAG/角色新增、预算扩展、运行中 Task Map 编辑不属于局部补丁；使用原受控配置或重规划路径。
- 已启动/终态请求拒绝本入口。Provider 只做建议，不能自行批准或写任务状态。

新 API：`POST /api/projects/{project_id}/workflow-preview-review`，operation 为 context/discuss/apply。
沿用 Web token gate；应用使用 workflow-intake 授权。服务端通过现有 Request/Synthesis/Proposal 服务写事件和不可变工件。
与受控 Workflow start/submit/request/reject 串行化，未新增第二执行状态机。

## 验证

验证记录在 `/home/min/workspace/public/zaofu-evidence/185-preview-review-20261024T1423/`（签收时归档）。
覆盖：纯问答、精确引用、局部保留、旧版本/旧批准拒绝、重复应用、晚到响应、编译中断后续行、
Channel 来源保护、配置候选不覆盖 YAML、Research 合同保留、新版经原 submit 准入。
Docker 浏览器使用隔离 API 和确定性 provider，实际操作页面/引用/差异/新版，不冒称真实模型语义质量验证。

### 签收结果

- 新增接口/补丁回归：17 passed；调用闭包（Request 与 Web proposal）31 passed，增量无效配置接口与 headless 事件路由分别单独回归。
- 本批影响规划：107 个文件，7 个新进程分片。原始结果保留：1586 passed、1 failed、2 skipped、1 deselected；失败为既有 Channel 四角色讨论的 3 秒时序窗口，单独复跑 1 passed，未修改该运行时。
- 第 5 分片包装器在 600 秒超时，底层 pytest 仍在运行；监工接续观察并记录最终 312 passed / 1 deselected（860.49 秒）。这是测试基础设施超预算记录，不将原 timeout 改写成 exit 0；该进程已退出。
- 自动层 serial 10 passed；Web typecheck、最终 build/bundle budget、unit 全通过；最终 premerge sentinel 51 passed。
- Docker Playwright 1 passed：真实 UI/API、确定性 provider，选中文字、浮动引用入口、讨论、局部 diff、独立应用、保留另一条验收、新版待批准、390px 布局。
- 本轮没有真实模型质量验证、完整全仓 pytest 或运行中的 Task Map 修改验收。

修复记录：预检传入 intake 与新版 manifest 的正确位置；Synthesis 重新准入后刷新当前 Request 再编译；审核处理中暂停详情轮询，避免暴露中间 ready 版本并丢失草稿；ConfigError 转受控 422。浏览器用例使用解析后的项目身份，避免 default 别名切换被正确的跨项目保护丢弃；未把 UI 的备用 run_id 显示字段当作实际启动证据。

证据保留初始失败与重跑日志、浏览器截图/trace、测试分片清单和超时接续回执。模拟服务已停止，simulation.done 已写入测试账本。
