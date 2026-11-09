# R59：同代际重放不能静默忽略显式输入变化

> 状态: done

实现：6686fb97 fix: reject changed explicit inputs during plan replay。dev合并da763331 fix: preserve explicit plan inputs during replay。

授权：持续监工LS-v1，发现并修复阻塞，记录恢复。本地dev 2f14f11f隔离实施、合并，不push。

现场新增车辆维保端口后，任务图不变而显式source_index已补2行；admit_plan_artifact_package_for_payload按同task_map_generation找到旧包，仅比较Task Map后直接返回旧包identity。新来源索引被忽略，evt-61a7651ae391/evt-ac9d27370650将修订当作相同待审计划抑制。旧包不可变是正确边界；静默接受新输入并返回旧输入违反正文不失真。OA读取冻结的旧包，RM只看到正常重复抑制，不能通过重试解决未暴露的漂移。

1. 在实际准入入口复现 -> verify: 同图同代际下，改变显式inline/ref/top-level输入或新增端口必须明确拒绝，不能返回旧Package。
2. 在既有lineage检查边界校验显式输入 -> verify: 校验ref/digest，比较解析后的实际内容；相同正文换路径/JSON格式及没有新输入的合法重放保持复用。冻结包、事件和Task不修改；新内容须经新计划代际/审批。
3. 覆盖实际调用者与恢复 -> verify: 聚焦测试、准入及事件/包影响闭包、sentinel和design199 mock E2E；现场保留在途治理Verify，用包含真实来源修订引用的新代际恢复端口任务，不通过绕过审批/编辑冻结包解决。

## 验证与恢复记录

- 原始回归 RED：5 failed / 4 passed，五种输入漂移均未抛错。首次夹具缺真实 inherited 包而失败，修正夹具后才记录该 RED；没有把夹具错误算作缺陷复现。
- 聚焦46 passed；design199 mock 与共享锁11 passed；dev-verify 自动 serial10 passed；premerge sentinel53 passed。
- planner 选择260个影响闭包文件，因为超过80便建议full suite；遵循仓库当前规则，保留全部260个选定文件执行 deterministic 闭包，不扩展为完整pytest。最终3709 passed / 1 deselected，425.26秒；不是完整pytest或真实provider E2E。
- 真实LS输入复制到隔离临时目录：v2索引漂移被拒绝并指明新代际；v3完整新包相同输入重放通过，临时目录删除。详见LS docs/records/2026-11-07-r59-live-input-probe.json；不修改生产事件或冻结包。
- v3正常准入后，核对全部显式端口、42项来源索引及旧41Task绑定后审批。2026-11-07 22:29:22 UTC 新生产者 FMS-12-GAP-VEHICLE-PORT-01 实际派发，operation wop-impl-tp-FMS-12-GAP-VEHICLE-PORT-01-2a-93ca348862e6；治理Verify保留。此为任务恢复，非产品完成。

- 合并后46项聚焦与53项sentinel通过。22:38 UTC控制面已加载da763331，watcher111869→247526；治理Verify、车辆端口Impl与OA的在途operation/attempt/dispatch/工作区/pane/PID保持。flow-discovery正常结算，准入evt-7f7a24bd2b67。重载收据为LS docs/records/2026-11-07-r59-controlled-reload.json。
