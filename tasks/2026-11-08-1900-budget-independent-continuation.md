# 无预算长任务的续接记录方法修复

> 状态: active

用户已授权持续监工、修复并记录；本项来自 ls-v1 FMS13/16/17 实际运行，无新增预算、全量重规划或状态写权限。

## 证据与范围

FMS13 operation wop-impl-tp-FMS-13-GAP-E2E-PROBE-01-997ff-307cb437c728 运行约2.5小时、多次压缩。现场 cost 显示所有 limits=0、warning_level=0；Task Pipeline 简报把续接笔记的明确触发放在 budget warning>=50，角色方法也以50/80表述。既有一般“维护续跑说明”并未提供无预算长任务的进度触发。并非证明provider丢失了原文，亦不改读取/准入门。

## 实施与验收

- 将续接记录触发独立于预算，按已完成检查组、提交/阶段切换和新未决项增量维护 -> verify: 实际 Task Pipeline impl/verify 初始/返工派发简报均携带方法，不靠 warning 事件。
- Dev 在现有允许目录记录；Verify 仅使用当前授权scratch/既有字段和持久化工具证据，不扩写产品目录 -> verify: 角色技能与简报一致，副本摘要一致，原只读/身份/必读规则保留。
- 更新已有work record说明，不创建第二份运行状态 -> verify: 原work record CLI和budget status测试通过，199恢复mock仍通过。
- 合并dev、将当前worker所需方法通过受控提示投递 -> verify: event/operation/attempt绑定，记录真实消费；不为更新方法重启全部任务。

记录位置：ls-v1/docs/records/2026-11-08-budget-independent-continuation/。本修复只改方法指导，不能证明全量输入加载或Watcher总CPU问题已解决。

## 已执行验证

实际派发入口先8失败/10通过；修复后39定向通过。dev-verify 20文件闭包203通过/1跳过，包含199恢复mock及work record/budget合同；哨兵53通过。技能用仓内read_skill_metadata校验（支持本仓stages/dependencies等frontmatter），两Provider副本逐字节一致。未跑全套或真实Provider E2E。

验证命令纠正：首次plan误用--format json，改为--json；一次在plan进程结束前读取输出导致JSONDecodeError，等待完成后正确读取。对名为serial的E2E误用-m serial得到2 deselected/exit5；该文件无serial标记，已纳入上述闭包并通过，不计作独立串行通过。上述均为operator命令问题，非产品回归。
