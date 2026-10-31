> 状态: active

# 常驻 Run Manager 使用启动时绑定的 CLI

来源：Owner 授权监工记录问题、修复并继续；dl Run 的建议事件
`evt-3b81bc851f34` 记录裸 zf 命中旧 checkout。当前扫描正常，修复不重启在飞角色。

范围：resident briefing 的 status/emit 与 rebind 提示、精确回归和实现文档。

- 复现旧 PATH CLI 被调用 -> verify: 新回归在原 HEAD 下两例失败。
- 绑定当前 CLI 与 shell 参数 -> verify: 隔离执行生成命令，旧 CLI 返回 97，绑定 CLI 返回正确 argv/状态目录/JSON。
- 保持启动和重绑定行为 -> verify: resident 与 cli_command focused tests。
- 运行验证计划与机械门 -> verify: impact closure、sentinel 和 flow smoke。

方法说明：[CLI 绑定](../../docs/impl/resident-cli-binding.md)。

验证：resident/cli_command 三文件 27 passed；premerge sentinel 51 passed；
flow smoke 10 passed；ruff 与 diff check 通过。dev-verify 将提示模块归到整个
orchestration 依赖闭包并选择 157 文件，按本次仅命令渲染的范围跳过 broad deterministic
和无关锁串行层，以上述精确测试覆盖生成、启动、重绑定和公共 CLI 解析。
收据：`/tmp/zf-resident-cli-verification-20261031.json`。绑定 CLI 的真实 dl status 返回 active。
