# 客户名册 CSV 导出（最小业务任务）

实现标准库 Python 命令行程序 `export_csv.py`：用户可以将 UTF-8 JSON 客户列表转换为 CSV，方便导入表格。
接口固定为 `python3 export_csv.py input.json`，CSV 写 stdout；无数据库、Web、网络、外部 Python 依赖。

验收条件：
- AC1：输入为对象数组，每条记录有字符串 name 和 email；CSV 第一行为 name,email，数据顺序与输入相同。
- AC2：中文、逗号、双引号通过 Python csv.reader 往返后与原字符串相同；使用标准 CSV 转义。
- AC3：空列表只产生表头；成功退出码为 0。
- AC4：无效 JSON、非数组和缺少所需字段时退出码非 0，stderr 有可读说明，stdout 不产生成功数据。
- AC5：交付标准库 unittest，覆盖上述行为；`python3 -m unittest discover -s tests -v` 实际运行至少 5 个测试且通过。

建议单一有价值的纵向 Task 完成交付，不人为拆分无依赖意义的微任务。保留真实 Scan、Plan、独立 Verify 和 Judge。
最终交付必须针对实际冻结提交，不能用报告存在、实现者自评或退出码 0 代替验收行为。

附加 harness 运行验证（不扩展产品功能）：
Impl 在首次写代码前，使用当前 briefing 的 `recover assistance request` 向 orchestrator 提交一个只读问题：
“当前合同的 CSV 转义是否可以直接使用 Python 标准库 csv.writer？”证据引用当前 requirement.md，说明已读 AC2。
保留当前 attempt/lease/heartbeat；用返回的 request_id 读取绑定回答后按原合同继续。该求助不是重新计划或扩权请求。
独立 Verify 检查业务 AC；harness 操作员另行检查求助身份、回答、派发和最终交付事件，不把 harness 机制显示为产品功能。
