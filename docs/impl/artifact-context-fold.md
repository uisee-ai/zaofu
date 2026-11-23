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
  正常并发append若同inode增长、原snapshot已全部消费为完整行，且该完整前缀
  复核一致、复核期间签名稳定，
  可返回该冻结快照；仍保存旧offset/签名，下一次再读取尚未消费的tail。
  若原snapshot含未消费的partial尾，发生并发增长必须重试，不能只哈希前面的
  完整行就承认该快照。未变化的合法partial仍可缓存，下一次补齐后继续读取。
- 2026-11-21真实8路Reader暴露持续append导致前缀复核反复失效。现在仅在active
  前缀复核期间取得EventLog同一append/rotation锁（最长等待5秒），锁内重新取签名，
  仍核完整前缀、inode、大小与复核后签名。解码和工件正文选择在锁外执行；不增加
  全量扫描重试，也不跳过terminal或partial边界。独立进程竞态测试验证写方在短暂
  复核期间让路，后续读取继续消费tail；非协作改写的拒绝回归保持。
- signed 日志每次完整验证，不复用 unsigned 缓存。未变文件的复用依赖文件系统
  可观察的签名与归档不可变约定；ctime 增加了对恢复 mtime 的改写检测，
  不声称能检测文件系统完全不可区分的元数据/内容回写。

## 验证与限制

`tests/test_artifact_context_fold.py` 覆盖追加解码计数、partial补齐、原义务过滤、
同长恢复mtime、改写后增长、替换/截断/旋转、已有 EventLog cache 的连续竞态、
signed重复验证；现有 artifact/hook/CLI caller 回归继续验证授权与读取边界。
测试中的 ctime 场景等待一个文件系统时钟刻度，避免把不可区分元数据当成可检测变化。

补充纠正（2026-11-13）：旧归档候选 `63c36fbb` 在合并前被Root与独立review
发现第三项P1，因此未合dev、未部署，原落地结论撤回。真实
`io.BufferedReader(8192)` 读取含40KiB settled行的文件时，在started解码后
等长覆盖settled后缀为无newline内容，能读满原snapshot剩余范围并被当作partial；
最终恢复原内容并append时，仅验证已消费前缀会漏掉调用前已有的settled。
这不是短EOF或不可区分metadata，而是未验证partial区域的增长竞态。
新增实际 `_require_current_operation` 回归先失败（错误接受），增加上述
完整snapshot边界条件后重试并拒绝 `operation_terminal`。前两项P1
（无后验fallback轮转漏终态、边界前EOF/短partial误接受）回归继续保留。

同一10,000条fixture（3归档+active）在追加1条后逐事件序列化结果完全相同：
旧版0.116695秒，新版0.000715秒。冷读旧版0.125463秒、新版0.128133秒；
这是补充前的局部微基准，不是 provider E2E 或固定性能承诺。
第三项P1补充后源码 `06df77413d9776015b03475250f24da4e8f5cd92e83b9588fcfe4d8334429be7`
重测同10,000条起点与同1条追加，并对cold/append全部序列化结果逐项对比：
dev基线冷0.105955秒/追加0.103102秒，新版冷0.116722秒/追加0.002401秒。
这验证完整snapshot追加路径仍可复用，不把部分snapshot重试成本隐藏为缓存命中。
本轮未重复扫描运行中的LS日志；此前只读实测保留如下，明确属于补充前版本。

第三项P1补充前的源码 `e3f7cf517c7dc8fcc66031026cc4d1b00c9b3220c302a201db9249518a397632`
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
