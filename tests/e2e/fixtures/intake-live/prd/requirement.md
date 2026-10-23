# 整数分金额汇总命令行工具

在本项目交付一个仅使用 Python 标准库的命令行小工具；没有网页、HTTP 或外部服务。
金额用整数分，避免浮点数。两个可独立验收的责任是业务汇总与命令行接入，必须有明确依赖和各自测试。

## 业务汇总

在 `ledger.py` 实现 `summarize(rows)`。rows 为含 category 字符串和 amount_cents 整数的字典列表。
返回 `{"total_cents": 整数, "by_category": {分类: 整数}}`；允许负数退款、零金额和 Unicode 分类。
空列表返回 total_cents=0 和空 by_category；拒绝布尔值、浮点数、缺字段或空分类，抛出 ValueError。

## 命令行

`python ledger_cli.py INPUT.json` 读取 UTF-8 JSON 数组，调用上述 summarize，并输出一行 JSON。
有效输入退出 0；非法输入或文件不存在退出 2，在 stderr 给出可读错误，stdout 为空。
示例输入：`[{"category":"餐饮","amount_cents":1250},{"category":"餐饮","amount_cents":-250},{"category":"交通","amount_cents":300}]`。
示例结果 total_cents=1300，餐饮=1000，交通=300；不得修改输入文件。

## 验收

`python -m unittest discover -v` 覆盖业务汇总与真实 subprocess 命令行；
`python -m compileall -q ledger.py ledger_cli.py` 通过。
README.md 说明命令、输入和错误行为。写入范围：ledger.py、ledger_cli.py、test_ledger.py、test_ledger_cli.py、README.md。
不得删除或缩小上述验收。使用已注册 PRD standard 路线，经过 Planner/Critic 和计划批准后才实现。
