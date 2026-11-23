# 原 8×8 Candidate 阻塞与孤儿告警分诊

> 状态: active

用原失败及全部已准入成员回放 -> verify: 选择一个 OA checkpoint，预算、纯运行错误及未准入成员保持原恢复入口。

重放历史锚点孤儿告警 -> verify: 仅放行当前 Candidate 的有界语义分诊；两次 tick 仅一个请求，宿主执行次数不增加。
