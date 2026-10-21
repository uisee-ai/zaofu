> 状态: active

# P1 现场补充：Autoresearch 隔离 Flow 输入暂存

来源：[设计 183](../../docs/design/183-runtime-result-authority-and-recovery-closure.md)，用户已授权的诊断环境修复及断点验证闭包。

真实诊断 rmar-7214d8508c92 已通过技能校验，但生成 docs/prd/autoresearch.md 后被源码仓库 docs/ ignore 规则阻止。inner runner 返回 3，尚未派发，candidate 保持 unverified（evt-9d5b3eccc862），未误报排除故障。

## 验收

1. 保证显式生成的 Flow 来源能在忽略 docs/ 的隔离 checkout 提交 -> verify: 同一真实 git 测试覆盖忽略/不忽略目录，先 RED 后 GREEN。
2. 保留并隔离其他会话暂存内容 -> verify: unrelated.txt 留在 index、不进入输入提交。
3. 通过调用方回归与隔离真实 git 复现 -> verify: runner、autoresearch caller 聚焦测试，以及现有失败诊断 worktree 上的只针对输入 prepare 验证；不借此声称产品或完整诊断完成。

## 验证证据

- RED：docs/ 忽略场景失败，非忽略场景通过；日志 /tmp/zf-autoresearch-ignored-input-red.log。
- GREEN：runner 与 Autoresearch 直接调用方 66 passed；另 sentinel 51 passed。
- 真实隔离失败 checkout 上仅重做输入准备成功，提交 0f420aa 只包含 docs/prd/autoresearch.md；正文完全一致、原 dirty zf.yaml SHA256 未变。没有运行新的业务任务或宣称完整诊断通过。
