# 计数器步长修复

现有 `counter.advance(current, step=1)` 忽略了 step。不要重构，只修复这个缺陷并添加回归测试。

必须满足：
- advance(4) == 5，保持默认步长兼容。
- advance(4, 3) == 7；advance(4, 0) == 4；advance(4, -2) == 2。
- 纯函数，不引入依赖、服务、网络或持久化。
- 将步长正数、零、负数的回归加入 test_counter.py，`python -m unittest discover -v` 通过。

写入范围：counter.py、test_counter.py。使用当前配置中已注册的 Issue 路线。
