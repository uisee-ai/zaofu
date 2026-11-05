> 状态: done

# 监工恢复：事件窗口增量读取

用户要求恢复持续监工。实况19 done、0活跃operation，watcher CPU约90%，Run Manager最后tick为19:00，隐私19:19阻塞未继续处理。重启仅控制面后，用临时faulthandler抓到生命周期usage反复read_days全量解析112MB账本；重启不是根治。

- EventLog.read_days窗口读取增量化 -> verify: 稳态重复读取不解码旧行，追加只解码新完整行；原有归档选择不变。
- 轮转/截断/替换/删除与签名 -> verify: inode/version变化失效，不跨签名或allow_unsigned污染，不消费未完整写入的行。
- 加载补丁恢复控制面 -> verify: 观察Run Manager新tick与真实任务派发；不将心跳存活计为业务推进。

RED：新增回归1 failed / 5 passed（稳定读取仍全量解码）。仅缓存只读投影，不改事件账本、canonical状态、语义判断或重试上限。

进一步证据：直接停摆原因是quiescent gate仍被旧P07B orphan升级evt-4f29b137ac9b钉住。P16B新的task.done及已接纳dev.blocked不在旧fanout进展表内；所以Run Manager全体跳过。日志全量扫描是额外CPU开销，不能将其当作唯一根因。用户恢复授权通过goal set产生evt-e42948353dbf唤醒，不改变重试配额。

- Task Pipeline 进展/失败与在飞状态 -> verify: kernel派发/完成或绑定operation及control_result_ref的结果唤醒监工；活跃task-stage阻止静默；终结op、heartbeat与未绑定自报不阻止静默。实际tick_services caller必须能到达Run Manager。新增RED为7 failed / 1 passed。

验证：EventLog/归档/签名/Watcher/生命周期闭包103 passed；Task Pipeline静默/实际tick服务/升级收敛/operation闭包58 passed。sentinel 50 passed / 1 skipped，flow mock 10 passed；未运行全量pytest。生产账本只读基准：17193事件首次1.033s，重复0.0003/0.0002s。

操作员受控唤醒后：evt-44764cd97c4d退出quiescent，evt-74175aaa2572启动Run Manager，evt-7029e54721ab自动请求隐私discovery，evt-dbd6bedb2001实际派发。此为操作恢复证据，代码补丁加载验证另行记录。

实现提交：1fd2eb5a fix: keep task pipeline recovery awake and fold event window reads。

加载证据：evt-5cc4831a7ccc，watcher 3559503→3592258；所有旧pane/PID保持，discovery operation wop-prd-post-verify-discovery-prd-flow-discovery-trig-evt-7029-86609a406ea7持续running。当前quiescent=False，Dashboard HTTP200；临时诊断进程已退出。产品仍19 done，后续语义修复由active产品任务继续追踪，本项不宣称产品完成。
