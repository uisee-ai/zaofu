# 事件账本追加中断后的行边界保护

> 状态: active

LS 在磁盘耗尽时 `events.jsonl` 出现 3 条部分写入后直接拼接下一条完整事件的损坏行；完整后续事件因行级 JSON 解析而丢失。已有原始账本只读保留，此任务修复未来 `EventLog.append` 遇到未终止尾行时的处理，不对历史残片推断业务结论或直接改写状态。

- 先前追加被 ENOSPC 或进程终止截断 -> verify: 下次受锁追加保留所有历史字节，令残片保持无法作为有效事件接纳，下一条有效事件独立且可由 replay / watcher 消费。
- 上次写完 JSON 但未写换行 -> verify: 不因后续追加把未提交的旧记录意外接纳。
- 多字节 UTF-8 截断与已签名事件 -> verify: 后续有效事件独立，原损坏内容不被当作真实成功。
- 对完整 JSONL、轮转及并发 -> verify: 现有 EventLog / EventWriter / event archive 回归与 premerge sentinel 通过。

验证：EventLog / EventWriter / 轮转 / Watcher 定向 104 passed；并行收集修复后 `tests/test_events_log.py` 与 `tests/test_runtime_watcher.py` 41 passed。完整影响回归 14258 passed、20 skipped、7 项既有基线失败（4 项未跟踪 export 脚本、1 项未跟踪 prompt、1 项飞书确认动作、1 项 mock 流程依赖 Claude 登录）；serial 10、sentinel 53 passed。首次广回归因测试在 collection 时生成随机 ID 导致 xdist 3 项 collection error，已改为固定值并重跑。
