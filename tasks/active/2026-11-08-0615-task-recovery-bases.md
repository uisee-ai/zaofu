# R64：Task Pipeline 保留增量生产者与部分成果后继的独立基线

> 状态: active；依据用户持续监工、发现阻塞即修复并恢复的授权实施。

LS-v1 FMS14正式阻塞并提交f0f88df，当前Candidate7360e2b。新files producer只能基于已发布Candidate，work successor需续接部分成果。当前writer_fanout_dispatch_base_commit统一基线导致冲突；强行统一为partial会污染producer基线。只读真实Git预检记录于LS-v1 docs/records/2026-11-08-mixed-recovery-base-preflight.json。未准入错误新图。

- [x] 阻塞Task Pipeline接受各自已验证的任务base，旧fanout仍拒绝混合base -> verify: loader与真实入口、Plan预检正反例。
- [x] generation准入记录从TaskMap编译的逐任务基线，replay派发与集成均消费对应基线 -> verify: 同一generation producer只见Candidate，successor合成新Candidate+获准partial；重启保持绑定。
- [x] 保持Git来源、部分成果准入/写域/冲突及Verify固定目标门禁 -> verify: 未授权partial、缺失或异常base不放行，既有partial successor回归通过。
- [ ] 更新恢复说明、聚焦影响闭包/mock E2E/sentinel，提交合并dev，受控加载后对LS实际修复图再验收 -> verify: 精确提交、测试记录和新派发基线；不将代码修复等同产品完成。

不修改LS canonical业务状态，不改变37个完成合同，不关闭Owner/E2E门禁，不push。

## 验证记录

2026-11-08：11模块聚焦204 passed；其余294个影响闭包测试文件4128 passed / 8 skipped；新增逐任务基线文件补充集成调用测试后10 passed。dev-verify serial 10 passed、sentinel 53 passed、deterministic mock flow E2E 10 passed。未运行全量pytest或真实Provider E2E。日志：/tmp/zf-r64-focused.log、/tmp/zf-r64-impact.log、/tmp/zf-r64-gates.log，receipt /tmp/zf-r64-gates.json。实际LS加载与恢复派发待验收，因此保持active。
