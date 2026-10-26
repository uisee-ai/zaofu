> 状态: done；3b7d25fb fix: stabilize approved light workflow startup

# 185 · 已支持路线的可靠性收尾

Owner于2026-10-24批准按三步建议执行：清单对账、现有恢复链/验证闭包、双入口简单任务到现有light。
基线5c2e8266；沿用184 worktree，前三处未提交文档为本session取消native能力的范围修改。

- [x] R1 清单对账 -> verify: PRD/Research成功与历史失败分开；取消native、Kimi暂停；热配置/跨Provider/完整通用恢复框架延期且有具体触发条件；不把取消记为实现完成。
- [x] R2 映射补齐 -> verify: 复现当前dev-verify的6项未映射，建立实际调用/行为测试或精确共享边界；plan不再有未映射错误，未知新路径仍拒绝。
- [x] R3 恢复可靠性 -> verify: dirty writer、reader证据/兄弟复用、gap版本、重启终态/不重派发、累计用量分别有当前回归和历史真实证据；复现缺陷再修复，未发现缺陷不新增机制。
- [x] R4 双入口到light -> verify: Agent方法明确适用/不适用与澄清；Kanban/Channel来源权限分离，精确预览/批准后走现有light单writer和独立验收；未批准、缺输入、旧来源拒绝；不增加关键词路由器或独立single内核。
- [x] R5 证据与收尾 -> verify: 影响闭包、sentinel、相关mock、必要真实Provider/浏览器分层记录；引用既有完整真实交付，不伪称旧证据在新版本运行；仅提交自有改动，不push/merge，模拟完成归档清理。

延期：运行中配置换代（出现已确认必须不中断Run换配置的需求）；跨Provider（明确第二Provider和切换目标）；通用权限/外部副作用恢复包（新增外部写入或跨权限迁移）。
强制只读隔离仍未验证：不可信/多租户/明确强制只读承诺前必须准入，当前不得宣称具备。
证据根：/home/min/workspace/public/zaofu-evidence/185-reliability-20261024T120651Z/。

实施与分层验收见[本批记录](../../docs/impl/185-supported-route-reliability.md)。R4真实路由4/4和两入口建议准入已通过；R5验证、证据归档与临时清理完成，实现已提交并按提交号归档。

本批修复：精确批准重放被预检误拒绝；文档投影误使批准摘要过期；Channel来源损坏/缺失导致未捕获异常。全部有先失败后通过证据。当前范围完成不表示185原始扩展全部实现，也不表示813文件的整分支闭包已运行。

最终签收：恢复166项；Agent方法112 passed/1 skipped；工具/合同101 passed/1 skipped；入口47项及最终输入边界28项；哨兵51项、三流mock10项、自动串行10项、Web typecheck/unit通过。组间有交集。真实Codex路由4/4；两份真实建议经精确批准到Task Map并验证排队续行。临时状态121文件归档校验后清理，两个探针Run受控取消。未跑全pytest或新浏览器，不替代历史完整交付证据；整个184分支验证规划813文件尚未全部执行。
