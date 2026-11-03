# 跨计划版本 Critic 差量审查

> 状态: active

来源：设计 198、LS Run `prepare-TASK-90D92C-e4eaededbd2b74fe` 的全量阅读放大及用户授权。以 dev `e50cb492` 为基线，在独立 worktree 实施；不改写正在运行或已中断 attempt 的输入合同。

## 验收

- 历史审查证据 -> verify: 验证当前获准旧包、独立 Critic 准入、来源与完整交付证据；伪造、缺失、冲突及大账本均按范围受控处理。
- 新旧 Plan 与输入 -> verify: 对绑定的来源生成完整、可验证的差异和范围，保留共享义务、变更及独立结论；身份或覆盖不全时保持现有完整读取。
- 已审未变来源 -> verify: 只有新旧摘要、来源及先前完整审查证据满足合同才缩减新操作的输入；独立 Critic 仍读取当前差异、必要合同并可受控回读原文。
- 生命周期 -> verify: 真实 operation 派发在固定 manifest/读取策略前使用证明；旧 attempt 不变；当前业务基线与计划身份不能仅凭空 binding 或相同 provider session 认定一致。
- 质量与观测 -> verify: 用隔离真实形态的对照和篡改/会话轮换/全局变更反例验证，比较必读字节与原因；运行影响闭包、sentinel 和 mock E2E，记录真实 Provider 尚未验证。

## 隔离现场对照

- LS 已暂停状态副本：旧 Critic 账本、被观察页和 Plan Package 经过校验；12 个未变端口、4 个完整可重放 JSON 差异。原计划读取策略去重选区 14,232,866 bytes；模拟新策略去重原选区 3,573,707 bytes，证据 sidecar 原始 135,787 bytes。该计算不含输出封装和页面重渲染，不代表 token 或模型质量。模拟通过 `simulation.done` 结束并清理副本，未修改线上事件。
- 自由文本、大于预算、未证实或差异不划算的端口保持全读；新 Task Map 与原始需求仍完整读取。新操作 Critic 自主复核变化及其相互影响，不继承旧 verdict。真实 Provider 对照未运行。

## 验证

- `/home/min/workspace/public/zaofu/.venv/bin/python scripts/dev-verify.py plan --base dev` -> 334 个测试文件的保守影响闭包；自动 deterministic + serial + sentinel。
- `PYTHONPATH=src .../python scripts/dev-verify.py run --base dev --workers 4 --keep-going` -> 开发中的广回归 14,303 passed / 20 skipped / 7 failed，serial 10 passed，sentinel 53 passed；7 项均在干净 dev e50cb492 同命令逐项复现（Feishu、Channel mock E2E、4 个缺 export 脚本、缺 prompt）。此轮运行期间有后续代码修改，最终相关调用链另行重跑。
- 计划合同、独立 Critic 准入、worker 回放、来源快照/索引、runtime 直连调用和伪造反例（17 个测试模块）-> 最终代码 533 passed；差量、受限来源和旧 operation 重放 -> 19 passed。
- `bash scripts/dev-premerge-gate.sh` -> 53 passed；`bash scripts/run-flow-smoke.sh` -> 10 passed。
- 未执行真实 Provider 或生产 LS canary；此前材料副本对照只验证字节/证明，不证明模型语义质量。用户目前要求暂停 LS，未部署或恢复。
