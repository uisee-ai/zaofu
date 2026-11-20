# 本地恢复能力与 GitHub dev 的集成核查

日期：2026-11-20。合并输入固定为本地 `06b30781` 与 GitHub `46e4c92e`；
共同祖先 `052e6a40`。本记录描述集成边界，不扩大为无人真实运行资格。

## 保留的能力与冲突处理

- Reader 的多跳工作记录、批量读取、续读见证与远端文本分页格式共同保留。
  批量分页逐页重新授权，文本和 JSON 均保留 cursor、完整输出见证及恢复原因。
- 保留本地 Task/Run/attempt、冻结候选、继承验证权的校验；将远端已验证替代任务
  的 currentness 核查移至本地已抽出的 `candidate_currentness`，不保留不可达旧实现。
- Plan 的执行范围与变更范围分别保留；可执行候选、Owner 待决草案继续使用各自
  既有准入语义。草案不伪造完整执行评审与成本。Plan Critic 的输入预算检查留在
  Critic 分支，普通 Planner 输入优化不读取不存在的 Critic 成本字段。
- 保留环境复用和流式调用生命周期账本，并在外层保留云端受管调用准入及结算。
  宿主 ready 通知同时保留配置投影与 watchdog；未部署实际宿主服务。
- 独立账号登录、云端授权与旧 token 路径共存；`candidate-continue` 和
  `task-host-assist` 仍要求明确 operator action token，普通写会话不能替代。
- Shell 写入守卫先识别有限的 literal cat，未识别时继续检查 literal apply_patch；
  不因提前返回漏掉实际 patch 目标，也不将文字报告中的路径误判为写入目标。
- Preview 保留分页、按需附件、绑定旅程和材料目录，同时合入原文默认入口、
  变更审阅、富 Markdown、图表及只读控制。已请求的同版正文不会因切换视图被丢弃；
  更换计划身份或关闭窗口仍取消请求。来源索引在 StrictMode effect replay 后可重启。
- 移除已被新 Inbox 实现承接的旧模块。将越过规模门槛的 Goal 引用解析及 Plan
  命令合同拆到各自职责模块，原入口继续调用，避免扩大豁免清单。

## 验证方法与限制

使用隔离 worktree 和独立依赖环境（dev、web、controller、stream-json extras）。
`dev-verify --base dev` 覆盖两边修改的模块、直接调用方、共享合同；删除模块及浏览器
fixture 等静态映射不足处显式补入现有入口测试，保留 sentinel 和 mock flow。
CLI 文档由合并后的 parser 重新生成，skill 分发副本与 canonical source 对齐。

首轮暴露预算分支位置错误、写入守卫提前返回、草案成本字段不完整、缺少 Controller
依赖，以及两边测试对默认视图、已固定引用、事件消费游标的前提差异。修复保留真实
验收责任：来源精确字节与摘要、故障归 Kernel、重放幂等、批准后禁止重复动作等。
源码关键字扫描改为 AST import 检查，配置中的 Provider 名称不再误报为 SDK 调用。

浏览器使用本机 `mcp/playwright:latest` 镜像、容器内 Chromium、独立端口与临时
模拟状态；执行真实业务断言并保留 trace、截图及 WebM。正式构建与开发模式分别
用于产品回归和 StrictMode 重放。每轮结果单独记录，不将失败或未运行层级计为通过。
最终测试数量、提交及证据清单保存于主 checkout 的 `reports/worktree-archives/`。
本轮不包含真实 Provider、新一轮 8×8、生产宿主接管或云端部署。

主 checkout 原有未提交文件、ignored Spec、历史 207–209 文档和嵌套项目保持独立；
与远端重号的历史文档按完整文件名区分，恢复其本地索引入口，不擅自重编号。
