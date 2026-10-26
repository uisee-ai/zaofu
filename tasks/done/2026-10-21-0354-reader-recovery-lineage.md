> 状态: done

# P1 现场续接补充：Reader 因果身份与过时恢复请求

来源：[设计 183](../../docs/design/183-runtime-result-authority-and-recovery-closure.md)，[现场记录](../../docs/records/2026-10-21-loshu-recovery-checkpoint.md)。本项属于用户已授权的断点恢复与验证闭包。

## 现场复现

失败 `evt-e652eab0bdcf` 属于 API-RULES fanout `evt-5752824e`、目标 `4f9959e`。
`plan_reader_stage_replan` 从同 Run 最近请求拿到 ATTACHMENTS 的 task_id/candidate，混入 API 目标，产生错误续接 `evt-cfb24d2f9b03`。
已完成准入的 ADMINISTRATORS 仍被旧 triage 再次发起 replan；实际完成事件 fanout.child.completed 不在旧进展集合中。
另一个恢复 reader 因历史 busy 状态待派发，需走受控 restart，保留未提交证据。

## 验收

1. 锚定失败 fanout 的原始 trigger，不混用同 Run 的其他请求 -> verify: 同 stage 两任务交错事件回归，重试 briefing 也使用当前 manifest 因果身份。
2. 已准入 child 完成抑制该任务的旧恢复请求 -> verify: 有效完成、失败/无关任务的反例。
3. 受控纠正续接并恢复 reader -> verify: 旧错误事件保留，准确 task/commit 的新请求实际派发；dirty 证据不丢失。

## 实施与验证

两项失败用例先 RED，修复后 299 passed；包含 stage_failure_replan、fanout_hardening 的实际 retry caller、Run Manager/triage、controlled actions、runtime recovery、stale-contract/terminal-kanban mock E2E。sentinel 51 passed。
现场只读回放修正了原 API-RULES task/target/candidate 身份，随后完成新源码加载及受控实际派发，见下方完成依据。

## 完成依据

实现提交：`5faa91a fix: bind reader recovery to its causal task context`。
受控恢复已实际派发 API-RULES：`evt-937b48df4f18`；operation started `evt-5b13cfcb745e`，candidate/reader HEAD 均为 4f9959e。19 份旧证据在重新绑定前备份，并通过原子 sidecar writer 持久化和校验，事件 evt-938147b138fb 绑定 manifest。
本项完成的是恢复因果身份及实际派发；产品验收及 Q01–Q03 仍由独立现场验收任务跟踪。
