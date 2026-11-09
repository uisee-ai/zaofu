# 199／200 实施与真实运行验证

> 状态: done

200 拆出的八个工作包已完成。本次真实 Codex/v5 业务完成、独立复验通过并清理退出。
范围与每项限制见 [实施记录](../../docs/impl/200-agent-recovery-implementation.md) 和 [199 的 31 项对账](design-traceability.md)。
本报告是选定证据的归档，不是可直接恢复运行的 canonical 状态快照。

## 实现与实际运行

实现提交 `b1b39758`；真实验证修复 `115e450d`（求助及时路由）、`768adb08`（sidecar 跨控制面重启保持单例）。
业务运行加载至 `115e450d`；最后一项生命周期修复由单独的真实宿主故障回归验证，未冒充重新跑过完整业务流程。

| 项目 | 实际结果 |
| --- | --- |
| Provider | Codex CLI 0.163.0、`gpt-6.1-sol`，真实订阅认证 |
| 配置 | `zf-prd-v5.yaml` 派生的项目 `zf.yaml`，有效版本 5.0.1；WRC / blocking / 8+8 / 16 活动 Pipeline / 串行 CAS / frozen_exact_commit 保留 |
| 业务 | 标准库 JSON 客户列表转 CSV；表头、顺序、Unicode/转义、空列表、错误原子输出和非空测试 |
| Run | `workflow-4ef709e12ce26efd` |
| 正式闭环 | Scan → Plan → Plan Critic → Impl → 任务 Verify → 集成 → 8 路候选 Verify → Discovery → Judge → ship → `run.goal.completed` |
| 最终事件 | `evt-f6dac67e0122`，2026-11-06 18:20:22 UTC；业务 Task 与根 Task 均 done，19 个 operation 均 settled |
| 冻结候选 | `6661dc6613d04fd143a44da7e5e0e1efaada06c8` |
| 隔离项目 dev 交付 | `b9219864fe5faf074286b878a7cc7669a7772d5a`；不是合并 ZaoFu 源码分支到 dev |
| 独立产品复验 | 对上述两个确切提交分别 `git archive` 到干净目录，8 个行为检查、7 个 unittest 均通过 |
| Discovery | 另写并运行 3 个测试、14 次 CLI 调用，零阻断缺口 |
| Judge | 五条必需 claim 关闭；裁决经 admission 后由 Kernel 完成运行 |
| OA 求助 | 当前 Worker 提问、OA 回答、原 attempt 读取后继续；没有创建第二 Impl 写入者 |

复验脚本在业务实现之前冻结。代码、测试、原始需求及摘要保存在 `product/`、[product-source.json](product-source.json)；
它们来自确切候选提交。完整业务阶段没有手写成功事件，也没有直接修改 canonical Store。
运行中操作员部署过已验证修复、重载过控制面；这不是全程无人工介入的故障恢复证明。

## 测试结果

| 层级 | 结果与解释 |
| --- | --- |
| 本轮最后一次广域 | **14,539 passed / 6 failed / 20 skipped**；发生在最终两项实跑修复之前 |
| 六项已复现基线失败 | public export 缺工具文件 4 项、缺内部 prompt 1 项、Feishu 旧 readiness fixture 1 项；沿用 199 隔离基线证据，未删除、跳过或改绿 |
| 主要影响闭包 | 341 passed；诊断准入 236 passed；实际 SQLite 共享修复／探针组 40 passed |
| 求助修复闭包 | 249 passed、调用方 97 passed |
| sidecar 修复闭包 | 67 passed；真实 SIGKILL/systemd 回归先红后绿，最终 1 passed（14.22s） |
| 其他必需层 | serial 10 passed、flow smoke 10 passed、最后 sentinel 53 passed |
| 静态／文档 | 变更 Python 的 ruff、skill 镜像一致性、manual currentness、diff check 通过 |

命令结果不可相加为唯一测试数。广域命令不是全绿；后续修复按实际模块与直接调用方完成验证。
详见 [test-receipts.json](test-receipts.json) 和 `test-logs/` 的原始输出及摘要校验；两份红日志用 gzip 无损保存，SHA-256 仍绑定解压后的原始字节。

## 问题、修复与实际效果

| 观察 | 处理与验证 |
| --- | --- |
| 首轮准备器误读宿主项目注册表 | 设置独立 `ZF_WORKSPACE_HOME` 后重建 canary；首轮明确 aborted 并退出，不记为成功 |
| RM 建议分支未完全复用共同准入 | 9 个拒绝反例先失败后通过；补当前案卷、人工等待和 precondition 检查 |
| 真实求助等待周期扫描，首跳多等约 180s | reactive/periodic 共用持久化 reconcile 与锁；原请求延迟照实记录，新增实时路由与并发去重回归通过 |
| 正常 stop 后遗留三次重载的旧 sidecar | 子进程继承 singleton lock，停止匹配完整参数／state dir；真实宿主复验证明重启不复制 sidecar、正常 stop 清掉进程组；旧进程按启动事件逐一核实后清理 |
| 固定 8 路与较大必需输入带来开销 | 保持预先冻结的配置完成测量；本轮未签收自适应并行或输入压缩优化，不删除证据制造快速完成 |

本次从正式 invoke 到 Kernel 完成为 **3,808.33 秒（约 63.5 分钟）**。
Impl 721s、任务 Verify 156s、最慢候选 Verify 590s、Discovery 952s、Judge 298s；详见 [execution-metrics.json](execution-metrics.json)。
控制面投影成对基准 p95 251.70→254.15ms，RSS 68,936→56,720KiB，满足预冻结界限；这不是模型吞吐提升证明。

usage 投影有 397 条未定价记录；显示 USD=0 不代表零成本或金额预算已在本次真实调用中得到证明。
预算／容量未提高，未新增全局时间或 token 限制。原始 usage 精度见 [usage-accounting.json](usage-accounting.json)。

## 证据与退出

- [workflow-milestones.json](workflow-milestones.json)：正式入口、候选冻结、最终判断、交付、完成与停止事件。
- [admitted-results.json](admitted-results.json)：18 个准入结果及 70 份被引用正文／探针，保存原始文本与 SHA-256。
- [runtime-load-receipts.json](runtime-load-receipts.json)、[configuration-facts.json](configuration-facts.json)、[canary.zf.yaml](canary.zf.yaml)：部署／加载／配置分别留证。
- [assistance-receipts.json](assistance-receipts.json)、[candidate-freeze.json](candidate-freeze.json)：求助身份、回答与确切候选绑定。
- [independent-product-result.json](independent-product-result.json)、[delivered-product-result.json](delivered-product-result.json)：独立检查真实候选及交付提交。
- [host-supervision-summary.json](host-supervision-summary.json)、[sidecar-host-summary.json](sidecar-host-summary.json)：真实宿主生命周期；任务使用 mock backend，不冒充模型诊断。
- [cleanup-receipts.json](cleanup-receipts.json)、[legacy-sidecar-cleanup.json](legacy-sidecar-cleanup.json)：退出、遗留进程的明确处置与临时目录清理。

已执行 `simulation.done`、`stop --include-run-manager`，核对归属进程和 tmux 服务退出后删除临时业务项目。
未归档 Provider home、认证、环境变量或全量对话。`drivers/` 保存本次驱动，绝对路径属于此次采证，不能把旧 manifest 用于再次启动。
199 正文保持原 SHA-256；199 开发 worktree 与工作分支保留。本次只做本地提交，未 merge 到 ZaoFu dev、未 push。
