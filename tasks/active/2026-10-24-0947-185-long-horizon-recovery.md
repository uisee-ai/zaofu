> 状态: active

# 185 · Long horizon 生命周期与原 Run 断点恢复

用户批准取消 Run 总时限，按任务完成返回；本轮真实验收 Run 总 token 上限也取消，
继续计量。沿用[监工任务](2026-10-24-0702-185-supervised-repair.md)，Kimi 不在范围内。

- [x] 生产/refactor profile 总时限设为0 -> verify: 配置编译、超过4小时/72小时的确定性测试；Operation超时仍生效，正数Run限制仍受支持。
- [x] 修复受控 reader 重试 -> verify: 新Operation/Attempt/scratch关联旧调用；旧调用保持cancelled；已完成reader不重跑，重复repair不重复派发。
- [x] 恢复被Run blocked取消的reader汇总 -> verify: 授权恢复后收齐结果继续；仍blocked/人工取消拒绝，失败结果保留失败语义。
- [x] 升级冻结源码、修订原Run预算并续行 -> verify: 原Run/Plan/TaskRefs/candidate保留，原AC独立Verify/Judge与Kernel最终交付；阻断如实记录。
- [x] 记录、验证、提交 -> verify: 影响闭包/sentinel/mock结果与真实证据分开报告，不push/merge。

`zf start` 是驻留项目服务；Run完成返回属于任务生命周期，不应因一个Run结束而停止其他任务。
实现说明：[长期Run与受控恢复](../../docs/impl/185-long-horizon-run-recovery.md)。


最终：原Run workflow-3ba5d2aa7ac4bb65真实恢复，原需求及计量基线不变。Discovery
发现整数输出边界缺陷，按原审批/独立Verify/Discovery/Judge产生并验收gap候选30aa6a3；
旧candidate99b0ed0及失败事实保留。Kernel交付main=48778d1，24必需声明closed，
run.goal.completed=evt-f0e7c931237f。交付目录原65项oracle、独立边界6项通过；
Docker真实API/UI四任务done且0页面异常。重启对账没有增加Operation或Ship次数。

实现：6513375d、4e60ec17、0fe2af8f、8b8cdd58、7c35ecb8、6be5b399、1eb976a7、
3ea26a8f及SUP-021收尾修复。SUP-013至SUP-021见监工记录，失败与夹具错误均保留。
SUP-020影响闭包90、sentinel51、flow mock3通过；SUP-021验证结果见记录。
本轮真实交付3.836h含暂停，5h/72h为受控时钟测试，不声称真实超过4h。
历史错误legacy closeout保留；SUP-021有真实事件前缀回放和原tick入口回归，
没有再启动Provider改写原运行历史。

证据根：`/home/min/workspace/public/zaofu-evidence/185-supervised-20261024T070200Z`。
原需求、Git、原始ledger/sidecar、失败/成功浏览器和测试证据均保留。
本任务只签收长期Run与该次原PRD恢复；184/185整体1—9中的配置冻结后CAS、
通用源码single入口、原生助手资格及完整跨Provider恢复仍由原active任务管理。
