# EventLogModel：真实入口、独立 oracle 与失败路径调研

> 文档定位（2026-09-30）：历史调研，正文“当前/建议/尚未实现”仅适用于各段所记时点，不是最新执行队列。现行方案见 [ZaoFu_TestKit.md](../03_plans_and_steps/ZaoFu_TestKit.md)，执行仍按既有暂停/跳过边界；本次不重跑或改写历史结果。
>
> 当前对齐入口：`tests/conformance/test_zaofu_testkit_s11_eventlog_model.py`。已有独立事件模型；正确检出失败不等于模型缺失，不把当前实现自动声明为去重通过。

调研日期：2026-09-14。范围：为 `ZaoFu_TestKit.md` §13 补充测试设计；只读源码、现有测试与官方 Hypothesis 文档，仅新增本文件，未运行测试、未调用 Provider、未修改 `src/`。下文“候选”均为源码推断，不能直接当作本轮已复现问题。

## 1. 必须保留的需求缺口

§13 把 `append、duplicate、archive、replay、非法事件` 与“ID 唯一、追加顺序、replay 等价”放在同一行，但当前 `EventLog.append()` 和 `EventWriter.append()` **均不保证按 ID 去重**。前者直接追加，后者完成 enrichment/schema gate 后仍直接调用前者。不能把 Writer 当成已有幂等入口，也不能靠允许模型重复就宣称原“ID 唯一”要求完成。[§13](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/03_plans_and_steps/ZaoFu_TestKit.md:562>)、[EventLog.append](/home/uisee/zaofu_v7/src/zf/core/events/log.py:77)、[EventWriter.append](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:87)、[实际追加调用](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:113)。

应明确分两层：

- 原始 occurrence 模型：每个成功的主动 append 都产生一个 occurrence；重复 ID 输入也保留其次数、内容和顺序，用于重放和查询一致性检查。一次 append 后的自动轮转不能额外复制或漏掉旧 occurrence。
- Conformance 唯一性：独立检查器继续把重复 ID 账本判为不符合“ID 唯一”。主动造重复输入应验证检查器确实拒绝，**不能把该检查器通过等同于生产入口已防重**；生产端何处承诺幂等仍需追溯。已有 S2 case 正是这个检查器测试，没有断言 append 拒绝重复。[S2 duplicate case](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s2.py:70)、[唯一性检查器](/home/uisee/zaofu_v7/tests/testkit/oracles.py:77)。

现有 S11 只生成新 ID 的 append/reopen，参考表只含 ID、type、marker；没有 duplicate、archive、非法输入或 Writer schema gate。[当前状态机](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_hypothesis.py:22)。保留现有测试，另加有界模块，比改变旧测试的声称范围更清楚。

## 2. 源码契约

### 写入、校验与重复

| 入口/模式 | 实际行为 | 对测试 oracle 的要求 |
|---|---|---|
| `EventLog.append(ZfEvent)` | 加路径锁，日期轮转，再大小轮转，然后编码并追加一行；不检查已有 ID | 参考表按 occurrence 追加；ID 集合不能代替列表 |
| `EventWriter` disabled 或无 registry | 不做 payload schema gate | 缺 required 的 payload 在此模式下并非预期拒绝 |
| `EventWriter` warning | 违规原事件先写入，再写 `event.schema.violated` | 预期两行且顺序固定，warning.causation_id 指原 event.id |
| `EventWriter` blocking | 违规原事件不写入，写一条 `discriminator.failed`，方法返回替代事件 | 预期一行替代事实，保留 blocked ID/type/payload 与规定的身份字段 |
| 未注册的 event type | loose，`validate()` 返回空 violations | 不能只因事件类型未知就判为非法 |
| 两种 schema meta event | 绕过 schema 校验，防止递归 | 可做固定边界探针，不应生成无限 warning/failure 链 |

来源：[log.py](/home/uisee/zaofu_v7/src/zf/core/events/log.py:77)、[Writer 三模式与 meta 绕过](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:93)、[warning 字段](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:119)、[blocking 字段](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:144)、[registry.validate](/home/uisee/zaofu_v7/src/zf/core/verification/event_schema.py:277)、[现有模式测试](/home/uisee/zaofu_v7/tests/test_event_writer_schema.py:61)。

Writer 还可能补齐 correlation/causation，并在配置 default_origin 且原 origin 为空时写入 origin。为了把本模型限定于账本和 schema，可让输入显式带固定非空 correlation_id、causation_id、origin；另用固定 case 检查自动 enrichment。需要比较这些字段时，从独立参考历史计算父事件，不能调用 Writer 的 `_enrich()`、`_latest_task_event()` 生成 expected。[enrichment](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:198)、[origin](/home/uisee/zaofu_v7/src/zf/core/events/writer.py:89)。

### 归档与重放

1. `EventLog` 没有独立 `archive()` API。真实用户入口是下一次 `append()`：active 的 mtime 日期不等于“今天”就先日期归档；不能只手动 rename 文件并声称覆盖了自动轮转。[调用次序](/home/uisee/zaofu_v7/src/zf/core/events/log.py:77)、[日期轮转](/home/uisee/zaofu_v7/src/zf/core/state/rotation.py:27)。
2. `ZF_EVENT_LOG_MAX_ACTIVE_BYTES` 非空有效正整数时，在**追加前**检查旧 active 是否已达阈值。因此当前 append 可以把文件推过阈值，下一次才轮转。大小分片名为 `YYYY-MM-DD-0001.jsonl`，遇已有文件递增 ordinal；日期归档的剩余 tail 名为 `YYYY-MM-DD.jsonl`。[size rotation](/home/uisee/zaofu_v7/src/zf/core/events/log.py:109)。
3. `read_all()` 按归档路径字典序读取，再读 active；不按 event.ts 排序。同一天的 `-0001/-0002` 在 `.jsonl` 前，所以正常单调时间下“大小分片 → 次日日切剩余 tail”排序正确。单调时间、ordinal 小于 10000 是首个有界模型的明确输入范围；不要用任意回拨 mtime 人为制造历史倒序。[list_archives](/home/uisee/zaofu_v7/src/zf/core/state/rotation.py:95)、[read_all](/home/uisee/zaofu_v7/src/zf/core/events/log.py:388)。
4. 日期归档碰撞会读取 archive+active、拼接写 archive，然后删除 active。无故障情况下应保持两个输入片段一次且顺序不变；崩溃窗口另见 ZF-TEST-013。[碰撞分支](/home/uisee/zaofu_v7/src/zf/core/state/rotation.py:58)。
5. `read_all()` 和 `read_from_offset()` 只消费最后一个换行之前的完整行；未完成的尾行不产出事件，也不推进其消费位置。offset 是 active 字节位置；只有新 active 比旧 offset 小时才自动回到 0，这不是完整跨分片重放游标。[offset](/home/uisee/zaofu_v7/src/zf/core/events/log.py:220)、[完整行快照](/home/uisee/zaofu_v7/src/zf/core/events/log.py:260)、[已有半行回归](/home/uisee/zaofu_v7/tests/test_events_archive.py:177)。
6. `read_days(1)` 当前明确只读 active；`last_days>1` 才枚举日期范围归档。因此今天的 size shard 不会进入 `read_days(1)`。若测试要声称“今日所有事件”，必须先确认产品承诺，不能把“active-only”悄悄解释成含当天分片。[read_days](/home/uisee/zaofu_v7/src/zf/core/events/log.py:430)。

测试时用一个只前进的逻辑 UTC 日期控制 `rotation._today_utc()`，每次实际写入后将 active mtime 设置为逻辑日期内单调推进的独立步时间。只替换时间输入，调用真实 append/rotation。`ZfEvent.ts` 与文件 mtime 分开建模；ts 可在专门乱序探针中逆序，文件 mtime 不要回拨。每次动作给不同 mtime_ns，避免普通状态机被人为“同 mtime、同 size”缓存命中支配。[轮转使用 mtime](/home/uisee/zaofu_v7/src/zf/core/state/rotation.py:46)、[read_all 缓存键](/home/uisee/zaofu_v7/src/zf/core/events/log.py:328)。

### 索引不是 occurrence 真值

`EventIndex.observe()` 的 `_event_by_id` 以 ID 覆盖；任务 ID 列表只抑制相邻重复，flush 合并时进一步去重并按 ts 排序。`events_for_task()` 只要这些 ID 都能在当前实例内恢复，就直接返回缓存，不检查 active 文件新鲜度。冷启动只加载 ID 映射、不信任持久化 payload；缺 payload 时退回账本扫描。[observe](/home/uisee/zaofu_v7/src/zf/core/events/index.py:62)、[任务查询](/home/uisee/zaofu_v7/src/zf/core/events/index.py:106)、[load](/home/uisee/zaofu_v7/src/zf/core/events/index.py:163)、[flush](/home/uisee/zaofu_v7/src/zf/core/events/index.py:244)、[EventLog fallback](/home/uisee/zaofu_v7/src/zf/core/events/log.py:410)。

因此不能把 index 的字典覆盖语义拿来规定 canonical log 去重。主动重复输入下查询表现不同可记录为契约待澄清；优先用**全新 ID、两实例交错追加**验证“缓存不能遮蔽账本事实”。现有测试也明确不允许把篡改的持久化 index payload 当真值。[现有投影隔离测试](/home/uisee/zaofu_v7/tests/test_events_log.py:235)。

## 3. 有界合法/非法输入设计

建议测试自有类型 `testkit.checked` 和小 schema：

```python
{
    "testkit.checked": {
        "required": ["token", "verdict", "detail"],
        "non_empty": ["token"],
        "enum": {"verdict": ["ok", "reject"]},
        "nested": {"detail": {"required": ["marker"]}},
    }
}
```

使用真实 `EventSchemaRegistry.from_dict()` + `EventWriter(..., schema_mode="blocking")` 执行 SUT。oracle 不调用 registry.validate；按下面“单个破坏点”的事先分类给出 expected violations。schema 可由测试 fixture 传入，无须修改仓库 `zf.yaml`。[registry 构造入口](/home/uisee/zaofu_v7/src/zf/core/verification/event_schema.py:208)、[required/non_empty/enum/nested 语义](/home/uisee/zaofu_v7/src/zf/core/verification/event_schema.py:1808)。

| 类别 | 示例 payload | 独立预期 |
|---|---|---|
| 合法 | `{"token":"a","verdict":"ok","detail":{"marker":0}}` | 原输入一行，全部稳定字段保持 |
| 缺必填 | 合法 payload 删除 `token` | failure，`payload.token / missing_required` |
| 空必填 | 合法 payload 的 `token=""` 或 `None` | failure，`payload.token / empty_required` |
| 错 enum | `verdict="unknown"` 或 `0` | failure，`payload.verdict / enum_mismatch` |
| 错 nested | `detail=[]`、`0` 或 `None` | failure，`payload.detail / type_mismatch` |
| 缺 nested 必填 | `detail={}` | failure，`payload.detail.marker / missing_required` |

注意 `required` 只检查键是否存在；单靠 required 不禁止 None/空字符串。`non_empty` 目前只把 None、空 list/tuple/str 视为空，不把 `{}`、`0`、`False` 自动视作空；不要超出该实现承诺随意生成“非法”。[具体条件](/home/uisee/zaofu_v7/src/zf/core/verification/event_schema.py:1808)。

原始损坏注入属于测试专有 fixture 输入，通过临时文件追加 raw bytes 后调用真实读取入口。它和 Writer payload 违规须分开标记：

| 原始行类别 | 样例/策略 | `read_all()` 预期 |
|---|---|---|
| 完整坏 JSON | `not-json\n`、`{"type":\n` | 跳过，不合成业务事实；后续完整合法行仍出现 |
| 顶层不是对象 | `[]\n`、`null\n`、`0\n` | 一个 synthetic，原因 `top-level must be object` |
| 缺少/非字符串 type | `{}`、`{"type":0}` | 一个 synthetic，原因 `missing or non-string \`type\`` |
| 非对象非 None payload | `{"type":"x","payload":[]}` | 一个 synthetic，原因 `\`payload\` must be a dict` |
| 合法 UTF-8 行的一部分 | 生成完整事件编码后按合法边界切分，先写无换行前缀 | 无输出、消费 offset 不越过前缀；补齐 suffix+换行后恰好出现一次 |
| 兼容输入，非当前结构违规 | 空字符串 type、payload=None、额外未知字段 | 不默认判为 malformed；未知顶层字段被过滤 |

来源：[decode](/home/uisee/zaofu_v7/src/zf/core/events/log.py:144)、[未知字段兼容](/home/uisee/zaofu_v7/src/zf/core/events/model.py:43)、[malformed 现有测试](/home/uisee/zaofu_v7/tests/test_events_log_malformed.py:35)。首版不混入签名 envelope：它走不同验证分支，属于额外输入域。

半行状态存在时，只允许 `complete_tail`、读取和 reopen；普通 append 和轮转规则先要求 `pending_tail is None`。否则会把新事件编码直接接到旧半行上，测试需要额外定义“旧写入永久中断”的语义，超出首版的完整 append 参考模型。

## 4. 状态机、oracle 与 shrinking

建议一个测试模块同时包含生成式主状态机与少量固定故障探针。主状态至少保存：独立 `history` occurrence 列表、可重用输入的 handle 表、逻辑日期、当前 active 原始字节、历史分片预期顺序、未完成 tail，以及每个 raw occurrence 的位置/分类。输入 ID 固定为计数生成，task ID 从 2–3 个短值选择，marker 用小整数，文本有固定最大长度；无需随机 UUID。[当前计数 ID 的先例](/home/uisee/zaofu_v7/tests/conformance/test_zaofu_testkit_s11_hypothesis.py:43)。

| 规则 | SUT 真入口 | 参考状态更新/每步验证 |
|---|---|---|
| `append_valid` | `EventWriter.append` | 事先构造完整稳定输入字段，追加一个 expected occurrence |
| `append_invalid_payload` | blocking Writer | 事先根据类别构造 failure 语义；检查替代一行、violations、blocked body 与身份 |
| `append_duplicate` | 对已生成的同 ID 输入再次 `EventLog.append` | occurrence 加一；唯一性检查器必须判拒绝；不声称入口幂等 |
| `append_raw_bad` | 测试注入器追加完整 raw 行，真实 `read_all` 读取 | raw 表加一，按预分类选择跳过或 synthetic |
| `begin_tail` / `complete_tail` | 注入器两段写，真实读入口 | 第一段仅 pending，第二段转为完整 occurrence |
| `advance_day` | 推进时间输入，下一次真实 append 触发日切 | 独立记录分片分组，history occurrence 顺序不改变 |
| `enable_size_rotation` / 正常 append | 临时设置阈值，真实 append | 明确旧 active 大小决定下一次是否分片 |
| `reopen` / `replay` | 新 `EventLog(path)`、真实读取 | 与同一独立 history 比较，不能只和旧实例的返回值互比 |

独立 oracle 建议分三项：

1. **物理账本**：从测试输入序列形成“完整行字节 + 未完成尾部”的预期；按模型中的分片顺序独立读磁盘，检查拼接字节流、行数、末尾换行边界及顺序。归档路径/存在性是 actual，不用 SUT 的 `list_archives()` 帮 expected 排序。Writer 自动生成的 failure ID/ts 是输出身份：先检查其格式、唯一性以及全部预测语义，再将这两个不透明值绑定到该 occurrence，供后续字节保存/replay 使用；不要把整个返回事件或实际解码结果直接 append 为 expected。
2. **语义账本**：合法行比较 type/id/ts/actor/task_id/payload/causation_id/correlation_id/origin；完整坏 JSON 的预期为无输出；synthetic 只规范化 id/ts，保留 type、actor、`line[:200]`、具体原因和在输出序列的位置。输入 raw 全文与 raw occurrence 序号/字节位置保存在 reference/evidence，不能只比较 malformed 数量。[synthetic 仅给 line/error](/home/uisee/zaofu_v7/src/zf/core/events/log.py:197)。
3. **重放只读性**：每次 replay 前后比较 active+archives 的原始字节与文件清单；只读不追加 synthetic。`EventLog.close()` 会 flush index，因此 canonical 文件与可重建 index 分开检查；`read_all()` 的浅复制也不能证明返回事件对象可任意修改。[close](/home/uisee/zaofu_v7/src/zf/core/events/log.py:101)、[只读 synthetic](/home/uisee/zaofu_v7/src/zf/core/events/log.py:144)、[缓存浅复制](/home/uisee/zaofu_v7/src/zf/core/events/log.py:349)。

若要直接断言生产投影提供的 raw 位置，可另读 `iter_event_records()` 返回的 raw_segment/raw_offset/raw_length/raw_line，并与上面的独立行表对照；注意该 API 把坏 JSON 也合成为 malformed 并占 seq，不能套用 `EventLog.read_all()` 的“坏 JSON 跳过”输出数量。[EventRecord](/home/uisee/zaofu_v7/src/zf/core/events/segments.py:61)、[segments 读取语义](/home/uisee/zaofu_v7/src/zf/core/events/segments.py:190)。

Hypothesis 官方说明：`Bundle` 让规则输出供后续规则复用，适合 duplicate 和父事件引用；precondition 根据实例状态过滤不适用规则；invariant 在每步后检查。precondition/invariant 不能直接访问 Bundle，所需状态应同时存在实例字段。失败输出给出可复制的短动作程序。建议 Bundle 返回整数/字符串 handle，不返回 ZfEvent，便于现有 JSON evidence 捕获。[官方 stateful 文档](https://hypothesis.readthedocs.io/en/latest/stateful.html#rule-based-state-machines)、[preconditions](https://hypothesis.readthedocs.io/en/latest/stateful.html#preconditions)、[invariants](https://hypothesis.readthedocs.io/en/latest/stateful.html#invariants)。

实施预算建议从 `max_examples=30, stateful_step_count=30, derandomize=True, database=None, deadline=None, print_blob=True` 起步，动作参数使用短值并把“最简单合法值/单一违规类别”排前；实际预算由主代理验证后定。这是本调研建议，不是官方默认值。为每项复杂动作保留固定短序列 case，避免依靠随机覆盖率保证必达。

继续使用 `ModelEvidence`：`@record_action` 放在 `@rule` 内层，`@record_invariant` 放在 invariant 内层；异常在 teardown 前保存完整 fixture 字节、参考状态和动作序列。first/latest 是失败候选，不能称为 Hypothesis 最终最小反例；原始终端反例与动作重放仍需核对。[现有证据契约](/home/uisee/zaofu_v7/tests/testkit/model_evidence.py:1)、[capture](/home/uisee/zaofu_v7/tests/testkit/model_evidence.py:107)、[replay_steps](/home/uisee/zaofu_v7/tests/testkit/model_evidence.py:174)。

## 5. 优先核验的失败路径

| 优先级/状态 | 最短操作方向 | 独立期望与注意点 |
|---|---|---|
| 高：新候选，未运行 | 实例 A append 新 ID a/task T；实例 B 同路径 append 新 ID b/task T；A 查询 T | `[a,b]` 与 reference 一致。A 的 index 可能仅返回 a；新建冷实例多半正常。全部新 ID，避免把主动重复误判 |
| 中：新候选，未运行 | 同实例追加 a(ts晚)、b(ts早)；close；原实例查询 T | 追加顺序 `[a,b]`；flush 的 ts 排序可能改成 `[b,a]`。须确认该查询承诺 insertion order；不得改 oracle 按 ts 排序掩盖差异 |
| 必须覆盖，当前源码看正常 | 同日 append 形成 `-0001/-0002` 分片；推进一天后 append，产生旧日 `.jsonl` tail | 原始 occurrence 顺序完全保持；这不是已知缺陷，优先加入生成动作和固定正常探针 |
| 既有已登记 | 日期碰撞归档写后、active unlink 前崩溃，再恢复 append | 自动搬迁重复旧 occurrence，见 ZF-TEST-013；不同于主动再 append 同 ID，不应新建重复 issue |
| 明确限定承诺 | size 分片后 `read_days(1)` | 当前只返回 active。若产品要求完整今日窗口则另追 caller；首版 `read_all` 不因此失败 |
| 更大后续输入域 | rotation 后新 active 大小大于/等于旧 cursor，再 `read_from_offset(old)` | byte-only cursor 不能凭大小识别所有代际变化；需要真实 watcher/cursor 承诺与 caller 证据，别把此 API 单独当全历史 replay |

两实例 stale query 的源码依据为 [EventLog 直接接收 cached](/home/uisee/zaofu_v7/src/zf/core/events/log.py:418) 和 [EventIndex 完整性判断](/home/uisee/zaofu_v7/src/zf/core/events/index.py:113)。逆序 timestamp 的依据为 [flush 的排序与内存映射替换](/home/uisee/zaofu_v7/src/zf/core/events/index.py:244)。

既有问题对照：ZF-TEST-013 的恢复重复发生在没有主动 duplicate 请求的自动归档路径；ZF-TEST-001 是签名 append-fold 缓存掩盖内容篡改，不能用新 EventLog 实例等同冷进程来宣称已覆盖；原来 S11 的 reopen 仍共享进程级缓存。需要明确新模型的 replay 层级是新实例还是新进程。[ZF-TEST-013](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:1095>)、[ZF-TEST-001](</home/uisee/zaofu_v7/awesome-zaofu/New_function/Test ZaoFu_Project/04_audit_and_issue_ledger/ZaoFu_issue.md:1021>)、[进程缓存](/home/uisee/zaofu_v7/src/zf/core/events/log.py:29)。

## 6. 本项可验收范围

- append/duplicate/archive/replay/非法输入进入真实入口 → verify：状态机每一步都对独立字节/语义 reference 检查，固定短序列覆盖同日分片再日切、blocking 替代和半行补齐。
- 新候选获得证据 → verify：主代理在当前 HEAD 运行无重复 ID 固定探针，只有真实失败才登记；本调研没有执行该验证。
- 原“ID 唯一”保持未闭合状态 → verify：报告区分 conformance 检测、raw occurrence 保留与生产幂等入口；检测器用例通过不冒充防重实现完成。
- 失败可重放 → verify：保留最终终端动作序列、参考分类、raw 行与位置、完整 state 字节；不把 first/latest 候选宣称最小反例。
