> 状态: done · `baa66c5f feat: confirm managed idle activity and report Codex subscription windows`

# 云端 Connector 空闲确认与 Codex 订阅窗口

依据 209、210，消费 Controller 新增的可选能力，保留旧协议兼容。实施见[云端 Runtime](../../docs/impl/208-cloud-runtime-delivery.md)。

1. 模拟达到阈值后新增终端、待补传、未知交付或未结执行 -> verify: 二次检查抑制确认，浏览器断开仍保留终端责任。
2. 测试稀疏及乱序的 Codex 限额窗口 -> verify: 最新独立窗口生效，非法数值、异项目/会话及未知字段不上传。
3. 运行实际 CLI 适配器调用路径并使用新旧能力组合 -> verify: 新 Controller 收到限定字段，旧 Controller 继续接收原用量合同。

`pytest -q --no-cov -m 'not perf and not host and not real_provider' -n 6 tests/test_controller*.py` 共 228 项通过。原生会话与空闲专项单独运行 20 项通过。修正了测试替换全局 sleep 对后台线程的干扰。wheel 包含 Connector 模块，仓库指令校验通过。

`python scripts/dev-verify.py run --base 940f98b8 --workers 8` 的广域确定性层为 15,766 通过、20 跳过、9 失败。9 项失败在未修改基点逐项复现，涉及 Feishu 确认、计划判断、两种 pending 引用场景、公开导出脚本和独立 prompt。调度器在该层退出，另行执行同一计划的串行层，10 项通过。整仓仍有这些基线失败。
后续真实 watcher 卸载与多租户恢复见[后续任务](../active/2026-11-18-1020-watcher-quiesce-and-restore.md)。
