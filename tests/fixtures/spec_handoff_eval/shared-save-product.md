# 保存与正式入口，共享失败状态约定

当前隔离项目开发 Python 3.11 标准库模块，不依赖第三方库、网络、数据库或界面框架。README.md 为简短中文用法文档；无需制作 Web 页面。

R-SAVE（P0，必需）：save_service.py 提供 save(current, draft, write)。current 和 draft 都为字典，write 为外部注入的同步可调用对象。必须恰好调用 write 一次，以 draft 的深拷贝为参数；write 正常返回后，才返回 {"state": draft的深拷贝, "input": {}, "error": None}。write 抛出 Exception 时返回 {"state": current的深拷贝, "input": draft的深拷贝, "error": "save_failed"}。不捕获 BaseException。
R-SHARED（两入口共用，必需）：调用结束后 current、draft 及其嵌套内容保持调用前值；即使 write 修改收到的参数再失败，也不得污染输入或返回的保留状态。同一输入可再次重试，失败不得发布新状态。保存次数与状态的断言均为行为验收，不能只检查返回错误字符串。
R-ENTRY（P1，必需）：app.py 的 submit(current, draft, write) 是正式调用入口，委托 save，行为完全一致，不绕过共享规则、不增加持久化格式、并发、账号或新业务校验。
R-VERIFY：python -m unittest -v 为验收入口；覆盖成功、失败、失败后重试、嵌套对象、write 修改副本后失败、BaseException 传播；在功能入口与正式入口分别独立断言次数、内容、错误及输入未变。全部使用内存测试数据，不修改项目真实文件。README.md 给两个入口的简短中文调用例子。

只规划两个任务：T-SAVE 交付功能入口、共享规则、本片测试和用法；T-ENTRY 依赖前者，交付正式入口及测试、完整 README，独立复验两入口共享行为。共享义务正文集中一次，每片完整写明引用、准备/清理、RED/GREEN、当前与最终验收责任。不要求首片提前运行尚未生产的 app.py；不得删除后继最终复验。

本次只生成 PRD 和完整 Plan Preview。Owner 人工审批后才可实施或执行 Git 交付；后续允许隔离项目内标准受控 Git 提交。本轮没有待用户选择的产品取舍，工程方案可由 Planner 明确并由 Critic 评审。
