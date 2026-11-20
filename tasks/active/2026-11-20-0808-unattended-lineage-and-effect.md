> 状态: active

# 无人恢复续批：多跳接力与重新入队效果

来源：用户批准上一轮两项确定代码缺口及 Provider 共因故障 mock 验证。
对齐 ZF-SPEC-004 UE-04/05/09/10；原综合任务见
[无人恢复续批](2026-11-20-0717-unattended-recovery-continuation.md)。

1. Reader A 写记录、B 接力后未写新记录即退出、C 再接力 -> verify:
   实际 runtime 重试入口保留 A 的不可变记录；不展开无限历史；篡改拒绝，
   新记录优先，旧身份和跨 scope 不获得新授权。
2. task-requeue 受控执行 -> verify: 回执仅进入 verifying；原 Task/Run 的
   kernel 准入工作进展后关闭 case；错误身份、失败结果、重放不提前关闭；
   人工批准仍保留原 Run/case，快结果不因先于 effect.pending 而丢失。
3. 共同 Provider 故障 -> verify: mock 检查机械监督仍有进展、语义工作明确等待，
   恢复遵守已有受控入口、暂停/取消且不把在线当完成；记录实际覆盖与缺口。
   等待不消费原建议/批准；无受信任恢复事实不重发诊断或回退 AR。

不启动真实 Provider/8×8，不部署服务，不改变账号及批准策略。
其他恢复动作族的专用效果合同仍保留在综合任务，不以此批宣称全 Spec 完成。
实现说明：[无人恢复](../../docs/impl/unattended-progress-recovery.md)。

## 实施与验收

- Reader 通过 runtime 连续三次 fresh retry 验证固定记录继承，大小不随接力链
  累积；跨范围、损坏 handoff/记录拒绝，新发布记录优先且不回退旧记录。
- task-requeue 使用受控动作和人工批准两个入口；原 Task/Run/case/checkpoint
  绑定保留，收到有效工作进展后结案，快结果早于 pending 仍可重放。
- Provider 共因 mock 本批以 RM resident 诊断派发为验收入口：四个实例注入
  同后端额度故障，RM tick 正常推进；原诊断等待、无同 Provider AR fallback；
  不受信任/不同作用域的健康事实不解锁，暂停期间不派发，恢复后单次派发。
  建议与人工批准消费者对 waiting_external 不消费原请求。未冒称 OA/AR
  所有执行入口、在途长期等待或真实四 Provider 进程故障均已验收。
- RED 阶段复现：第二次接力丢入口；损坏继承记录被忽略；重新入队即结案；
  已知额度耗尽仍发诊断；等待消费建议/批准；批准丢失或被夹带字段改写身份。
  修复后对应回归与调用方通过。

最终验证（当前 worktree，PATH 使用共享 venv，PYTHONPATH=src）：

- `python scripts/dev-verify.py plan --base dev`：1029 个测试文件的影响闭包。
- `python scripts/dev-verify.py run --base dev --workers 8 --receipt /tmp/zf-unattended-lineage-closeout-verification.json`
  -> passed；确定性层 **12594 passed, 18 skipped**（269.08 秒）。
- 同一验证器的 `bash scripts/dev-premerge-gate.sh` -> **53 passed**；
  `bash scripts/run-flow-smoke.sh` -> **10 passed**；已执行层退出码均为 0。
- real-provider 显式层 not_run；未启动真实 8×8、额度探测或宿主服务。
- 首轮闭包 12590 passed；补审批链后 12593 passed；最后增加批准身份防错用例，
  针对最终代码再次执行整个已选闭包，以上 12594 是最终结果，不能累加各轮计数。
- `git diff --check` 通过；本批只本地提交，不 merge/push。

全 Spec 的剩余动作族、在途等待、OA/AR 降级、真实额度/宿主资格继续留在
原综合任务；本任务的 done 仅指上述有界交付与确定性验证。
