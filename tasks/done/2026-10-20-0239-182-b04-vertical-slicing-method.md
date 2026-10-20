# 182-B04 P2 · 可独立交付的任务切片与阶段集成

> 状态: done；实现：`865863d feat: bind planning roles to execution readiness methods`。
> 来源: [设计 182](../../docs/design/182-plan-review-task-slicing-and-evidence-closure.md) B04。
> 授权: 用户明确要求在独立 worktree 生成 backlog 并全部实施，随后启动 Dashboard 重新规划和评估。

## 范围与验收
- 对应：F03、F05、F06、D03、D05；所有者：Planner/Critic 方法；依赖：B03。
- 为每片写出完成后可演示的行为、合法调用入口、前置和返工边界 → verify: 领域工作包与可执行切片不再混用。
- 重审过大工作包与冗余条件任务 → verify: 车辆、设备、全站契约等有可说明的拆分依据；取消只读不无故等待物理删除。
- 给出 G2 读取、G3 作业、G4 全量交付的接线/验证方案 → verify: 各阶段真实入口可达，早期任务不承担未来行为，最终验证消费已有证据。
- 核对同一入口及共享文件所有权 → verify: 中间集成不会引入跨任务写入重叠或兄弟目录越权。
- 检查事务边界 → verify: 单次作业、设备替换、车辆调拨不会被拆成对外可见的部分提交。
- 不预设“必须增加到多少个任务”；任务数量由行为、依赖和验证成本推导。

## 实施记录

- 方法实现：`865863d feat: bind planning roles to execution readiness methods`。
- 同一可复用技能要求逐片写明演示、入口、前置、返工边界，保持单一共享文件 owner、
  早期接线与中间集成；事务按一个对外原子结果划分，不追求固定任务数。
- 洛书案例形成 54 个任务、32 波：车辆与设备按行为拆开，取消态只读不再依赖物理删除，
  一次维保作业、设备替换和车辆调拨各保持完整事务 owner。
- LS-ACCESS 提前建立真实入口与扩展约定；G2/G3 返工后新增 7 条 mandatory task_verify AC
  和 6 条命令，当前阶段实际验收后才解除后续依赖；原 candidate 复验保持 mandatory。
- G0 演示已从后续 LS-ACCESS 的入口改为本阶段拥有的安装/清单检查。结构及依赖检查
  不替代语义判断，最终独立 Critic、正式 Plan 和浏览器证据见
  [综合评审](../../docs/records/2026-10-20-0327-plan182-loshu-replan-review.md)。

## 完成证据

- 正式案例：`e7d1cd1 docs: record verified Loshu replanning and coverage review`。
- 最终图 54 任务 / 32 波，54 份 canonical contract 探针合法；依赖无环、生产者前置及
  写入范围通过检查。旧 26 任务 / 82 AC 均有映射，7 个规划 AC 已回归当前规划责任。
- LS-ACCESS 第 2 波建立入口；G2/G3 的 7 条当前 AC 和 6 条命令保留；最终 64 条 candidate
  AC 在 Impl 模板仍 blocked，未降低 mandatory。单次作业、设备替换、车辆调拨保持原子 owner。
- 最终正式 Critic 通过，浏览器完整展示最终 Plan；任务数不作为最优并行度或产品通过率证明。

- 详细评估：[综合评审](../../docs/records/2026-10-20-0327-plan182-loshu-replan-review.md)。
