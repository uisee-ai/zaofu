# 引用编辑使用完整 Preview 阅读区域

引用编辑原先受 `.plan-bound-document` 的 55dvh 上限影响，外层还有滚动区域。1440×1000 的浏览器回归复现了正文底部距可用区域底部 238px 的空白。

## 实现

仅调整 Inbox 全屏编辑范围内的布局：编辑区采用可收缩的纵向 flex 容器，正文填满剩余高度，取消该处的 55dvh 上限；外层沿用 Preview 的隐藏溢出规则，正文独立滚动。文档选择与工具栏不收缩，Agent 保持工具栏下方的浮层，不挤占正文。未修改 API 或审批行为。

## 验证

- 生产构建、bundle budget、定向 `dev-verify.py` 的前端 typecheck 与单元测试通过。
- Docker Playwright：1440×1000、1440×1400、390×844 三种尺寸验证模式切换前后阅读区域相同、正文填满剩余空间、可滚动至末尾、Raw 保持高度、Agent 不压缩正文且页面无横向溢出。
- 引用修改回归使用真实双击选词触发入口，模拟 Provider 候选及 Apply，验证 Diff、审批锁定和退回后状态。上述四项最终通过，原 Preview 的桌面/手机布局两项也通过。
- 首轮新断言漏计手机阅读区的 1px 底边框，已修正；原有程序注入选区的操作未出现引用入口，改为原生双击选词后通过。失败日志保留，不把重跑结果当作首轮通过。
- 8002 实际 `ty-review-42cb2551` / `evt-38c86009183e` 在本次验证开始时已经是 approved。未修改审批状态，仅验证真实文档布局、Agent 弹层及已处理版本的禁用保护。1000px 高窗口的编辑正文高 802px，Preview 正文高 793px；1400px 高窗口分别为 1202px 和 1193px。三个尺寸均无未利用的正文底部空间（手机差 1px 为边框），无写操作。

证据：[修复前回归](2026-10-27-plan-edit-reader/before.log)、[首轮回归](2026-10-27-plan-edit-reader/after.log)、[最终引用编辑回归](2026-10-27-plan-edit-reader/agent-final.log)、[真实页面结果](2026-10-27-plan-edit-reader/result.json)、[真实页面脚本](2026-10-27-plan-edit-reader/live.mjs)、[桌面](2026-10-27-plan-edit-reader/1440x1000-editing.png)、[手机](2026-10-27-plan-edit-reader/390x844-editing.png)、[构建](2026-10-27-plan-edit-reader/build.log)、[前端检查](2026-10-27-plan-edit-reader/verification.log)。

8002 静态资源已更新。8003 夹具在验证后记录 `simulation.done`、停止并清理；本轮未提交、合并或运行后端全量测试。
