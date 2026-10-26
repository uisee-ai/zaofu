# 交接与证据

当前最终 candidate = revision-3。所有实现 Task 均已标为 done。
DEV 声明已完整执行 TDD：RED 输出是 `ModuleNotFoundError: optional_test_helper`；修好测试环境后测试通过，未保存目标业务断言失败。
AC-COUNT 的回归测试在实现前已有代码上首次就通过；DEV 未修改 query.py。
实现后的字段名清理发生于 revision-3。

当前 wrapper 允许复用 receipt U3：revision-3、精确命令 `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests`、结果 3 passed、deterministic=true、reusable=true，命令摘要与当前输入一致。
旧集成 receipt J2：revision-2、组合成功截图，未运行 revision-3 的组合探针；不能复用到当前目标。
本案例提供的 U3 是已核实样例事实；无需安装 pytest 或重跑该相同命令来获得相同计数。
允许读源码与执行新的纯内存风险探针；所需代码和标准库均已提供。
