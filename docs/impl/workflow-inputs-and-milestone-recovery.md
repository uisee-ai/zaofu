# Workflow 输入、里程碑与调度

## 212 的读取与恢复

每次派发继续以原 request、actor、attempt、合同和输入策略为准。新建的 Candidate Verify、任务集成验收和 Thin Judge 可以分别使用 owner 在配置中固定摘要的切片证书。只有来源清单、目标、profile、完整原策略摘要和授权 selector 均匹配时才替换必读项。每个替换项需要列明义务。合同、目标、显式必读和原文的授权范围保留。既有 request 重放使用冻结的选择。当前 8×8 沿用原来的 40 项。

| 操作 | 获取的事实 |
| --- | --- |
| `zf artifact index --attempt ATTEMPT --source-id ID` | 按已授权的来源精确定位。通用文本检索会跳过二进制正文，媒体保留描述符。 |
| `zf artifact recover --attempt ATTEMPT` | 当前绑定、工作记录、历史已读与独立的 Provider 交付缺口。超过一页时沿返回的 `next_delivery_gaps` 命令继续。已定位的缺段用字符半开区间表示。 |
| `zf artifact workspace --attempt ATTEMPT` | 在持久工具会话前台启动只读挂载，初始化后打印 `INPUTS.md` 和 `RESUME.md` 的绝对路径。来源路径相对于该目录。查询、浏览目录均不构成必读见证。 |
| `/usr/bin/cat /绝对路径/inputs/来源.txt.chars-0-800` | 读取选中正文的字符区间。普通文本、CRLF、中文与空值保留原字节。二进制可原样读取，媒体的视觉验收由相应角色承担。 |

在同一 Codex 会话内容压缩时，若轨迹能证明部分材料已交付，`zf artifact read --required --resume` 保留当前上下文的重读位置，并返回 `continuation.historical_next_cursor`。先用 `zf artifact recover --attempt <id>` 找回工作记录，再按该游标补读历史交付缺口。轨迹确认当前上下文收到 `next_resume=continue_witnessed_delivery_gap` 提示后，再次执行 `--resume` 也会从已核验的交付缺口继续。省略 `--resume` 可从头重读。已有回执不能证明模型当前记得材料。

挂载试点使用 Linux、cgroup v2、FUSE、`fusermount` 及 libfuse2。项目可通过 `uv sync --extra native-inputs` 安装对应的 Python 适配器。在项目的 `workflow.flow_metadata.artifact_worker_cgroups` 中将具体 worker 实例映射到专属 cgroup 的绝对层级名。每次 open/read 都检查当前派发凭据、会话、进程、cgroup 和 grant 代次。直接 I/O 并禁用数据缓存，原进程退出、凭据轮换后旧描述符不得继续读取。当前版本只为与注册会话相符的 Codex CLI 0.171.0 单文件 `exec_command` 创建严格原生交付见证。旧合同、共享工作区、无法验证的包装器及不支持的 Provider 使用派发中原有的 CLI。

文件访问时的 caller PID、实际文件、冻结来源及读区间形成宿主见证。准入重新核对该会话中唯一 call ID 的参数和公开返回。完整输出及可精确复核边界的 Codex 截断片段可以累计，并可用 CLI 补齐中段。原生见证、CLI stdout、当前模型记忆和产品判词分别记录。挂载在产品仓库之外创建，停止后释放。挂载不创建产品文件。

选择新切片时，在 `workflow.flow_metadata.approved_review_scopes` 中按输出 profile 放置 owner 审查过的不可变工件描述符。证书使用 `approved-review-scope.v1`，包含 `approved_by`、`revision`、绑定的 `workflow_run_id`、`target_commit`、`output_profile_id` 与 `baseline_sha256`。每个替换给出原 source/artifact/digest/selector、对应义务以及新 selectors。覆盖不足或换版时继续使用原清单。验收对账及风险批准由合同责任人完成。

## 213 的接入位置

配置在 `zf.yaml` 的 `workflow.flow_metadata` 中。以下入口默认按项目选择启用。

| 配置或入口 | 行为及恢复 |
| --- | --- |
| `milestone_handoff: true` | Candidate Verify 新 request 冻结目标、成员、前驱判定、草稿来源及剩余义务。重放复用同一摘要。worker 通过 `zf artifact milestone --attempt ATTEMPT` 查看状态，本地操作员可在原 worker 退出后执行 `zf workflow milestone OPERATION`。 |
| `milestone-task-lifecycles` | 新 Candidate 在已有任务级调度证据时固定可选数据源。`$.summary` 分 Plan 版本与包摘要报告任务首次入场的峰值及共同区间；`$.tasks[i]` 提供冻结名单上该成员的派发、起止事件与重试标识。`zf artifact milestone` 可查看摘要。新的 OA 聚合失败检查点会从已绑定的 Reader 交接中获得同一摘要，旧派发保持原样。该时长统计内核运行区间，Provider 实际活跃时长需独立轨迹证据。 |
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

现行 CLI 默认使用 JSON 与 8 KiB 批页。B 是显式文本批页试验。三组调用数来自模拟工具返回，无法据此估算真实模型 token 或从派发到准入的耗时。运行数值对照可设置 `ZF_8X8_FIXTURE_STATE`、`ZF_8X8_FIXTURE_ATTEMPT` 后执行 `tests/e2e/test_workflow_input_replay_mock_e2e.py`。宿主用例是 `tests/test_artifact_mount_host.py`，需专属的 `zf-input-mock-*.scope`，并设置 `ZF_TEST_INPUT_MOUNT=1`。

## 里程碑接手、静默与独立作业

| 场景 | 实际入口 | 有效证据与失败处理 |
| --- | --- | --- |
| 静态检查解除门禁 | Kernel 静态门与 Stage Action 执行前后核对干净 Git HEAD；Candidate Verify 冻结质量门包含的命令 ID | 绑定 run、目标、任务图代次、合同摘要与完整命令集合。重放可复用相同候选已准入的逐命令成功回执。工作树变脏、漏跑、来源不符或目标变化时继续待证。 |
| 阶段屏障与关闭前 OA 接手 | 启用 `milestone_handoff` 后，命中冻结候选与 Reader 交接的 OA checkpoint 从源事件读取交接包 | 包含原 freeze、成员、合同、义务、缺口和下一责任实例。运输层等待旧 operation 静止后启动新会话。准入前再校验候选身份。相同唤醒只产生一次派发。 |
| 全板静默 | 在已启用的 `run_boards[RUN]` 加入正整数 `silence_seconds`；Supervisor 通过原有投影交给 Run Manager 的 `diagnose-attention` | 检查成员未完操作及尚未派发的任务。有效租约、同一 request/actor/attempt 的合法等待和有界工具执行宽限会推迟诊断。用量单列。未知原因交 RM，同一静默窗口重复巡检只派发一次诊断。 |
| 跨会话独立只读报告 | 在 cron 定义中设置 `action: agent_report`、`provider: ollama`、本机 `port`、`model`、`max_output_tokens`、`timeout_seconds` 及带摘要的 `input_refs`；可设置 `max_input_bytes` | 每个 occurrence 建立独立会话，验证角色范围与输入预算，先记录 started，再调用无工具的本机模型。报告及实际 token 落不可变工件。如果进程在调用中消失，结果归 owner 对账，禁止自动再发。手工触发沿用带授权令牌的原入口。 |

专项回归涵盖错 run、合同或目标的静态回执，OA 重复唤醒及过时结果，未派发任务上的静默告警，以及定时作业发送后失联、撤销与并发。运行 `tests/test_milestone_gates.py`、`tests/test_milestone_successor.py`、`tests/test_board_liveness.py` 和 `tests/test_scheduled_agent_job.py` 可复核。

## 本机真实进程与业务评测（2026-11-23 UTC）

使用独立的 Git 工程、运行目录和 CODEX_HOME。Codex CLI 0.171.0 通过环回 Responses 适配器访问本机 Qwen2.5。宿主 Ollama 0.6.2 不满足该 CLI 原生 OSS 接口版本要求，适配器仅供此项测试使用。每个测试单独启用。

| 真实观测 | 结果与边界 |
| --- | --- |
| Candidate Verify 中断与恢复 | 先在冻结 Git 目标执行一次 pytest，真实 Codex 在后续 turn 开始后收到 SIGKILL。重新打开角色注册表与原会话，从 RESUME 恢复，再将模型判词及宿主回执提交至 `SemanticResultSubmitService`。接纳事件 `evt-7b11061e4144`，恢复后新增宿主检查 0 次。首次 turn 19.85 秒、756 输入 / 2 输出 token；恢复 turn 45.64 秒、1554 输入 / 13 输出，报告的缓存输入均为 0。 |
| 历史上下文失真 | 一次试验中模型少复述一个审计标记字符。回归用例精确比对冻结标记，允许按原权限只补读对应来源一次，再核验后提交；仍不匹配则用例失败。上行成功样本无需补读。 |
| 独立巡检报告 | 一个真实 occurrence 取得独立 session 和有摘要的报告，模型只接收获准的冻结来源。Ollama 返回 246 输入 / 10 输出 token。 |
| 单、双模型业务对照 | 同一道 SQLite 应收对账题，执行者 Qwen2.5，双模型增加 Qwen2:7b 作 Planner。两个顺序各跑一次，每组允许一次返工。验收器在隔离数据库核对五行，参考 SQL 通过 5/5。 |

| 顺序 | 模式 | 秒 | 输入 / 输出 token | 最终业务通过 | 返工次数 |
| --- | --- | ---: | ---: | ---: | ---: |
| 单 → 双 | 单 | 174.31 | 323 / 171 | 0/5 | 1 |
| 单 → 双 | 双 | 233.66 | 798 / 332 | 0/5 | 1 |
| 双 → 单 | 双 | 174.80 | 792 / 319 | 0/5 | 1 |
| 双 → 单 | 单 | 102.12 | 318 / 167 | 0/5 | 1 |

反序试验中出现 SQLite 不支持的 `GREATEST` 和余额或顺序不符。该任务的双模型两次都花了更多 token 和时间，业务评分没有提高。项目维持原执行模型。两组顺序都使用相同输入与独立判定，每次耗时受本机负载、模型驻留和缓存影响。

复核以上真实用例使用 `pytest --no-cov -m real_provider -s`。在隔离测试环境为 `tests/integration/test_codex_milestone_recovery.py` 提供 `ZF_REAL_CODEX_LOCAL_URL` 和该环回端点要求的凭据。`tests/integration/test_worker_model_comparison.py` 的单、双模型试验设置 `ZF_REAL_MODEL_COMPARISON=1`，反序再设置 `ZF_MODEL_PAIR_REVERSE=1`。真实中断小样本从保留宿主回执到结果准入；原 8×8 的 40 项合同、两次断点和三种读法另有冻结回放。全量真实三组派发到准入的配对结果、以及原项目无人干预抵达最终交付，仍需各自独立取证。

## v5 配置、读取入口与 8×8 断点

根模板的公开入口为 `PrdFlow.spec.milestoneHandoff` 和 `PrdFlow.spec.artifactWorkerCgroups`。v5 启用里程碑交接，保留 WRC 的常规调度、独立 OA 与 RM，以及 8 个 Impl / 8 个 Verify。模板的 cgroup 映射为空。项目通过自己的 `zf.yaml` 给具体实例指定专属 cgroup，编译校验注册实例、Flow 归属及组之间的物理重叠。多 Flow 取对应 kind 的元数据；项目原有的显式覆盖优先。

| 谁在读取什么 | 核实后的入口 |
| --- | --- |
| Impl 编辑源码、运行产品测试；Verifier 检查候选源文件 | 读取实际产品 workdir。工作区里同名的文件自身无法充当已冻结 workflow 输入的交付证据。 |
| 已获准的 Codex Reader、Impl 或 Verify 的新 request | 首先用 `zf artifact workspace --attempt ATTEMPT` 启动有权限和期限的只读文件视图，随后直接通过文件路径读取 `INPUTS.md`、`RESUME.md` 和正文。挂载位于产品 Git 工作区之外。读取字节及 Provider 公开返回以相同会话和 call ID 对账。 |
| 原 request 没有冻结 cgroup、宿主或 Codex 版本不匹配、或授权撤销 | 使用原 `zf artifact index/read/recover`。给已经发出的 request 补一行 YAML 无法改变它的选路。 |
| OA 的语义 checkpoint、RM 的诊断与受控动作，以及结果提交 | 沿原有受控 CLI / 准入入口。没有把报告、权限检查、事件或终态写入搬进产品目录。 |

托管的 Linux tmux Worker 由 `managed_worker_cgroup(project_root, state_dir, actor)` 生成项目独占的 user systemd scope 路径。将该值固定到项目 YAML 后，正常启动入口把对应 Codex 进程安置到这个 scope。其他角色沿用原位置。已有活跃同名 scope 拒绝二次启动；外部托管的专属组仍由部署方安置。挂载还要求 FUSE、libfuse2 与匹配的 Codex 版本。宿主验证同时核对实际 PID 归属、跨组拒绝、授权撤销与原生工具返回。

### 原冻结断点的定向补读对照

两条 Candidate Verify lane 均使用原 40 项合同、19 条宿主回执和同一浏览器缺口。每次仅补读原 `target` 一项，用本机同一模型回答三个问题（候选短 SHA、缺口回执 ID、可用回执数）。第一条按 A/B/C 顺序，第二条反序。A、B 使用正式 CLI 页渲染器，C 使用正式文件视图的原文，三组同样收到恢复证据。以下只计本次模型调用，未计全量准入时间。

| lane 与顺序 | 格式 | 输入 / 输出 token | 秒 | 精确答对 |
| --- | --- | ---: | ---: | ---: |
| 0 · 先 | A，JSON | 2048 / 100 | 89.66 | 0/3 |
| 0 · 中 | B，文本批页 | 2048 / 100 | 57.91 | 0/3 |
| 0 · 后 | C，文件正文 | 1774 / 22 | 25.08 | 3/3 |
| 1 · 先 | C，文件正文 | 1769 / 26 | 27.39 | 3/3 |
| 1 · 中 | B，文本批页 | 2048 / 100 | 64.24 | 0/3 |
| 1 · 后 | A，JSON | 2048 / 100 | 68.28 | 0/3 |

A、B 都用满了本机该配置报告的 2048 输入和 100 输出 token。这个窗口限制影响成绩；不能由这两条 lane 外推生产模型或整份包的质量。服务未提供缓存命中 token。复核命令为 `pytest --no-cov -m real_provider -s tests/integration/test_frozen_checkpoint_reader_comparison.py`，用 `ZF_8X8_FIXTURE_STATE` 指向原状态、`ZF_REAL_READER_ATTEMPTS` 传入逗号分隔的两条 attempt。历史状态以只读方式打开。

另一真实小型 Candidate 在会话中连续两次遭 SIGKILL 后复用原会话。最终判词严格匹配后，产生唯一准入事件 `evt-9446560fd5b5`，目标 `d99bad92`，恢复后新增宿主执行 0 次。两条完成的 turn 分别耗时 47.90、2.99 秒，token 分别为 755/2 和 1557/12。本机 FUSE 试验分别通过真实模型自行选择工具、以及固定工具选择两种方式得到可核对的 Codex 原生文件交付。

### 8×8 原现场的无人恢复缺口

原 Run `workflow-ddd320aa0dc7c2ef` 已完成 16 项业务任务。在候选 `88992ce7`，两条 Verify 正式报告 blocked。四条验收义务引用的浏览器命令退出 0，宿主事件 `evt-cacecbb4aaf4` 保存了 9 个文件。原合同声明 `artifacts/evidence/browser/`，其目录无法与归档中的具体文件建立精确绑定。保留 18 项可消费的检查与 1 项待办。

聚合事件 `evt-36dfa17cd84c` 同时带有基础设施类别和已准入的依赖阻塞。旧 RM 只看类别并漏派 OA，工作流锚点随后被判为孤儿，`evt-6dc7582281e5` 令控制面进入静默。本轮修复仅将成员齐全、绑定一致、正式完成独立审核且全部报告该依赖缺口的聚合交给 OA。未知、未准入、超时、预算耗尽仍有各自负责人。当同一锚点已有上述分诊责任时，watchdog 不再另行报孤儿。对历史孤儿告警，仅允许同一运行、同一故障的语义审核，外部停止或审批仍有效。两次 tick 只产生一次合法请求，未启动新的宿主命令。

只读重放原始账本选出 `oa-candidate-review-aa4e7342f3c0e242976fcefa`。业务材料的等价性或需要修订的合同，仍由 OA/审批流程判断。原实验的无人资格已有人工介入并封存为 `closed_failed`。从该断点开始的验证单独评价。

专属文件视图也通过了一条完整 8×8 lane 的宿主验证。临时项目为同一冻结 Candidate 和 40 项输入建立新的授权 request（原 request 前缀 `9671fd06`，新 request 前缀 `37678252`，manifest 前缀 `05889beb`）。真实 `zf artifact workspace`、FUSE 和 Codex 执行 82 次文件读取。第 41 次后杀掉 Codex，原 SID 恢复并自行读取挂载的 RESUME.md，取得原缺口与声明路径，随后仅完成剩余 41 次。正式交付校验通过 40/40，恢复后新增宿主命令 0。模型选择命令由隔离的脚本固定，工具输出由真实进程产生；用例耗时 22.34 秒。可复核 `tests/integration/test_frozen_codex_workspace.py` 的环境说明。

2026-11-23 13:46 UTC，运行中的实验控制面从 `3841652d` 受控升级至 `bfbda3b3`，专用项目 YAML 为 lane 0 固定 cgroup。重载前后 4 个 tmux pane 及 16 项已完成任务均保留。RM 从历史故障自主发出 `evt-42f547657f65`；OA checkpoint 为 `evt-28dd510a3161`，实际开始事件为 `evt-4a5813f2545a`。该 OA 通过普通受控 CLI 读取审核资料。观察期间无人工向产品 worker 派发指令。部署与本次观察另保存在实验目录的 `v5-recovery-20261123T1335Z/`。

最终影响闭包以 6 个 worker 完成，确定性测试 13,796 通过、22 跳过；串行 4、契约哨兵 53、mock E2E 10 项全部通过。首次 8 worker 测试发现生命周期和 transport 模块超过体量约束，随后将孤儿处理、tmux 启动环境移到各自责任模块；另一个 Channel 等待超时在单独重跑及最终闭包中通过。真实 Provider、FUSE 与 8×8 冻结回放的证据另列如上。

13:54:55 UTC，原 Run 的 OA 提交结果 `evt-7742a8d5e186`。13:55:01 UTC，内核接纳 `escalate`（`evt-79a0c6162444`），应用事件为 `evt-632f05e75aad`，并将 `evt-8be6a2b5d4d7` 交给 owner。OA 从该断点核对了两个独立 Verify 判词；冻结策略要求的 5 个来源均有完整读取区间，另读了两条 lane 的结果。这次 OA 走受控 CLI；准入后原 16 个任务仍为 done，新增宿主命令 0 次。

OA 判定十个 finding 均需诊断执行证据，保留现有产品合同和候选。当前获准动作不含验证重新绑定。原始容器归档 SHA-256 为 `fa5f748965c4b071879843a64e12628421093bf43be87705b3007d76b0ae5b65`。现场复核了九个文件的实际字节摘要，拟议映射列出两份截图、两份命名录像、两份 trace 和环境 JSON；额外两个录像只作补充。待审核稿保存在实验记录 `v5-recovery-20261123T1335Z/browser-evidence-review.md`。准入阅卷、Judge 和最终交付仍需独立结论。

纯 Plan Critic 给出通过判词后，通用 `task_map.ready` 聚合读取该尝试的 source manifest 和审阅合同绑定的 Planner 子报告。打包前复核生产者集合和子产物摘要，沿用审阅时保留的引用。Planner 工作目录已被回收或下一次尝试复用了同名文件时，仍能交付原候选。缺少绑定或摘要变化会产生协议修复失败，语义返工成本为零。该交接仍须经过正常的产物包准入和 Owner 审批。

Owner 用当前候选的引用申请 Critic 复核后，新尝试把复核理由、反证引用和上一判词列入必读。复核沿用冻结的原生产者资料，索引仍可按需检索。判断跨任务的时序冲突时，Critic 同时核对当前计划中的前置陈述和实际依赖；单项验收命令的运行范围只说明该任务的验证方式。
