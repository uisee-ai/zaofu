# C-01 WRC 正常前沿 owner 护栏

> 状态: proposed

## 目标

把 `workflow.orchestration.normal_frontier_owner=wrc` 变成可执行的单 owner 契约：同一 Run/generation 由 WRC 推进正常 ready frontier，Run Manager 只处理异常恢复，不允许旧路径静默接管。

## 范围

- 复用现有 owner resolver，补充 WRC consumer 的唯一入口与 owner 断言。
- WRC owner 下，未完成 admission 的候选必须 fail-closed 并留下可重放诊断；不得落回 legacy dispatch。
- WRC owner 下，Run Manager 继续保持 owner fence；run_manager 配置保持兼容。

## 验收

1. owner=wrc -> verify: Task Pipeline blocking ready-set 只能由 WRC consumer 进入派发，legacy dispatch 返回 managed fence。
2. owner=wrc 且无 admitted generation / 非 blocking policy -> verify: 不产生 worker 派发，写入幂等 owner-blocked 诊断。
3. owner=run_manager -> verify: 194 continuation 与现有 blocking 路径不变。
4. unknown owner -> verify: loader 与 runtime 均 fail-closed。
