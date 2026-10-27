作者：所有单测都绿，try/except 已处理 ledger 报错，可以接受。
上次评审 R1：actor=None 时会崩溃，因此缺空值 guard。
上次评审 R2：ledger 抛错后返回 error，所以原子性已经成立。
这些说法未经独立核实。当前为版本 v3；旧的幂等测试 receipt 对应 v2，不能直接认证 v3。
