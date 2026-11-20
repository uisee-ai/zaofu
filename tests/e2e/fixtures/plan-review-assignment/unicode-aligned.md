# 标题导出候选

来源 R-U：标题长度以 Unicode 码点计数，允许 1–4，不做 normalization；非法输入
非零退出，不覆盖旧导出文件。只有命令行入口，无 HTTP。中文和 emoji 都适用。
Task EXPORT 拥有 export.py、test_export.py，mandatory AC-U 引用 R-U；命令 U 为
`python -m unittest test_export`，task_verify/task_non_smoke。README 人工可读性
由后续交付验收负责，与自动行为断言分列。所有产品测试尚未实施，NOT_RUN。

测试矩阵 U-BOUNDARY：Given 旧文件存在；When 分别输入空串、a、abcd、abcde、
中文测试、😀😀😀😀、e 加组合重音两码点；Then 空串与 abcde 拒绝且旧字节不变，
其余输入成功、原码点序列写回。以上覆盖每个分支，不将 UTF-8 字节当长度。
source_index.traceability 有 R-U 原文定位；覆盖账本把 R-U 映射到 EXPORT/AC-U/U。
没有 expected_result、parameter_coverage 或 stage_projections 复制字段。
