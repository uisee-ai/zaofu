# Artifact/Hook 上下文的追加读取复用

`ArtifactContextEvents` 是 CLI 内的只读、保守过滤视图，由 artifact reader 和
`hook_context_events` 使用。事件日志仍是唯一权威；缓存不落盘，不改变授权、
当前 attempt/dispatch、required_reads、run aliases 或 preserve_event_ids 的判断。

## 原因与实现

LS-v1 监工观察到每个工具的 pre/post hook 都需上下文，旧版按全部文件签名缓存。
active 增长一行就重新扫描 9 个未变归档（约435MB）和 active（约22.6MB），
解码约10.56万条事件；只读计时每次约3.2–4.8秒。这个测量是函数开销，
不代表历史双 cat 30秒中的每个 hook 精确耗时。

- 每个文件缓存签名（device/inode/mtime/ctime/size）、最后完整行偏移、
  已消费前缀 SHA256 和过滤后的事件。缓存只属于本 reader 实例。
- 未变归档复用过滤结果。active 同 inode 且增长时，先重新哈希旧完整前缀；
  相同才复用旧事件并解码新增完整行。不是仅凭 size 或短边界标记推断追加。
  前缀不同、替换、截断、同长变更均重新读取该文件。
- partial tail 不推进 consumed；后续补齐后恰好读取一次。过滤规则保持原样，
  连被过滤行也计入前缀摘要，避免改变过滤内容时漏掉失效。
  只有读取完整个原snapshot剩余范围、末行无newline才是合法partial；边界前
  EOF/短partial必须重试，防止临时截断后恢复并append时漏掉原本已存在的terminal。
- 读前/读后核对文件签名，并复查归档 inventory。竞态清除缓存后重试一次；
  再失败抛出 OSError，明确当前读取不可用，不交付可能遗漏 terminal 的事件集合。
  既不调用可能命中旧 mtime/size 缓存的 `EventLog.read_all()`，也不降级为
  无 inventory 后验的完整扫描：后者同样可能在旋转时漏掉已存在的 settled。
  正常并发append若同inode增长、已读取完整前缀复核一致且复核期间签名稳定，
  可返回该冻结快照；仍保存旧offset/签名，下一次再读取尚未消费的tail。
- signed 日志每次完整验证，不复用 unsigned 缓存。未变文件的复用依赖文件系统
  可观察的签名与归档不可变约定；ctime 增加了对恢复 mtime 的改写检测，
  不声称能检测文件系统完全不可区分的元数据/内容回写。

## 验证与限制

`tests/test_artifact_context_fold.py` 覆盖追加解码计数、partial补齐、原义务过滤、
同长恢复mtime、改写后增长、替换/截断/旋转、已有 EventLog cache 的连续竞态、
signed重复验证；现有 artifact/hook/CLI caller 回归继续验证授权与读取边界。
测试中的 ctime 场景等待一个文件系统时钟刻度，避免把不可区分元数据当成可检测变化。

同一10,000条fixture（3归档+active）在追加1条后逐事件序列化结果完全相同：
旧版0.116695秒，新版0.000715秒。冷读旧版0.125463秒、新版0.128133秒；
这是局部微基准，不是 provider E2E 或固定性能承诺。

最终源码 `e3f7cf517c7dc8fcc66031026cc4d1b00c9b3220c302a201db9249518a397632`
的真实 LS ledger 只读观察（2026-11-13 14:47 UTC）：冷读4.390176秒，
保留105,883条；等待外部正常追加739 bytes后同inode热读0.036419秒，保留
105,884条。未向LS写事件或启动worker工具。早先旧版真实观测为冷2.895775秒、
下一次失效重读5.273733秒（105,747条）；两次观察的active/保留集合与时间不同，
不能计算严格同输入加速比。受控fixture的两版本起点、追加事件与完整结果才相同。
热读0.036419秒包含前缀校验；未变快照缓存命中的0.002秒级观察不是本次append
测量，也不是新hook CLI冷启动性能。

冷启动仍需完整扫描；每个 hook 都是新 CLI 时跨进程成本仍存在。active增长仍读取
旧前缀字节以证明不变，只避免重新解码；竞态频繁时可能重试或明确不可用。
未改 registry 写入、hook配置、调度或权限检查，也未在运行中的 LS 控制面部署。
