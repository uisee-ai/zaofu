# 本地读取候选

原始要求 R1：editor/reader 可读取当前草稿，成功 CLI 0 / HTTP 200，返回当前值且不写文件。
R2：缺失/非法角色拒绝读取，CLI 非零 / HTTP 403，安全原因且旧文件不变。
Task CLI 与 HTTP 分别拥有自己的实现与测试，mandatory AC 完整引用 R1、R2；
两片独立 task_verify 各执行自己的 runtime 测试命令，受控工件交付证据。
产品尚未实施，证据 NOT_RUN。

矩阵 CLI-roles：Given 有有效旧草稿；When 缺失/非法角色读取、editor/reader 读取；
Then CLI 非零，安全原因，旧字节相同。
矩阵 HTTP-roles：相同输入组合；Then HTTP 403，安全原因，旧字节相同。
