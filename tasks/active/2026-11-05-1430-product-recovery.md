> 状态: active

# 产品任务断点修复

用户批准按上一轮分析实施。框架前置已在 4f23798c 修复，见 [实现说明](../../docs/impl/task-recovery-20261105.md)。

- P04 五项 Verify 缺陷 -> verify: 保留原合同命令，独立 Verify passed，候选集成门禁。
- P06 注册 adapter 与输入/版本/回执绑定，缺模型 fail closed -> verify: 两条负例通过，成功路径明确注册测试 adapter；不冒充真实模型验收。
- P18 两项状态修复 -> verify: 暂停待确认失败落库，历史 attempt 完整不变，旧代次发布拒绝，Docker browser 回归。
- P16B 上游合同缺口 -> verify: 受控 gap plan 补私有结果和发布/读守卫，再实现隐私审核与受控导出，撤权回归。

受控重试：P04 evt-7ec18155ba26（唯一额外 g4）；P06 evt-40aa1996aaa2（g3）；P18 evt-9ab57fa85070（g3）。运行 workflow-0ea133e63454375e，保留已接受的 15 个任务；不得修改规范状态 JSON。

## 2026-11-05 14:49 UTC 检查点

- P18 已完成：产品 commit `032d1636`（`feat: preserve recovery attempt history across pause failure and cancel`）；独立 Verify 接纳 `evt-0f654e1e331e`，候选集成 `evt-41528ac85fd1`，Task done `evt-c3dd68d798a3`。候选前进到 `fa984630d17145583025bffbeb730165064f02a6`；runtime 42、browser 5，自检与独立多轮历史/旧代次/取消探针均通过，真实模型硬件资格未签收。
- P04 原五项修复已提交 `f73aac3`，runtime 35 和 Docker browser 通过。独立 Verify `evt-b1a91883b7e3` 新发现 review 幂等缺陷：旧请求重放可覆盖新复核。受控批准唯一 g5 `evt-69a88e826e4c`，只补幂等和并发、保持原成果，不重建已完成实现。
- P06 原假成功已修复 `6c10096a`，runtime 23 与 Docker browser 通过。独立 Verify `evt-faff3699ba6f` 新发现无资源 grant 可推理和非法类别 HTTP500；受控唯一 g4 `evt-2027e57bb6ee`，复用 access.authorize 并补接纳/执行/发布边界及精确 4xx 回归。真实模型配置仍缺，不能宣称生产模型资格完成。
- P16B Reader 已产出两个串行任务；`65f1103e` 修正 bridge 历史 ID 污染。第一次受控重放被源分片校验拒绝，第二次将 parent 绑定当前 Plan 节点后，`evt-253ba4414ad1` 应用成功，新 Plan Package `5f47133546cc1dc038f5938c56a0e8f88a342aae4de1b3cc42c8683ec11a0d60` 待批。未批准：独立语义检查发现矩阵正文仍引用旧任务，且旧 P16B/下游依赖尚未对账。需修订完整端口，不能将 task_input_policy 能读等同于 SDD 语义一致。

当前产品记录保持 active；不把已提交、已入包或重试派发计为通过。完整 canonical 证据在产品 `.zf-datal`，不提交运行态到 ZaoFu 源码。

## 2026-11-05 15:25 UTC 检查点

- P04 已闭环：最终提交 `a01a019f6da1298845cfea0b1387bc3ff355050b`，最终 runtime 49 / smoke 1 / Docker browser 1 / build 通过；独立 Verify 实测 8 路并发、延迟重放、撤权、跨项目、异体冲突及事务回滚通过。`evt-93d426a257bc` task.done，候选已集成，P05 依赖解除后自动派发。当前 done 17。
- P06 最终提交 `fbc368022015d4e0d0b76eaa78d2c1db6c9944e1`，代码实现 `ab8cd67`；最终 runtime 51 / smoke 1 / Docker browser 1 / build / Ruff 通过，`evt-6ec7bcb9c7ff` dev.build.done。独立 Verify operation `wop-verify-tp-P06-5b75f4ff257b7885-verify-r-92ffb606cb2b` 正在执行，尚未计为通过。
- P16B：shadow Orchestrator 评审 `evt-23c0f9388e99` 为 revise；本 session 按既有授权正式拒绝 `evt-28f97747d17a`，通过受控动作修复描述符与重合成来源，当前 Planner 已实际执行 `evt-bc38a48ef7dc` / `fanout-prd-plan-evt-8539e0dc`。完整端口修订须经预校验、Critic 和批准，旧 canonical P16B/下游残留仍需正常 adoption 对账。尚无隐私实现通过结论。
- `evt-d169779bf04f` 使用现有 source-repair actuator 只重启控制面。watcher 3843158 → 320573，重启前后的所有 pane/PID 完全相同，P04 Verify/P06 Impl/Planner 三项 operation 保持 running，没有 task requeue；`evt-179d5b314a9f` loop.started。实际 watcher 已加载本轮四个框架实现提交，未重启 Dashboard 或 worker。执行前现有 actuator 5 项与 control-plane-only 3 项测试通过。

## 2026-11-05 16:00 UTC 检查点

- P06 独立 Verify `evt-a8e3678aade0` passed；`evt-b0983ee93409` task.done，候选集成完成。P04/P06/P18 均为 done，总 done 18；真实生产模型及硬件资格仍不签收。
- P16B 完整 Plan 经 Critic `evt-8f730c90135e` passed，package `36b2b573`。批准 `evt-a4a86c8ee758` / plan `evt-70d3608671f2` 后，新代次 `evt-f1606798bd8b` 接纳两项任务。`3381b333` 已由真实 adoption 验证：旧 P16B 取消 `evt-d2368abab953`，P03/P02 历史取消补齐见证，P19/P20/P20Q 的旧依赖迁到实际新节点；P16B 原会话 `evt-0337b27f3749` 归档，未重开 done。
- OA shadow `evt-856f84b38bf7` 正确指出打包后的 PAS 三个身份字段漏入输入范围；首次派发 `evt-d235e594d944` 被拒。`64cff8fa` 修复机械身份共享读取，保留原包摘要及业务分区 fail-closed，两个任务各 12 端口与 36 个任务分区只读校验通过。
- 两次仅重载控制面：`evt-b3080405645f` watcher 320573→591756、`evt-583dcc8b5b18` 591756→654755。各次重启前后旧 pane/PID 保留，P07B 活跃 operation 未重建。P16B 发布契约于 `evt-270f653910a1` 实际派发，operation `wop-impl-tp-P16B-PUBLISH-CONTRACT-R2-84be-4767b962404e`；privacy 依赖发布契约，尚未实现完成。
- 后续 P05 新发现 align 生产 Registry 动态加载 ImportError/404；独立 discovery 已复现并建议上游加载修复再采样续接，属于后续任务新发现，不归咎已完成的 P04。本记录保持 active，不能把新任务已派发计为产品通过。

## 后续观察边界

P05 discovery 的两个 gap 已产出，但 `evt-f4ddc8ae440b` task_map.amend.failed：P05-ALIGN-smoke 缺 deterministic/reusable 声明；当前不能称为自动修复完成。P07B 另报上游动态插件加载缺口，待其正式结果和有界恢复。P16B 发布契约的新 attempt 已实际读取来源，尚无 build/Verify 成果；briefing 共有 586 个必读切片，输入范围性能需作为后续改进评估，不在本批放宽阅读门禁。

源码修复工作树已提交并清洁；实际控制面使用主工作区中经字节比对加载的补丁，未 merge 或 push。产品任务仍由后台 runtime 推进，此 active 记录不冒充四项产品全部完成。
