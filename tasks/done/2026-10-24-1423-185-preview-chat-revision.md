> 状态: done

# 185 · Preview 引用讨论与局部修订

来源：Owner 授权“生成 backlog，这部分也实现”，并要求划线引用修改。
基线：184 worktree / 0cfb25b0。复用现有 headless Chat Agent、不可变工件、请求版本与审批服务。

- [x] P1 Preview 讨论入口 -> verify: 精确绑定 Request revision/proposal digest；纯问答不改变计划、不启动 Run。
- [x] P2 划线引用 -> verify: 引用携带字段、原文及当前版本；跨段/过期/伪造引用拒绝；键盘选择可用。
- [x] P3 局部补丁与差异 -> verify: 仅修改显式选定字段，逐项 before/after，未选字段保持；不让模型重写完整计划。
- [x] P4 受控应用 -> verify: 新版 Preview 单独批准；过期、已启动、重复应用、晚到响应安全；来源/Task/PRD 不被旁路修改。
- [x] P5 验证归档 -> verify: API/调用闭包、前端构建与 Docker 浏览器路径、哨兵、相关 mock E2E；记录精确边界并本地提交。

范围：启动前审核。允许当前控制器已有配置标量微调，生成配置候选并单独批准；独立 Request 的需求字段通过原 revision 服务修订。Task/Channel 绑定的需求变更须回到规范来源修订，不在 Preview 改写；运行中 Task Map 使用现有重规划流程。引用可以讨论任意可读段落；不支持的结构/配置修改明确提示受控配置路径。


实施与验证：[Preview 引用修订](../../docs/impl/185-preview-chat-revision.md)。
新增 17 项通过；Request/Web 调用闭包 31 项通过；107 文件影响闭包已完成，历史时序失败单独通过，保留一项包装器超时与监工接续 312 passed 回执。serial 10 passed、Web build/unit、sentinel 51 passed、Docker 浏览器 1 passed。Provider 为确定性 mock，不冒称真实 Codex 语义验收。

实现提交：`b5b6b078 feat: support quoted workflow preview revisions`。
证据：`/home/min/workspace/public/zaofu-evidence/185-preview-review-20261024T1423/`，186 个文件 SHA256 清单。仅本地提交，未 push/merge。
