# 重复激活导致 reader 被误回收

> 状态: done

实现：`ec0ac08c fix: preserve reader epochs across repeated run activation`。

用户2026-11-07授权持续监工、修复卡顿并记录。本任务来自现场新发现，非扩大产品范围。

## 根因

Planner `evt-b0f3d36451ca`在02:00:19派发；02:00:20重复goal active重写此前blocked
的恢复索引，将该活跃reader反向判为旧终局代次。02:10:21 warning记录idle=true，
context=0.7206，随后fresh recycle并重新读briefing，原规划进度未落盘。

## 最小修复与验收

- 同一blocked代次只记录首次active/running -> verify: 重复激活保留当前reader、跨角色不误认。
- 新blocked与hard terminal保留 -> verify: 新阻塞需新恢复，completed/cancelled不被active复活。
- 真实生命周期入口 -> verify: parent backlog+活跃reader不走idle recycle，终结后允许回收，保留busy/hardcap规则。
- 原历史固定窗口重放 -> verify: 同一事件序列旧代码null/terminal，新代码原reader/active。

RED 3 failed/35 passed；初步GREEN76 passed。最终focused/自动tier/mock/sentinel证据待归档。
关联产品监工总记录：`tasks/active/2026-11-07-0200-two-gap-supervision.md`。

最终验证：focused 76 passed；`dev-verify plan/run --base dev --paths <本批源码及两份测试>`自动4文件73 passed；直接调用闭包及生命周期mock E2E 114 passed；sentinel 50 passed/1 skipped。未执行全量或真实provider E2E。

固定历史窗口before/after回放与主checkout `git apply --check`通过。精确patch和日志在 `/tmp/zf-supervision-20261107/reader-epoch-result.json`。该修复不能找回已经被回收的会话内容，只阻止同一问题再次发生。
