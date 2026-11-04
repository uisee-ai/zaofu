# Critic 断点恢复与失联判断顺序

> 状态: done
> 实现: `824e5643 fix: fence suspended synth recovery before retry`

LS 原 Run 从 paused 恢复后，Critic operation 仍为 suspended、尚未应用
checkpoint 时，旧派发的历史失联计数与新 worker launch 触发终态失败；随后
checkpoint 虽被接纳但 fanout 已失败，未实际重派。保留原请求及失败证据。

1. 模拟先恢复 Run 后应用 checkpoint 的顺序及已有失联记录 -> verify: suspended
   operation 不产生新的失联或失败；无效/无关决定不能解除保护。
2. 确切原 operation 的受控 redrive -> verify: 只重新计算该代有界恢复次数，
   原请求/attempt 不变，实际派发后才标记 started，恢复后失败仍有界。
3. 对已发生的无语义 transport 失败执行精确修复与重派 -> verify: 绑定失败、
   operation/request/attempt、恢复事件及角色，拒绝语义结果和不匹配身份。
4. 运行直接调用与事件合同闭包、模拟及真实 LS 恢复 -> verify: 新代码在原 Run
   显示真实 dispatch 和 provider 读取，不把接纳 checkpoint 当成任务完成。

## 当前验证

修复前两个恢复顺序均复现错误失败；修复后 reader、operation、CLI 重启及
准入闭包 237 项通过，sentinel 53 项和 mock flow 10 项通过。错误 attempt、
不可信 origin 和错误 recovery owner 的负例均拒绝。真实 LS 已经部署验证，详见下方记录。

## 真实 LS 复验

原失败事件 `evt-9c9e4328415a` 经精确重开，原 operation 与 request hash 保持；
`evt-6b14071cf34f` started、`evt-8d7a9f94b5a9` briefing 已注入。原 session
压缩后真实读取从 `test_matrix.json` 旧 page_end 262144 连续继续，最近三页
输出在 transcript 中观察到；仍需独立 Critic 通过与全量语义覆盖验证。
