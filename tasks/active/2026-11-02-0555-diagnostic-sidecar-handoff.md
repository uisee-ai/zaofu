# Autoresearch 诊断引用与身份端到端传递

> 状态: active

## 证据

LS 的 `rmar-bd116d51bd21` 诊断种子中，`context_ref` 被转为 Python dict repr 字符串且 Run、failure_class、source_event_ids 未完整传入；原事件包含带摘要、访问范围和 required 字段的正式 sidecar 引用。该缺陷会妨碍按原始证据定位，但不能仅凭它解释此前 token 预算耗尽。

## 验收

1. Run Manager 请求经 invocation/accepted/trigger/loop/命令种子 -> verify: 精确 task、Run、失败类别、源事件 ID 及结构化 context/read-set 引用一致；sidecar 摘要、访问范围和 required 标记不丢失。
2. 诊断 seed 保持只读引用 -> verify: 按原 state_dir 和摘要 hydrate 原文件，非指定 payload 不泄漏；无效描述符不被伪造成有效 sidecar。
3. 原有字符串来源、单飞/提案及事件契约不回归 -> verify: 直接调用者、mock runtime、事件与恢复合同测试通过；不改变预算或原运行中的冻结输入。

## 实现与验证记录

- 对明确的 `sidecar-ref.v1`、64 位 SHA-256 和安全相对路径保留原描述符；使用既有 redaction，未 hydrate 或复制正文。Run/Task/trace/recovery case、故障类别及源事件按当前请求传递；旧字符串仍可定位，不伪装为可验摘要。
- 端到端函数链与真实 Reactor 调用测试覆盖 context/read-set 摘要、权限、retention、原 state_dir hydrate、篡改摘要、越界路径、未知格式和无关 payload；当前诊断冻结的旧 seed 不回写。
- `scripts/dev-verify.py run --base dev` 65 文件影响闭包 1248 passed / 7 skipped；新增篡改摘要断言后的直接调用者重跑 18 passed；premerge sentinel 53、flow smoke 10 passed。未做真实 provider 成功 E2E。
