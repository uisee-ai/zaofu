# 媒体证据读取沿用已配置捕获上限

> 状态: active

隔离8×8授权修复。OA真实输入报告container media exceeds file limit；当前zf.yaml已配置单文件64MiB/总计128MiB，捕获成功的mobile-trace.zip约22.8MB，但materialize_container_media仍硬编码20MiB，导致整个只读媒体索引缺失。本项不扩大配置、执行权限或探针次数，不重跑浏览器。

- 捕获与投影使用同一已验证配置 -> verify: 实际Reader恢复调用接受配置范围内的大媒体，保持原receipt和内容digest，不执行新命令。
- 保持边界 -> verify: 单文件/总量超过配置、非法编码、坏digest、路径/符号链接仍拒绝；整份证据验证完成前不发布局部索引。
- 三个真实caller -> verify: Reader host handoff、OA语义输入、Candidate continuation均传入当前配置；诊断上下文呈现尺寸边界。
- 真实证据复核 -> verify: 在独立临时副本中对原宿主receipt完成全量digest/只读projection核验，原运行账本/源证据不变；部署后观察新输入能够访问媒体，不伪造产品通过。

实现及离线核验完成，部署后的新输入观察仍待完成，因此保持active。聚焦87 passed；自动影响闭包2212 passed/7 skipped，sentinel53、flow mock10。真实原证据副本旧代码RED（file limit），修复后9个文件全部digest/只读校验通过，原始receipt/evidence摘要不变，新增宿主执行0。证据：reports/eight-qualification-20261120T122929Z/media-limits-{focused.log,closure.log,retained-evidence-proof.json}（隔离验证worktree）。

期间首次聚焦命令因诊断测试文件名写错未执行任何测试；改为实际的test_host_recovery_diagnosis_context.py后通过。它属于验证命令错误，不计为产品失败。
