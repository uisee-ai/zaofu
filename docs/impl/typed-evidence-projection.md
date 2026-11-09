# 结构化证据兼容投影

语义结果的证据可以是旧式字符串引用，也可以是包含 ref、digest、scope 等字段的
JSON 描述符。`result_submit._compatibility_projection` 在合并交付工件引用时按完整值
稳定去重，不要求引用可哈希，不将描述符转换为字符串或仅按路径去重。

该投影仅兼容旧报告消费者；原语义结果仍由既有 profile adapter、read ledger、
admission 与 EventWriter 决定是否接纳。异常恢复必须使用原 operation 的授权提交入口，
不能直接改写 operation 或伪造完成事件。

回归测试 `tests/test_result_evidence_projection.py` 覆盖混合引用、嵌套 scope、同路径
不同 digest、交付引用合并和真实 Orchestrator 提交/settlement/hydration。
