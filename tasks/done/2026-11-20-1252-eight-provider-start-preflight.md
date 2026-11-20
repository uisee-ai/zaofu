# 8×8 真实验证：受限 Codex 启动前沙箱检查

> 状态: done

实现：`2ba5ac95 fix: reject unavailable restricted Codex sandbox before startup`。

来源：用户授权真实 8×8 无监工验证、发现问题修复并评估。首轮 Run
`workflow-055abbd5196f67c6` 的 product-scan、tech-scan 和 Run Manager 均无法启动
命令，实际错误 `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`。
现有 doctor 已能检测，操作者启动前漏跑；`zf start` 的自动预检也未复用探针。

## 范围与边界

- 在 `zf start` 启动 Provider/transport 前，对显式 workspace-write Codex 配置运行已有探针一次。
- 探针失败保留环境诊断事件并拒启，不能随 skip-workflow-inspect 跳过；dry-run 不访问真实宿主。
- 不修改 sandbox/权限/模型，不自动安装宿主包，不修改原失败或虚构业务完成。
- 本项修复不等于宿主已修复，也不等于无监工、并发、最终交付资格通过。

## 验收

- 普通/no-watch/控制面/skip-workflow-inspect 启动遇探针失败 -> verify: 非零，transport 未调用，env.preflight.failed 含角色和诊断。
- 多个受限角色 -> verify: 一次共享探针；mock/非受限配置与 dry-run 不调用真实探针。
- 自动验证闭包 -> verify: 聚焦测试、启动调用方、Provider/环境探针与相关恢复 mock E2E、sentinel 通过。
- 真实故障宿主复验 -> verify: 新 start 在任何模型派发前拒启；宿主安装与 8×8 恢复待管理员处理后另验。

## 证据

实施说明：[真实验证记录](../../docs/impl/eight-by-eight-unattended-qualification.md)。
原始证据存于 reports/eight-qualification-20261120T122929Z/。

## 已执行验证

- dev-verify plan/run（显式两个新增/更新测试）：2,085 passed；覆盖 start、直接调用方和 Provider/事件边界；不是全量测试。
- dev-premerge-gate：53 passed。
- host supervision / env preflight mock：11 passed，2 个真实宿主测试 deselected。
- 故障宿主真实 start：exit 1，事件 evt-fd44e9774005；未启动 Provider。
