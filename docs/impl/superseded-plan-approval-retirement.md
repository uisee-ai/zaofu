# 已替换计划的审批等待退休

`plan_approval_replay.pending_plan_requests` 曾只消化 approved/rejected 与相同
内容的重复批准。LS-v1 已有明确包替换事实的6条历史请求仍显示 pending，导致
`has_pending_plan_approval` 一直为真。它被 CLI、goal idle driver 与 Run Manager
failure-closeout 使用；正常在途Task调度仍继续，但后续空闲恢复可能误等审批。

投影现在同时消费 Kernel 的包替换记录。只有同Run、精确旧包ref/digest，并且
后继包已在同slot、同cause获Kernel admitted，才移出旧请求；不按时间新旧或任意
新计划猜测。来源、身份、准入证据缺失时保留等待。迟到的旧请求不会复活；后续
显式重新准入同一不可变包时，允许对当前请求再次审核。

锁内 `decide_pending_plan` 使用同一 pending 投影，过期 approve/reject 不落
事件，不意外触发 replan。此规则不自动批准新包；新包待审批请求仍阻止空闲
推进。历史事件保留，Task状态、Owner问题和业务验收均不改变。

测试 `tests/test_superseded_plan_approval.py` 覆盖 CLI、真实决定入口及 idle
driver；跨Run、错误摘要/路径/slot、非Kernel替换、不同cause和缺少准入均不能
消除请求。真实LS回放证据在其 `docs/records/2026-11-08-plan-approval-retirement/`。
