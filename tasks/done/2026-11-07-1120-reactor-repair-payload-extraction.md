> 状态: done — 673bdade refactor: move repair dispatch classification to its owner

# 抽取自修复派发分类，恢复 reactor 结构门

main `orchestrator_reactor.py` 为 6981 行，冻结 cap 为 6977；结构门实测 1 failed / 15 passed / 1 skipped。保留 main 的 completion interruption hook 与 suspended 生命周期兼容，不调整 baseline、不删除空行规避。

- 将自修复派发 failure_class 的机械映射原样移至既有 repair_authorization 职责模块 -> verify: 原真实 confirmed-candidate 入口产生相同 payload。
- 仅变更函数定义与现有 caller import/name -> verify: main 两项兼容差异仍原样存在，精确 patch 可应用且结果低于 cap。
- focused caller/授权闭包 + sentinel/mock -> verify: 精确结果写入[实现说明](../../docs/impl/reactor-repair-payload-extraction.md)。

主会话统一提交/归档/同步；本片不修改运行态。

## 验证

- main 原始结构 RED 1 failed / 15 passed / 1 skipped。
- 抽取前真实 caller payload 边界 24 passed；抽取后 6 个 focused 文件 77 passed / 1.64s。
- Sentinel 50 passed / 1 skipped；精确源补丁 main `git apply --check` 通过。
- 函数体 AST 改名后相同；main 应 6981→6964 行，两个既有兼容 hunk 均保留。
- dev-verify broad 选择以 focused 替代 deterministic/serial；自动 contract 50 passed / 1 skipped，flow smoke 10 passed / 1.87s。

主会话已将精确hunk同步main，保留现有两个兼容改动；main实际6964行，sentinel50 passed/1 skipped，直接caller+gap入口+context归属组合41 passed。这是等价职责抽取，未打断正在运行的Privacy和Critic；本轮未merge或push。
