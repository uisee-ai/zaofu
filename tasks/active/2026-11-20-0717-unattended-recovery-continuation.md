> 状态: active

# 无人恢复续批：语义接力、等待、外部监管与效果

来源：用户确认继续上一轮 P0 优先项；ZF-SPEC-004 UE-03/04/05/07/09。
在当前隔离 worktree 实施，不启动新版真实 8×8，不探测真实账户，不安装宿主服务。
实际探测接口/预算与部署范围保持待决；先验证机制、授权边界和 mock 故障。

1. Reader 新 operation 接力 -> verify: 真实 runtime 重试入口冻结前驱记录，
   当前凭据分页可读；跨 child、篡改、旧目标及终态拒绝；记录不代替必读/验收。
2. 运行中外部额度等待 -> verify: 持久等待可重放，恢复判断有界且显式授权，
   恢复遵守暂停/取消，不能以等待结束冒称原任务完成。
3. 独立控制面监管 -> verify: mock 主循环活锁与内部 watchdog 共因故障，
   独立入口能判定与接管，精确 owner fencing；正常长任务不因耗时被误杀。
4. 恢复效果/重复故障 -> verify: 动作回执与原工作进展分离，控制器重建后
   累计无进展停止条件保留，不通过新 attempt 清零。

按现有事件、Store、受控动作和 handoff 载体扩展，不引入第二状态机。
完成后执行影响闭包、premerge sentinel 与确定性 mock，记录具体结果并本地提交。
更大语义重划、自主补丁实际部署、综合 UI 和真实无人资格另按对应范围核验。

## 本轮实施对账

- 第 1 项：已实现同角色、同 child 的直接前驱语义记录固定、当前授权读取和重放。
  回归覆盖损坏引用、跨角色/child/目标、旧 operation、分页摘要和输出上限。
  后续 `2026-11-20-0808-unattended-lineage-and-effect.md` 补齐同角色同 child
  连续中断的固定记录继承；任意祖先检索或跨角色授权转交仍不在完成声明内。
- 第 2 项：已修复额度错误误入普通重试、共享锚点 Task 导致 Reader 等待事实漏记、
  cooldown 覆盖 exhausted 展示。主动探测及自动恢复尚未实现，保持 active。
  继续条件：确认 Provider/账户/模型对应的探测接口、调用效果、预算与授权范围
  （Spec §9 原有未决项）。当前不以缺少报错、等待超时或进程在线作为恢复证据。
- 第 3 项：配置、unit 生成、主循环完成通知和 PID/epoch 拒绝均已实现并做 mock；
  未安装 unit，也未做真实冻结/接管资格。继续条件：指定部署节点/单元、适用 watchdog
  周期及宿主故障验证范围；不得把当前协议测试写成已部署或真实接管通过。
- 第 4 项：已修复跨 Run/sibling 进展污染累计停止条件，新增 Task+Run worker
  重启后原工作准入效果核验，包括快速结果早于 respawn 回执到达的竞态。
  其他动作族和跨部署完整故障矩阵仍需单独对账，不借本项结果概括全覆盖。
  后续 0808 任务补齐 task-requeue 的原工作效果与快结果重放，及已知额度耗尽
  期间 RM resident 诊断等待；OA/AR 全部入口和长期在途等待矩阵仍未完整验收。

专项 RED → GREEN：Reader 接力、额度分类/等待、host watchdog、恢复范围匹配、
worker 原工作效果。本综合任务保留 active，
不因部分源码完成而归档为整个无人恢复能力 done。

## 验证记录

环境：当前 worktree，PATH 使用主仓库共享 venv，PYTHONPATH=src 固定本分支源码。

- `python scripts/dev-verify.py plan --base dev`：1026 个测试文件的影响闭包；
  涵盖 config、provider/host、artifact handoff、orchestration 的直接调用和合同。
- `python scripts/dev-verify.py run --base dev --workers 8 --receipt /tmp/zf-unattended-continuation-final-verification.json`
  -> passed；确定性测试 **12566 passed, 18 skipped**（261.15 秒）。
- 同一验证器执行 `bash scripts/dev-premerge-gate.sh` -> **53 passed**；
  `bash scripts/run-flow-smoke.sh` -> **10 passed**，所有已运行步骤退出码 0。
- 显式层 real-provider 为 not_run；未执行真实 Provider、实际 systemd 接管或新版 8×8。
- 首轮为 3 failed / 12562 passed / 18 skipped。两项 Reader 可选历史输入兼容
  回归已修复；飞书夹具在 dev d442a9bd 同样复现未评估 readiness 却期待确认成功，
  已仅修夹具并补拒绝对照。完整影响闭包重跑通过，没有跳过失败用例。
- `git diff --check` 通过；只提交本批列出的路径，保留主仓库原有未提交内容。

本地提交完成后以该任务文件的 Git 历史定位实现；没有合并或推送授权，不执行 merge/push。
