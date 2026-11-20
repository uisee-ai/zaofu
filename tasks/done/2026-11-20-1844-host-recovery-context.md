# Run Manager 宿主验证恢复可见性

> 状态: done — `5a0a9682` fix: preserve candidate host recovery ownership and evidence

用户授权：8×8真实验证发现问题后修复、评估并断点恢复。真实案卷 rmd-dc3f1b95364788d1 未包含已启用的宿主验证策略；Agent只见一次非浏览器收据，18:42:20建议人工修环境，18:42:33生成evt-9360c775d27d。实际同一Run的Docker队列已完成13/19并继续执行。这证明诊断输入缺口，不证明Agent看见完整输入后一定选择正确。

- 案卷呈现同Run的宿主预授权声明与已发生执行事实 -> verify: 真实resident dispatch入口产出的sidecar包含enabled/approval/allowlist、开始/完成事件、绑定收据；异Run及非可信自报不混入。
- 保存未知和故障证据 -> verify: 缺失或损坏收据可见为unavailable，不能计算为成功或阻止诊断请求。
- 方法指引区分策略声明、执行中、命令通过、产品验收 -> verify: Agent在升级前核对宿主队列和当前授权；等待有owner/缺失命令/唤醒条件，不据单份非浏览器收据认定没有恢复动作。
- 不改Agent决策权限或自动解除human gate -> verify: 既有RM准入和human gate测试保持；仅修改诊断材料及方法，不动运行态事实。
- 测试后提交，窗口外部署 -> verify: 聚焦、影响闭包、哨兵及相关mock E2E通过。新真实Agent判断单独记录，不能由单测推断无人验收通过。

验证：实际入口回归6红转绿；owner乱序回归1红/9绿转全绿。合并聚焦121项通过，补充owner未知结果/乱序与限制50项通过（有重叠）。影响闭包首跑13,210 passed、17 skipped，因测试环境缺少controller依赖有13收集错误和1失败；使用依赖齐全的实验venv对14个受影响文件补跑182 passed。Web typecheck/unit、53哨兵、10流程、2项真实Docker执行器检查通过。重复后端验证因错误tier名被操作者停止，已用正确deterministic tier完成剩余检查，未停止运行态进程。当前代码尚待窗口外部署，真实接续结论单独记录。

真实接续：19:44实际resident请求rmd-2d226c8e1e519f24已包含enabled策略、19命令、83条同Run宿主动作及收据状态。19:49:08建议evt-5cbcd7d22d5d识别原生队列，选择有owner/缺失项/唤醒条件的wait，随后runtime消费，未要求重复人工授权。证明本次输入和判断闭环，不推断未来诊断全部正确。证据reports/eight-qualification-20261120T122929Z/recovery3-host-diagnosis-context-proof.json及recovery3-host-diagnosis-outcome.json。
