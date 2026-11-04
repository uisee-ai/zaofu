# tmux 会话生命周期使用精确名称

> 状态: done
> 实现: `e650332a fix: target tmux sessions by exact name`

LS 启动时配置目标 `zf-ls`，tmux 按前缀把 `zf-ls-control...` 当成目标，
启动清理误杀启动者自身；保留原 Run 和所有现有会话。

1. 隔离复现前缀匹配 -> verify: 仅前缀相同的会话不能被认为是目标或被杀死。
2. 启动/停止与异常清理使用 tmux 精确会话身份 -> verify: 精确目标可清理，
   前缀相似会话存活；其他项目和同名以外会话不受影响。
3. 测试 mock、调用方和真实宿主 -> verify: 定向回归、premerge、mock flow 与
   唯一命名的真实 tmux 双会话测试通过，不触碰 LS 实际会话。
4. 合并 dev 后启动 LS、复核原 Run/checkpoint -> verify: 控制面保持运行，
   原输入和 attempt 未被错误替换，再按用户授权继续恢复。

## 已完成的验证

- 修复前 deterministic 复现前缀误杀；独立宿主 tmux 探针证明前缀匹配。
- mock CLI/transport/启动调用方 87 passed；真实 tmux 1 passed；sentinel 53 passed、
  mock flow 10 passed。完整 deterministic suite 未运行。
- 合并后 LS 控制面已启动并记录 `loop.started`，原 Run/operation 保留；
  后续发现的 Critic 恢复顺序缺陷由独立任务跟踪。
