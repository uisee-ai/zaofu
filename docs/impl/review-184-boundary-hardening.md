# Operator、PRD 评估与证据读取边界修复

本批次对应 `tasks/` 中 `2026-10-22-0620-184-*` 四项任务。
以实际入口回归为准，不修改已有事件或实际项目运行状态。

## Operator WebSocket

默认与项目级 `/operator/io`、`/operator/control` 在连接终端管理器前
复用 Terminal 的同源判断。Origin 必须存在，scheme、host/port 必须与
当前连接匹配；Operator 不额外开放跨源白名单。
同源校验通过后仍须满足既有 token、passcode session 或 trusted-local 授权。
本地受信模式不豁免 Origin；无 Origin 的客户端也不能直接操作终端。

## PRD 评估恢复

`channel_prd_evaluation_recovery.py` 使用现有 POSIX 文件锁。锁从派发前取得，
覆盖后台线程、输入校验和 provider 评估整个执行期，finally 释放；进程退出
由操作系统释放锁。锁文件保留本身不代表执行仍存活，检查实际锁归属。

存在 dispatched/started、没有终态且执行锁可取得时，记录
`output.evaluation.failed` / `INDETERMINATE`，原因是执行中断。
Kernel 的空闲 replay sweep 和受控评估请求都会执行该检查。
仅 requested 尚未派发的请求由空闲 sweep 继续派发。

原 request 不再次执行；Owner 显式 retry 才创建新 request_id，新请求同时
隔离输入、报告与结果身份。活跃锁持有者不会被重复请求或恢复检查抢占。
线程启动失败也记录失败并释放锁。历史 completed/failed 不被恢复覆写；
已有 provider 调用期限继续约束执行，恢复不按 elapsed time 猜测结果。

## Sidecar 读取

`hydrate_sidecar_ref` 单次打开文件，最多读取 `max_bytes + 1` 字节，随后
对该字节缓冲执行长度、SHA256 和内容解析。返回的 digest、byte_count 与
payload 绑定同一份数据。读取期间原子替换路径只能影响之后的读取，不能
产生“旧摘要 + 新正文”。独立 `verify_sidecar_ref` 检查接口保持不变。

## PRD 质量合同

新受控 synthesis 请求和讨论自动生成的 synthesis 请求均持久化
`prd_quality_contract=channel-prd-quality.v1`。生成与修复消息保留该要求。
回复入口按 channel/thread/request 读取事件账本绑定，Agent 的消息 refs
或正文不能降级它。缺少标记、错误版本或不满足质量结构进入现有 repair 流。

独立评估按 canonical synthesis 事件关联的请求再次验证版本与正文。
没有版本绑定的历史请求保持原兼容规则，不追溯修改历史产物或补造结论。
本变更只落实已有 schema 与证据要求；内容质量仍由 Agent 评审。

## 验证入口

- `tests/test_operator_websocket_security.py`：真实 API 路由、跨源拒绝、正常 IO/control。
- `tests/test_channel_prd_evaluation_recovery.py`：重放、受控重试与线程启动失败。
- `tests/e2e/test_channel_prd_evaluation_recovery_mock_e2e.py`：真实子进程丢失，
  经 Orchestrator 周期入口恢复，再显式重试；provider 为 mock。
- `tests/test_sidecar_refs.py`：读后原子替换与大小边界。
- `tests/test_channel_prd_quality.py`：必需版本、历史兼容、repair 与评估预检。
- 影响闭包由 `scripts/dev-verify.py plan --base dev` 解释，运行结果记入任务归档。
