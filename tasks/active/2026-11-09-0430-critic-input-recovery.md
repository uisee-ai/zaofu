> 状态: active

# Critic 输入范围、成本预检与恢复收敛

用户批准按本轮审查建议执行 P0/P1。基线 `21cbfc48`；独立工作区 `critic-recovery-20261109`。保留独立 Critic、原始输入、完整任务合同与全部验收责任。

- P0-1 重规划范围对账 -> verify: 报告指定任务与实际变更、共享/全局变化，不将未证明的局部变更默认为增量评审。
- P0-2 输入成本预检 -> verify: `zf.yaml` 配置去重正文/分页预算；超限在 provider 派发前明确失败且保存诊断，不能伪装为 Critic 语义否决。
- P0-3 失败状态收敛 -> verify: synth 超时/无结果终止后对应 operation 关闭，回放幂等，旧失败不能关闭新 attempt。
- P1-1 技术失败分类 -> verify: 输入超限、synth 无活动不消耗语义重规划额度，仍遵守有界技术恢复。
- P1-2 诊断与断点 -> verify: 操作者可只读查看评审模式、范围差异、体积、必读覆盖与最后读取时间，不借用 worker 凭据，不制造读取记录。
- P1-3 方法更新 -> verify: Planner/Critic 技能要求引用历史正文、准确范围证明、全量分区覆盖与最终全局检查；同步三份技能并通过现有技能检查。
- 运行态验证 -> verify: 基于修复代码关闭 datal 陈旧 Critic operation，受控签发新评审；保持已完成任务与活跃 Verify 目标，记录准入结果，不改 canonical JSON。

## 现有证据

本轮 48 个来源，可读取去重正文 27,330,729 字节，实际必读 30 条 / 19,682,377 字节，已覆盖 7 条；请求只指定 2 个任务，51 个任务记录中 43 个变化（41 个范围外），8 个全局字段变化。未提供增量范围证书。历史记录嵌入规格正文。两条 Critic operation 在 stage timeout/run blocked 后仍为 running。

## 验证层级

先新增真实入口回归并确认 RED，再实现 GREEN；运行影响闭包、`dev-premerge-gate.sh` 和相关 deterministic mock E2E。真实 datal 恢复另记录，不把 mock 结果当真实 provider 验收。

## 实现与验证记录

- 已实现 P0/P1 代码、CLI、PrdFlow YAML 配置和技能；运行态恢复尚待下节记录，当前不标记 done。
- 首轮 6 项真实入口回归 RED；修复后影响闭包 195 passed（含 CLI、操作收敛、输入预检、受控 reader 恢复），最终配置入口及相邻模块 70 passed。
- `scripts/dev-premerge-gate.sh`：53 passed，manual docs currentness ok。
- `scripts/run-flow-smoke.sh`：10 passed；串行锁测试：10 passed。
- `scripts/dev-verify.py run --base dev --tests tests/test_critic_input_recovery.py --workers 8 --keep-going`：广域确定性测试 15479 passed / 47 failed / 20 skipped。初次命令未将 venv/bin 放入 PATH，子进程找不到 python；新增 CLI 的生成文档随后已刷新。
- 修正 PATH 后，以相同 47 个失败 node 对照原始基线 `21cbfc48` 与修复分支：均为 23 passed / 24 failed，失败集合完全相同，无修复分支独有失败。剩余涉及已有 profile token 断言、锁重入、旧受控动作/重规划测试、wake 数量，以及基线缺失的 tools/prompt 文件。广域验证不能标全绿。
- 原始日志：`/tmp/zf-critic-{verify,baseline-compare,patched-compare,final-impact,flow-tests,sentinel,smoke}.log`；广域 receipt `/tmp/zf-critic-verify-receipt.json`。真实 provider 恢复与 mock 分开记录。

## 真实恢复（2026-11-09 05:25 UTC）

实现 commit：`57b435dd fix: bound critic inputs and reconcile terminal reader operations`。

- 控制面从本工作区启动，PID 3510487；`session.started evt-33aaf1883dcb` 声明 `control_plane_only=true/workers_preserved=true`。
- 两个陈旧 Critic operation 由实际恢复 sweep 关闭：`evt-5287dbba973b`、`evt-44a89f95451b`，没有手改 JSON。
- 受控动作请求 `evt-ad758c823547`，成功结果 `evt-bc7ea0571da1`；新 Plan trigger `evt-8ae1c0c1b0e0`，fanout `evt-31048f9e507e`，Planner dispatch `evt-404ca12964a0`。修复建议已绑定在必读 plan-rework-context 中。
- 保留已有工作；P16B-CONSUMER-WAITS-R1 自然通过 Verify、集成并完成（`evt-d25cd2f351d5`）；完成数 37。P11A 独立 Verify 继续；P16B-CORE-PRELOCK-R2 已恢复 Impl（`evt-a51c21567ebe`）。
- 发现旧 worker CLI 不支持新增 PrdFlow spec 键，改为显式完整 Plan stage 合同兼容配置；新旧 CLI 均可加载，stage 内容及其他配置对账不变，仅 stage 列表顺序变化。
- 原始证据：`/home/min/uisee/zaofu-echo/datal/diagnostics/critic-input-recovery-20261109/live-recovery-evidence.json`、`deployment.json`、`config-compatibility.json`、`recovery-result.json`。
- 尚未完成的验收：新 Planner 包的实际体积、独立 Critic 的完整读取与审查、Plan 准入结果。因此保持 active，不把运行恢复冒充产品或 Plan 验收通过。
