# 手动重开 Critic 时提供安全续读入口

> 状态: active

LS 真实操作的 `worker.recovery.injected` 已送原 briefing，但该实际入口未附带
同 session 的按需续读指引；压缩后历史回执不能代替当前正文。

1. 手动失败恢复、回收及普通 reader 恢复提供按身份绑定的指导 -> verify: Codex
   使用当前 attempt 的 `--required --resume`，说明仅在正文仍可用时使用；Claude
   不获得其不支持的指令，未知 attempt 安全回退。
2. 原计划/权限与输出证据不变 -> verify: 实际派发入口、mock 负例及读回归通过。
3. 结合当前 LS 运行实际验证 -> verify: 如本次 briefing 已发出则明确区分代码
   完成与真实生效，不为补提示而重复终止活动 provider。

## 当前验证

修复前 Codex 与 Claude 两种真实入口回归均失败；修复后调用方闭包 189 passed、
sentinel 53 passed、mock flow 10 passed。当前 LS 的简报已在合并前发出，尚不能
称该次使用了新提示；等待下一次自然重派或其他真实验证。
