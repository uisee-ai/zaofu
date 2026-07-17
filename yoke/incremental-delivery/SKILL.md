---
name: incremental-delivery
description: "增量交付纪律:薄切片实现→测试→验证→提交循环,每个增量保持系统可用。writer 在 lane/worktree 语义下使用;与 candidate patch-id 幂等集成、attempt 绑定、allowed_paths scope gate 机械配对。"
---

# Incremental Delivery

按薄切片构建:实现一片、测一片、验一片、提交一片,再扩展。禁止一把梭
整个特性。在 ZaoFu 里这不只是好习惯,是**集成机械的输入格式**:candidate
按 patch-id 从你的 task 分支摘补丁(cherry-pick 幂等),提交粒度直接决定
集成质量与返工成本。

## 何时使用

- 任何多文件变更、从 task 拆分构建特性、重构既有代码;
- 任何"想一次写超过 ~100 行再测"的冲动出现时。
- **不适用**:单文件单函数的最小变更。

## 与 kernel 合约的配对

| 你的行为 | 消费它的机械 | 违约后果 |
|---|---|---|
| 一个关注点一个 commit | candidate patch-id 幂等集成(FIX-10) | 大杂烩 commit 冲突时整包回滚 |
| 只动 allowed_paths | 契约 scope gate | 越界文件 → task.contract.invalid |
| 在自己 worktree 的任务分支工作 | task_ref 铸造 + attempt 绑定 | 碰主 checkout = 破坏集成真相 |
| 每增量绿(typecheck+相关测试) | combined-tree quality gate | 红增量进 candidate → integration.failed 冻结全 pdd |
| rework 叠加修复不重写 | 同 lane 同 worktree 续做语义 | git reset 重来 → attempt 断裂,完成判 stale |

## 增量循环

```
实现最小完整片 → 测试 → 验证(测试过/构建过) → 提交 → 下一片
(承接推进,不重启)
```

## 切片策略

- **纵切(默认)**:每片打穿栈的一条完整路径(建任务=DB+API+UI 一条
  线),片片可演示——task 级的纵切哲学见 vertical-slicing,本技能管
  task 内的执行;
- **契约先行**:前后端并行时,片 0 定契约(类型/接口),1a 后端对
  契约、1b 前端对 mock,片 2 集成——**共享契约变更单独成片且前置**:
  跨 lane 类型偏斜 per-lane verify 看不见,candidate 集成才炸(r4
  `PresentationPath.rendering` 实锚);
- **风险先行**:最不确定的片最先做(先证明 WebSocket 能通,再在
  上面盖楼)——片 1 失败时你还没投入片 2-3。

## 实现规则

### 规则 0:简洁优先
写之前问"能工作的最简单做法是什么"。写完自查:能更少行数吗?抽象
配得上复杂度吗?在为假想的未来需求造东西吗?三行相似代码优于过早
抽象;第三个用例出现前不泛化;先写朴素明显正确的版本,正确性有测试
证明后才优化。**第一片必须接线**(有真实调用方)——写了没人 import
的类不是交付,是 library-without-callers。

### 规则 0.5:scope 纪律
只碰任务要求的。不许:顺手清理邻近代码、重构没在改的文件的 import、
删不完全理解的注释、加规格外"看着有用"的功能、给只读文件现代化语法。
发现 scope 外值得改的,**记下不动手**,进完成汇报的 noticed 清单。
**根因在 scope 外时拒单**(dev.failed + out-of-scope),拒单理由回流
replan 作归因反证(FIX-11)。

### 规则 1-5
**一次一件事**:组件新增/既有重构/构建配置 = 三个 commit,不混;
**始终可编译**:片间不留碎状态;
**未完成特性上 flag**:没准备好见用户但要合入时用 feature flag 圈住
(r4 的 WebGPU 开关即实例)——flag 现在加,不是"以后";
**安全默认**:新行为默认关闭、opt-in;
**可回滚**:每片可独立 revert;删除与替换分开成片;迁移带回滚。

## 常见借口

| 借口 | 现实 |
|---|---|
| "最后一起测" | bug 复利:片 1 的 bug 让片 2-5 全错 |
| "一把梭更快" | 感觉快,直到 500 行里找不到哪行炸了 |
| "太小不值得单独提交" | 小提交免费;大提交藏 bug、回滚疼 |
| "flag 以后加" | 没完成就不该用户可见,现在加 |
| "这点重构顺手带上" | 重构混特性,评审与排错双倍难 |
| "再跑一遍构建确认" | 代码没变,重跑不产生信息 |

## Red Flags

- 100+ 行没跑过测试;一个增量多个无关变更;"顺手把这个也加了";
- 片间构建/测试红着;大量未提交改动堆积;第三个用例前造抽象;
- 为一次性操作新建 util 文件;scope 外文件"路过顺手改"。

## Verification

- [ ] 每片单独测过、提交过,消息说明覆盖哪条验收
- [ ] 相关全量测试过、构建净、类型检查过
- [ ] 特性按规格端到端可用;无未提交残留
- [ ] 共享契约变更片前置且全仓 typecheck 过
