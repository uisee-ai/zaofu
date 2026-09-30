# K2：Store 完整性与两进程更新的实施边界

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s2_store_integrity.py`。Store integrity、concurrency 和 projection_rebuild 检测已存在；已检出缺陷不等于检测框架缺失。

2026-09-14；HEAD `142a62a` 当前工作树。沿用 research 技能，已读 [Store 要求映射](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/02_research_reports/store_requirement_mapping_research.md>)，仅补损坏输入与 Feature/Session 并发实施所需边界。未运行测试、探针、网络或子代理；下列实际行为均为源码静态判断，不是新 Issue 的复现结论。

## 1. 读取入口、输入形态与当前行为

| Store / 真实入口 | malformed syntax | 正确解析、错误根类型 | 合法空状态 / 缺失 / 空文件 |
|---|---|---|---|
| TaskStore：`list_all/get` → `_load_raw`；构造函数只存路径 | 非空坏 JSON 在 `json.loads` 抛 `JSONDecodeError` | 非 list 根（如非空对象）返回 `[]`，无结构错误 | `[]` 是空 active board；缺失、空白文件也返回 `[]`；可能仍有 archive，不等于整个 TaskStore 没历史 |
| FeatureStore：`list_all/get` → `_load_raw`；构造函数只存路径 | 非空坏 JSON 抛 `JSONDecodeError` | 非 list 根返回 `[]` | `[]` 是空 active 集合；缺失/空白返回 `[]`；archive 另读 |
| SessionStore：`load`；构造函数只存路径 | 不完整 flow YAML 抛 `yaml.YAMLError` 子类 | 非空 list/scalar 到 `SessionState(**data)` 抛 `TypeError` | 缺失、空白、`null`、`{}`、`[]` 都进入 `ZfNotInitialized`；合法初始化由 `create` 生成完整 Session，允许 `workers=[]` |
| RoleSessionRegistry：构造函数立即 `_load` | 不完整 flow YAML 在构造/重新加载时抛 `yaml.YAMLError` 子类 | 非空非 dict 根直接 return，缓存成为空；无明确结构错误 | `{roles: {}}` / `{}` 可表示空 registry；缺失、空白、`null`、`[]` 经 `or {}` 也当空；后续 `get_or_create` 可创建 identity |

来源：[Task load](/home/uisee/zaofu_v7/src/zf/core/task/store.py:64)、[Task archive 查询](/home/uisee/zaofu_v7/src/zf/core/task/store.py:200)、[Feature load](/home/uisee/zaofu_v7/src/zf/core/feature/store.py:51)、[Feature 查询](/home/uisee/zaofu_v7/src/zf/core/feature/store.py:89)、[Session load](/home/uisee/zaofu_v7/src/zf/core/state/session.py:59)、[Session create](/home/uisee/zaofu_v7/src/zf/core/state/session.py:49)、[Role 构造/load](/home/uisee/zaofu_v7/src/zf/core/state/role_sessions.py:67)。

正向空状态必须选择所属格式：Task/Feature 的 `[]`、Role 的 `roles: {}`、通过 `SessionStore.create()` 得到的无 worker Session。不要把四个文件都写成 `{}` 或 `[]` 后期待相同结果。

空文件容忍是当前 Task/Feature/Role 明确的加载行为，不能仅凭它直接报损坏 Issue；“新空状态”与“原有非空 canonical 被截断为空”需分开场景。代码并不保存足够证据区分二者。若另测截断导致丢失，保留截断前真实 Store 快照并明确该恢复合同，不把缺失文件的首次初始化当作数据丢失。

## 2. 冷实例与已加载实例

- Task/Feature 每次 public read 和 mutation 都重新读取 canonical；没有跨调用缓存。Session `load/update/upsert_worker` 也重新读盘。先构造对象再损坏文件，随后只断言“构造未失败”没有覆盖任何读取。[Task mutation](/home/uisee/zaofu_v7/src/zf/core/task/store.py:222)、[Feature mutation](/home/uisee/zaofu_v7/src/zf/core/feature/store.py:121)、[Session mutation](/home/uisee/zaofu_v7/src/zf/core/state/session.py:67)。
- Role 的 `get()` 文档明确为 cached，`all()` 也只读 `_entries`。已加载实例在文件被改坏后仍返回之前值，单独不是“重新读取损坏 canonical 后静默接受”的证明。[get/all](/home/uisee/zaofu_v7/src/zf/core/state/role_sessions.py:169)。
- Role `get_or_create(new_instance)`、`update_instance_meta` 在持锁且文件存在时调用 `_load()`；用这些 public mutation 能检测已加载实例是否拒绝损坏。`_load` 先清空缓存再解析；抛错后内存缓存可能变空，不应把“内存对象完全未变”加成未经定义的 canonical oracle。[get_or_create](/home/uisee/zaofu_v7/src/zf/core/state/role_sessions.py:137)、[metadata mutation](/home/uisee/zaofu_v7/src/zf/core/state/role_sessions.py:209)。
- Role 若文件缺失，warm mutation 不会执行 `_load`，可能保留并重新保存现有缓存；cold instance 则从空开始。缺失/cold/warm 不能合并成一个参数后声称行为等价。[存在性分支](/home/uisee/zaofu_v7/src/zf/core/state/role_sessions.py:137)。

## 3. 最小损坏矩阵与精确 oracle

建议仅四 Store × 两形态 × cold/warm，共 16 个场景；合法初始化另作正向对照。每个场景先经真实 Store 建立有效 seed，保存 seed 字节与身份，再向隔离测试 canonical 文件注入以下内容。损坏注入是 fixture，不是生产恢复行为。

| kind | syntax fixture | 非空错误根 fixture | mutation 输入 |
|---|---|---|---|
| task | `{"broken":` | `{"sentinel":"damaged-root"}` | `TaskStore.add(Task(id="TASK-NEW", title="new"))` |
| feature | `{"broken":` | `{"sentinel":"damaged-root"}` | `FeatureStore.add(Feature(id="F-NEW", title="new"))` |
| session | `workers: [\n` | `[sentinel]\n` | `SessionStore.update(latest_event_offset=7)` |
| role | `roles: [\n` | `[sentinel]\n` | `RoleSessionRegistry.get_or_create("review-new")` |

表内 `\n` 指实际换行。用非空错误根避免与已有空状态分支混淆；不要把合法 YAML 标量误以为 syntax error。

冷例：注入后创建新对象并调用真实 read/mutation；Role syntax 可能在构造阶段已拒绝，应记录 phase=`construct`，不能声称 mutation 已执行。warm 例：注入前已通过该对象真实读 seed，注入后走 read/mutation；Role 的 fresh-read 使用新构造对象，warm mutation 使用原对象。

每个失败预期必须有：①明确拒绝（具体异常类型或既有拒绝结果）；②尝试操作后的 canonical 字节仍等于**注入后的损坏字节**；③无新 Task/Feature/Role 身份被保存；④未拿 projection/缓存拼凑新 canonical。不是要求注入后文件仍等于有效 seed——fixture 已经主动改变它。另保留 seed 作为前置事实证据。

Task/Feature 的错误根会让 `update(seed_id,...)` 返回 `None`，不会触发保存，容易出现“没写文件所以 pass”的假阳性；`add(new_id)` 才覆盖读取→写回的风险。源码预期它会用新列表覆盖错误根；必须实际运行后才能记录失败。[Task.add](/home/uisee/zaofu_v7/src/zf/core/task/store.py:222)、[Task.update](/home/uisee/zaofu_v7/src/zf/core/task/store.py:292)、[Feature.add/update](/home/uisee/zaofu_v7/src/zf/core/feature/store.py:121)。

Session 不要用 `create()` 做拒绝损坏的 mutation oracle：它不读旧文件，职责就是初始化写入；测试它覆盖已有损坏文件不能证明普通更新入口违规。`update/upsert_worker` 在 `_save` 前必经 `load`。[Session API](/home/uisee/zaofu_v7/src/zf/core/state/session.py:49)。

若 read 已静默返回空但 mutation 仍需检查，不要第一个断言就结束场景而遗漏破坏性写入证据：分别收集 read 与 mutation 的返回/异常/前后字节，然后用独立 oracle 汇总。不能 `except Exception: pass` 把任意环境异常归为正确拒绝；JSON syntax 用 `JSONDecodeError`，YAML syntax 用 `yaml.YAMLError`，Session wrong-root 用 `TypeError`（另验证未保存）。

合法对照：缺失 Task/Feature/Role 在首次 add/get_or_create 后可冷实例读回；合法空 list/role map 可追加；Session 缺失/空 load 与 update 应 `ZfNotInitialized`，随后显式 create→update→load 成功。每个对照都验证原本保留的字段/身份，不只看“没抛异常”。

## 4. Feature/Session 两进程的最小真实接口

| 场景 | 父进程 Given | 进程 A / 进程 B | 冷读最终 oracle |
|---|---|---|---|
| Feature 同记录非冲突字段（优先） | `FeatureStore.add(Feature(id="F-ONE",title="seed",priority=3))` | `update("F-ONE",title="A")` / `update("F-ONE",priority=1)` | F-ONE 同时 title=A、priority=1；id/status/created_at/user_message 与 seed 一致；仅一个记录 |
| Feature 不同记录（可选） | 经 add 建 F-A/F-B | `update("F-A",title="A")` / `update("F-B",priority=1)` | 两条均保留且各自只有目标字段改变 |
| Session 独立顶层字段（优先） | 父进程唯一一次 `create(project_root=...)`，保留 session_id/started_at/workers | `update(runtime_state="active")` / `update(latest_event_offset=7)` | 同时 active、offset=7；session_id/started_at/project_root/workers 不变 |
| Session 不同 worker 记录（可选） | 同一合法 Session，可有第三个 seed worker | `upsert_worker(WorkerState(role="dev",turn_count=2))` / `upsert_worker(WorkerState(role="review",turn_count=3))` | dev/review 各一次、值正确，seed worker 和顶层 Session 身份保留 |

Feature `update` 锁内 load→修改字段→保存；不选 done/cancelled，以免扩展成双文件 archive 原子性测试。[实现](/home/uisee/zaofu_v7/src/zf/core/feature/store.py:132)、[字段定义](/home/uisee/zaofu_v7/src/zf/core/feature/schema.py:21)。Session 两 API 都锁内 load→合并→保存；不同 worker 按 `role` 合并，不能让两进程都 `create`，后者合法重建初始 Session 会改变身份。[update/upsert](/home/uisee/zaofu_v7/src/zf/core/state/session.py:67)。

同步方案：使用 `multiprocessing.get_context("spawn")`、两个独立 Store 对象和 `Barrier(3)`；两子进程可先读 seed，把 ready 标识送回父进程，然后在 **public mutation 前** `barrier.wait(timeout=...)`。父进程确认二者已 ready 后参加 barrier，一起放行；子进程只调用真实 Store API并报告精确返回与异常，父进程有界 join 并用新 Store 读盘比较。[仓库现有 barrier 模式](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_multiprocess.py:90)。

这证明并发发起的更新没有丢失，不声称已穷举内核调度交错。若需要确认锁竞争，可在父进程持真实 `locked_path(canonical_path)` 时启动 spawn 子进程，等待两者报告 mutation 即将进入后释放锁；不要替换锁、load、save 为假实现。[实际文件锁](/home/uisee/zaofu_v7/src/zf/core/state/locks.py:38)、[canonical→锁路径](/home/uisee/zaofu_v7/src/zf/core/state/locks.py:68)。

不要在 `_load_raw/load` 返回后放“双进程同时到齐”的 barrier：这里已经持有排他锁，第一个进程等待第二个，而第二个被锁挡住，会人为死锁。同步位于 mutation 之前；必要的定向故障注入只用父进程控制的单方释放事件，不要求双方同时进入临界区。

进程层 oracle：两个 child 都完成、exitcode=0、两份成功回执；最终状态是两组非冲突 patch 的并集，未改字段等于 seed。`serial` 只防 pytest 外部并行，不能代替此处两进程并发。使用有界 barrier/join/queue timeout，finally 终止仍存活的确切 child、join 并关闭 queue。[已有 serial/进程测试](/home/uisee/zaofu_v7/tests/test_state_locks.py:19)、[现有清理](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2_multiprocess.py:533)。

## 5. 投影、登记与退出边界

Task terminal index 损坏可忽略并从 canonical archive 查询/重建，这是声明的投影容错；不等于 active Task JSON 或 archive 正文可被猜测为空。[terminal index load](/home/uisee/zaofu_v7/src/zf/core/task/store.py:98)、[get fallback](/home/uisee/zaofu_v7/src/zf/core/task/store.py:200)。Role 方法的旧注释称 metadata 为 runtime projection，不能据此覆盖 AGENTS.md 对 RoleSessionRegistry 当前状态所有权的要求或授权删除整个 registry。[该局部注释](/home/uisee/zaofu_v7/src/zf/core/state/role_sessions.py:209)。

主线程追加的明确相关重建风险：`refresh_spine_projections` 分别加载 `projections/{task_attempts,stage_spine,workflow_health,workflow_spine}.json`，但四份共同使用 attempts 的 `cursor_offset` 决定读取起点。只删后三者之一可能保留末尾游标而漏重建；只删 attempts 则从零重读，并向其余旧投影的 rounds/counters/milestones 再累加。这是源码推断，尚未复现。[共享游标](/home/uisee/zaofu_v7/src/zf/runtime/workflow_spine_projection.py:350)、[累加](/home/uisee/zaofu_v7/src/zf/runtime/workflow_spine_projection.py:472)、[四份写出](/home/uisee/zaofu_v7/src/zf/runtime/workflow_spine_projection.py:522)。

该风险的有界 oracle：用能分别产生四份非空投影的固定事件建立 baseline；独立场景分别删除四份全部（正向）与每个单文件（四个恢复负例），调用真实 refresh，逐一比较**四份完整 JSON**与 baseline，不能只检查刚删除文件或 attempt_count。过程保留 canonical Task/Feature/Session/Role/TaskAttempt、EventLog 和 required sidecar 字节不变；不要自行先清空剩余投影来掩盖单文件恢复缺口。若 baseline 无任何 stage/health/run 数据，对应场景不得计为有效覆盖；正式运行失败后再决定 Issue。

更正前一研究的“精确注册候选”措辞：其中 `file.py::nodeid` 只可用作覆盖映射/手选 pytest 闭包，**不能直接放入当前 SUITES**。S0 把每项当现存文件路径，并直接 read_text/AST parse；本轮应选有界完整模块，不扩改 runner/审计语义。[exists 审计](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s0.py:185)、[AST 审计](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s0.py:299)。前一文件按本轮单文件写入范围保留不改。

最小出口：损坏矩阵的每项都注明 cold/warm、错误发生阶段、前后 canonical 字节；合法初始化独立通过；Feature/Session 优先的两组并发 patch 完成真实 public API→落盘→冷读闭环。只有正式运行后的可复现失败才进入 Issue，异常分类和语义缺口不得被 blanket xfail 吞并。
