# Workflow 输入、里程碑与调度

## 212 的读取与恢复

每次派发继续以原 request、actor、attempt、合同和输入策略为准。新建的 Candidate Verify、任务集成验收和 Thin Judge 可以分别使用 owner 在配置中固定摘要的切片证书。只有来源清单、目标、profile、完整原策略摘要和授权 selector 均匹配时才替换必读项。每个替换项需要列明义务。合同、目标、显式必读和原文的授权范围保留。既有 request 重放使用冻结的选择。当前 8×8 沿用原来的 40 项。

| 操作 | 获取的事实 |
| --- | --- |
| `zf artifact index --attempt ATTEMPT --source-id ID` | 按已授权的来源精确定位。通用文本检索会跳过二进制正文，媒体保留描述符。 |
| `zf artifact recover --attempt ATTEMPT` | 当前绑定、工作记录、历史已读与独立的 Provider 交付缺口。超过一页时沿返回的 `next_delivery_gaps` 命令继续。已定位的缺段用字符半开区间表示。 |
| `zf artifact workspace --attempt ATTEMPT` | 在持久工具会话前台启动只读挂载，初始化后打印 `INPUTS.md` 和 `RESUME.md` 的绝对路径。来源路径相对于该目录。查询、浏览目录均不构成必读见证。 |
| `/usr/bin/cat /绝对路径/inputs/来源.txt.chars-0-800` | 读取选中正文的字符区间。普通文本、CRLF、中文与空值保留原字节。二进制可原样读取，媒体的视觉验收由相应角色承担。 |

挂载试点使用 Linux、cgroup v2、FUSE、`fusermount` 及 libfuse2。项目可通过 `uv sync --extra native-inputs` 安装对应的 Python 适配器。在项目的 `workflow.flow_metadata.artifact_worker_cgroups` 中将具体 worker 实例映射到专属 cgroup 的绝对层级名。每次 open/read 都检查当前派发凭据、会话、进程、cgroup 和 grant 代次。直接 I/O 并禁用数据缓存，原进程退出、凭据轮换后旧描述符不得继续读取。当前版本只为与注册会话相符的 Codex CLI 0.171.0 单文件 `exec_command` 创建严格原生交付见证。旧合同、共享工作区、无法验证的包装器及不支持的 Provider 使用派发中原有的 CLI。

文件访问时的 caller PID、实际文件、冻结来源及读区间形成宿主见证。准入重新核对该会话中唯一 call ID 的参数和公开返回。完整输出及可精确复核边界的 Codex 截断片段可以累计，并可用 CLI 补齐中段。原生见证、CLI stdout、当前模型记忆和产品判词分别记录。挂载在产品仓库之外创建，停止后释放。挂载不创建产品文件。

选择新切片时，在 `workflow.flow_metadata.approved_review_scopes` 中按输出 profile 放置 owner 审查过的不可变工件描述符。证书使用 `approved-review-scope.v1`，包含 `approved_by`、`revision`、绑定的 `workflow_run_id`、`target_commit`、`output_profile_id` 与 `baseline_sha256`。每个替换给出原 source/artifact/digest/selector、对应义务以及新 selectors。覆盖不足或换版时继续使用原清单。验收对账及风险批准由合同责任人完成。

## 213 的接入位置

配置在 `zf.yaml` 的 `workflow.flow_metadata` 中。以下入口默认按项目选择启用。

| 配置或入口 | 行为及恢复 |
| --- | --- |
| `milestone_handoff: true` | Candidate Verify 新 request 冻结目标、成员、前驱判定、草稿来源及剩余义务。重放复用同一摘要。worker 通过 `zf artifact milestone --attempt ATTEMPT` 查看状态，本地操作员可在原 worker 退出后执行 `zf workflow milestone OPERATION`。 |
| `milestone_readiness.required_gates` | 显式 gate ID 需要同 run、合同、generation 和 target 的已通过证据。空义务集须有 `empty_waiver_gate` 对应的通过记录。目标变更、未决执行、缺少独立验收、缺少命令或发现待处理问题时，投影列出缺口与 owner。实际推进继续走原准入。 |
| `run_boards[RUN]` | 固定 `enabled`、`generation`、`members` 与可选 `groups`。用 `zf artifact board --attempt ATTEMPT --post ID --recipient @group/review --text TEXT` 提交；接收者用 `--seen KEY` 和 `--ack KEY` 分别报告看见与消费。投递前重新核对活跃 operation、原 request、接收者、代次；竞争和重复消费共享同一认领。 |
| `orchestration_schedules` | 定义 `id/revision/owner/goal/workflow_run_id/effective_at/enabled/action`。`inspect` 需要五字段 cron、IANA 时区、`max_occurrences`；`notify` 需要 deadline 和原 operation、request_hash、actor、attempt，可绑定一个事件类型及来源。每个 wait 只有一个胜因。夏令时缺口跳过，重复时间取较早一次；停机默认合并，`catchup` 可指定有限补算。 |
| `zf workflow schedule ID` | 返回历史、计划时刻、执行与完成时间、重试归属。手工巡检给 `--run-now REQUEST_ID --authorization-token TOKEN`，在定义中预先固定 owner 令牌的 `manual_token_sha256`，重复 request 不重复作业。独立巡检仅汇总已有 run 的操作事实；首轮没有启用无人值守的外部写入。 |
| `worker_model_planners[实例]` | 仅 Impl 新派发冻结 `enabled/model/port/max_calls/max_output_tokens/timeout_seconds`。Executor 可在同一 attempt 通过 `zf artifact worker-plan` 将选中的冻结依据交给本机只读 Planner，再将建议带回原工作区。回交原因有 initial、repeated_failure、interface_deviation、design_choice。保留用量、耗时和失败回执，结果未知时不自动重发。独立 Verify 和原合同继续管控交付。 |

双模型试点使用环回地址的 Ollama 文本接口，默认端口 11434；没有授予 Planner 工具、工作区写权限或新的业务角色。不支持该适配器、既有预算无法覆盖或没有授权时，Executor 使用原模型继续。服务返回的模型名、输入和输出 token 才计入该次调用。[Ollama 文本接口](https://docs.ollama.com/api/chat)

## Mock 与宿主证据

使用 8×8 原 Run 的两条 Candidate Verify lane，各从冻结清单和不可变来源复制到独立临时状态。每组使用单独的账本与模拟公开工具返回。A 是 8 KiB JSON 批页，B 是 32 KiB 文本批页，C 是单文件直读、每页最多 8,192 字符。

| 每条 lane | A | B | C |
| --- | ---: | ---: | ---: |
| 模拟读取调用数 | 72 | 16 | 82 |
| 覆盖的原必读项 | 40/40 | 40/40 | 40/40 |
| 两次恢复新增的宿主执行数 | 0 | 0 | 0 |

两条 lane 都保留 19 份原执行回执、18 项可消费证据和 1 项浏览器缺口。恢复可定位声明路径、媒体索引、生产者与原回执。跨 lane 的交付回执被拒。单独的 Candidate 正例和媒体失败反例覆盖实际结果预检与准入。文件切片、CLI 补读、错调用和缺见证使用正式结果提交入口测试。Linux 宿主测试在专属临时 cgroup 中使用真实内核挂载与模拟输入，验证跨进程转移描述符被拒、授权撤销和卸载。

### 回归中发现并修复的问题

| 触发条件 | 修复及核验 |
| --- | --- |
| 8×8 的文本来源与浏览器媒体出现在同一份清单，按文本查询时尝试将二进制解码为 UTF-8 | 精确按来源 ID 查询，全文检索跳过二进制正文。两条冻结清单均完成重放，旧描述符的序列化字节保持一致。 |
| 给所有 Worker 追加挂载说明，让旧 Judge 简报超过 12 KiB | 仅向已配置原生读取的 Worker 注入说明，原有 Judge 简报预算用例通过。 |
| 浏览器声明路径指向不可消费的目录，记录缺口时重复检查同一路径并抛出异常 | 单独保留生产者声明与原失败原因。宿主回执回归覆盖目录、媒体不可用及恢复，不重复运行浏览器。 |

影响范围测试需要控制器依赖。验证使用工作树独立环境安装 dev、web、controller 和 native-inputs 四组依赖。

本轮记录的是模拟调用及冻结历史，真实模型端到端耗时、缓存 token、任务质量和双模型收益仍需另测。上述数字支持保留文本批页默认路径，不能据此缩短正在执行的合同或切换生产默认。运行数值对照可设置 `ZF_8X8_FIXTURE_STATE`、`ZF_8X8_FIXTURE_ATTEMPT` 后执行 `tests/e2e/test_workflow_input_replay_mock_e2e.py`。宿主用例是 `tests/test_artifact_mount_host.py`，需专属的 `zf-input-mock-*.scope`，并设置 `ZF_TEST_INPUT_MOUNT=1`。
