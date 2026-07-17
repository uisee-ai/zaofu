---
name: vertical-slicing
description: "tracer-bullet 垂直切片的 task_map 拆分法:每个 task 纵切全部集成层、独立可验收,替代按技术层横切。task-map-synth/plan 角色使用,配对 task_map 契约与 lane 亲和语义。"
---

# ZaoFu Yoke Vertical Slicing

拆 task_map 时的默认形状应是 **tracer bullet 垂直切片**:每个 task 打穿
全部集成层(schema→逻辑→UI→测试)交付一条窄而完整的路径,而不是
按技术层横切("先把 schema 层做完,再做逻辑层")。

## 为什么(r4 反面实锚)

r4 按技术层横切成 sim-core / renderer / web 三个 task:每 lane 各自全绿,
但 ①跨层缺陷(五视角超时)归因困难——牵涉面横跨 renderer 与 e2e,
replan 两次路由错 lane;②跨 lane 类型偏斜(SIM 改必填字段,3D fixture
不知情)per-lane verify 原理上不可见;③没有任何单个 task 完成后产品
"可演示"——必须三 lane 齐活才第一次看到端到端效果。垂直切片下,
"五视角切换"会是一个 task,归因/验收/演示都落在一个 lane 内。

## 与 kernel 合约的配对

| 切片规则 | 对应的 kernel 机械 |
|---|---|
| 每片独立可验收(demoable) | contract `verification` 直接写端到端命令,verify 单 lane 即可闭环 |
| 片间依赖显式(blocked_by) | task_map 顺序/lane 排队;无依赖片并行入 lane |
| 片内文件面纵向收敛 | `allowed_paths` 按功能路径(跨包但窄)而非按包整包圈地 |
| prefactor 前置成独立片 | "make the change easy" 片先行,风险与冲突面前移 |
| 共享契约变更单独成片 | combined-tree gate(FIX-10)在它合入时立即验证,不与业务片纠缠 |

## 方法

1. 从验收条款(PRD 矩阵/共识条目)出发倒推切片:一条可演示的用户
   路径 = 一片;
2. 每片自问:"单独完成后,operator 能用一条命令看到什么?"答不出
   = 横切了,重切;
3. 依赖排序:prefactor → 共享契约 → 无依赖业务片(并行)→ 集成片;
4. 横切仅在两种情况下合法且须注明理由:纯基建层(如引入渲染引擎
   骨架)与性能专项;
5. 产出仍走 task_map schema:affinity_tag 按切片主责域标注(供 lane
   亲和复用上下文),不等于按层划疆。

切片粒度是增量纪律的上游:片内的执行循环(实现→测试→提交)见
incremental-delivery。

## 反模式

- "按目录拆最干净"(目录是横切的化身);
- 片大到覆盖多条验收条款(返工时整片重审——增量交付纪律的上游
  就是切片粒度);
- 把测试单独切成一片("补测试"片 = 前面所有片都无法独立验收)。

## How to test

给一份含 6 条验收条款的 PRD:产出的 task_map 每个 task 应能点名
"完成后可演示什么 + 对应哪几条条款",且无纯层任务(schema-only/
UI-only 无演示价值的片)。
