# 设计 182 · 洛书重规划与综合验收

> 状态: active。公共修复已验证，新 Plan 生成与语义复审尚未完成。
> 设计：[182](../design/182-plan-review-task-slicing-and-evidence-closure.md)；[实施记录](../impl/plan182-review-and-evidence-closure.md)。

## 审查边界和入口

本次在 `/home/min/.codex/worktrees/9f60/zaofu`、分支
`wip/codex-2026-10-20-plan182` 实施。原项目的 Plan 和事件保留；新规划使用
`/home/min/workspace/loshu-plan182`、配置状态目录 `.zf-plan182`。

- [新项目 Inbox](http://10.9.100.141:8002/?project=loshu-plan182-eae8133e&page=inbox)
- [修复后的旧 Plan 完整 Preview](http://10.9.100.141:8002/?project=loshu-033ea7f8&page=inbox&plan=evt-642cfd6f51d0)
- [旧 Plan 的只读 API](http://10.9.100.141:8002/api/projects/loshu-033ea7f8/plans/evt-642cfd6f51d0/preview)

当前授权涵盖 ZaoFu 修复、重新规划和评估。业务代码、数据库迁移及发布未执行；
没有自动批准洛书产品 Plan。生成了方案、方案可执行、产品已通过是三个不同结论。

## 固定的来源分母

原稿 `/tmp/gm/loshu-fms-handoff.md` 共 5,082 行，内容 SHA-256 为
`582c21ea932e124daaa3041496f2a7064e817a24e6ec67b8b62e89716fcfc2ce`。
本轮沿用六行凭据脱敏后的 MD，SHA-256 为
`61dd290e611fda6260b5f769cc98f4c775d038ce29f6a175222bc405a9e89b7e`，不复制凭据。

| 范围 | 必须保留的含义 |
| --- | --- |
| 62 个主页面、6 类表单、80 个 ASCII | 清单完整只是结构证据；C01 六标签、D01 五标签必须继续细分追溯 |
| E01–E26 | 验收场景要绑定行为生产者、命令和最早可验证时点 |
| Q01–Q15 | 保持未决事实，按受影响阶段关闭；不能猜测答案或因尚未决定就删需求 |
| A18、Q05、E25/E26 | 核心与扩展条件分别保留；A01 默认 M4/C0，扩展验收后 M4/C1 互斥替换 |
| A27、Q08 | 条件范围不能默认启用，也不能连同核心 A26 删除 |
| 取消、Q15 | 现有取消态只读与新增取消命令分开；不能把取消等同物理删除 |
| RTK、Q07 | 核心账号功能保留，阈值决定单独标记 |
| PostgreSQL、59 表草案 | PostgreSQL 是已确认方向；草案与既有测试记录不等于本轮真实迁移或产品运行结果 |

原 Plan 有 26 个任务、13 个波次，2,260 个行级条目、300 个条款、82 个 AC、
81 条命令和 24 行浏览器矩阵。以上用于追溯和新旧映射，不作为目标任务数或功能通过率。

## 已验证的公共修复

| 检查 | 实际证据 | 判断 |
| --- | --- | --- |
| 完整正文与摘要 | 旧 Plan API 返回 45,491 字节，与绑定正文一致；页面单独展开摘要 | 已修复正文选择和引用交接 |
| 长文与附件 | Docker Chromium 在桌面和手机读到末尾及 Q15；12 个附件内容与绑定 digest 一致 | 真实页面链路通过 |
| 只读预览 | 浏览器未发起非 GET API 请求，前后审批仍 pending，无 JS 错误 | 没有因查看触发审批 |
| 切换 Plan | 延迟旧响应后关闭并打开新 Plan，旧响应不能覆盖新正文 | 真实 React/API 回归通过 |
| 超长正文 | 12,000 条中文/emoji 触发分页，逐页拼接与 API 全文逐字一致 | 正文保护与分页不会丢失内容 |
| 分层完成 | 实际 writer handoff 保留未来 mandatory AC blocked；当前失败仍拒绝 | 局部完成不冒充候选完成 |
| Verify 准入 | Runtime 固定当前阶段；普通和 typed 路径均检查必验项、owner 和未来签收 | 不能靠改报告 owner 或 waived 绕过 |
| 方法注入 | 实际 Planner 的工具读取事件及物化文件 hash 与 canonical skill 一致 | 证明已消费方法，尚不证明生成结果满足方法 |

测试：影响闭包 865 passed；sentinel 51 passed；确定性流程 smoke 10 passed；
新增 Docker 页面用例 4 passed；前端 build/typecheck 通过。自动扩大回归原有 38 个失败
已在原 dev 复现；手机 contract-health 表格溢出同样在原 dev 复现。完整原始结果和失败分类
见实施记录，不能把本次聚焦通过写成全仓全绿。

## 新 Plan 的待完成评审

新工作流 `loshu-plan182-review` 已通过正式 flow intake/submit，两个扫描已完成。
首次输入准备遗漏的 24 个图片/数据库配套文件已经补齐，但扫描运行在较早 checkout。
正式评审必须验证 Planner/Critic 对补充来源的真实消费；必要时通过受控拒绝和返工修订。

后续需填入正式 Plan/Package 身份、来源与跨矩阵闭包、新旧任务映射、F02/F03/F05/F06
反例复审、条件范围、TDD/SDD 判断及新 Preview 浏览器结果。完成前不归档 B03/B04/B06/B07，
不声称新 Plan 已完全覆盖或可无条件派发。
