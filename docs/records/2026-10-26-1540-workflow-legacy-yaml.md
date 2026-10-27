# 旧版 Workflow Raw YAML 兼容

关联：[187 实施说明](../impl/187-ai-sdlc-graph-workbench.md)。本次继续在 `wip/codex-20261026-workflow-graph-ui` 修复，未 commit/merge。

tt 的 `workflow-79e2bcedae7ad383` 为 running revision 2；旧 effective-config 不包含 `yaml`，前端因此错误显示等待准备。原始 private candidate 仍存在且摘要匹配。本次通过只读详情投影恢复其完整脱敏 YAML，重启 8002 后从 0 字符恢复为 6532 字符；Proposal digest 和 effective-config descriptor 均保持不变，见 [before](2026-10-26-workflow-legacy-yaml/before.json) / [after](2026-10-26-workflow-legacy-yaml/after.json)。

兼容路径只在已验证的旧快照上生效：复用 private candidate 路径验证，限制读取大小，同时匹配快照来源摘要与 Proposal 目标摘要，再解析、脱敏和序列化全部 YAML 文档。候选丢失、损坏或不安全时，只展示冻结的编译配置并标明来源；不可变快照本身损坏则保持不可读。不会用当前项目 `zf.yaml` 替代历史内容，不回写快照或事件。运行中的 Raw 仍不能修改。

验证范围：

- 后端新增 12 项全部通过；覆盖真实 HTTP 调用、旧快照的合法摘要、当前配置漂移、候选丢失/损坏/越界/符号链接/大小上限、脱敏、现代快照兼容及快照损坏拒绝。相关现有 85 项通过，覆盖 Workflow 请求/阻塞候选/图投影、sidecar 和 config apply；未跑全量 pytest。首次组合运行 96 passed、1 failed，失败为断言未考虑 Web 二次文本脱敏；修正断言后重跑新增 12 项通过。
- 前端 build/typecheck/bundle check 通过；自动 Web typecheck 和模型单测结果见 [verification-final.log](2026-10-26-workflow-legacy-yaml/verification-final.log)。首次自动 Web 检查被 SIGTERM 中断（exit 143），单独重跑；保留原日志，不将中断视为通过。规划器的 80 文件宽泛 Python 层未运行，使用上述有明确范围的聚焦回归。
- Sentinel gate：50 passed、1 skipped。Docker Playwright：2 passed，覆盖编译快照来源/不可读提示，以及原有审阅、启动和运行跳转链。首轮浏览器夹具只拦截 `default`，漏掉真实项目别名；修正拦截范围后全部通过。
- 8002 真实 tt、yy 只读浏览器检查：Raw 文本与 API 完全一致；tt 桌面/手机可见、Agent 输入禁用、无横向溢出；无页面异常，没有发送修改或启动动作。预览 context 查询单独计数，见 [live-final.log](2026-10-26-workflow-legacy-yaml/live-final.log)。[tt Raw 截图](2026-10-26-workflow-legacy-yaml/tt-7c45327e-raw.png)、[手机截图](2026-10-26-workflow-legacy-yaml/tt-mobile.png)。

使用 Docker `mcp/playwright:latest --network host`，没有安装宿主浏览器。临时 8003 服务已停止，按配置的状态目录追加 `simulation.done` 后清理，见 [cleanup](2026-10-26-workflow-legacy-yaml/cleanup.json)。8001 未变；8002 继续运行最新 worktree 构建，启动时间见 [health](2026-10-26-workflow-legacy-yaml/health.json)。本次不重复真实 Provider 或游戏生成 E2E。
