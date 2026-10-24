> 状态: active

# 185 · Long horizon 生命周期与原 Run 断点恢复

用户批准取消 Run 总时限，按任务完成返回；本轮真实验收 Run 总 token 上限也取消，
继续计量。沿用[监工任务](2026-10-24-0702-185-supervised-repair.md)，Kimi 不在范围内。

- [x] 生产/refactor profile 总时限设为0 -> verify: 配置编译、超过4小时/72小时的确定性测试；Operation超时仍生效，正数Run限制仍受支持。
- [x] 修复受控 reader 重试 -> verify: 新Operation/Attempt/scratch关联旧调用；旧调用保持cancelled；已完成reader不重跑，重复repair不重复派发。
- [x] 恢复被Run blocked取消的reader汇总 -> verify: 授权恢复后收齐结果继续；仍blocked/人工取消拒绝，失败结果保留失败语义。
- [ ] 升级冻结源码、修订原Run预算并续行 -> verify: 原Run/Plan/TaskRefs/candidate保留，原AC独立Verify/Judge与Kernel最终交付；阻断如实记录。
- [ ] 记录、验证、提交 -> verify: 影响闭包/sentinel/mock结果与真实证据分开报告，不push/merge。

`zf start` 是驻留项目服务；Run完成返回属于任务生命周期，不应因一个Run结束而停止其他任务。
实现说明：[长期Run与受控恢复](../../docs/impl/185-long-horizon-run-recovery.md)。


11:12 UTC：实现提交6513375d、4e60ec17、0fe2af8f、8b8cdd58、7c35ecb8、6be5b399、
1eb976a7已本地提交。原Run已取消总time/token并真实恢复；两项原Verify通过，Discovery
发现产品缺陷后产生gap，新候选30aa6a3外部65项/新增边界探针通过，新Verify结果准入。
当前等待Discovery/Judge/Kernel交付；因此真实验收项仍不勾选、任务不归档。
