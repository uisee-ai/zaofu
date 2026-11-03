> 状态: done

# P2：并行查看多个项目使看板匹配缓存持续失效

用户授权的 dl 监工与看板修复续项。`33ecbb6d` 单项目预览刷新通过，正式
8001 服务验证仍滞后；16:06 后真实请求同时包含 dl 和 datal 看板，且工作进程
常驻约 2.3 GiB。第一版只留一个匹配结果，解码缓存又会在不同大型项目之间
淘汰；重复解码导致 epoch 变化，即使事件账本未变也重复全文匹配。

保留有界的每项目序号索引，按实际事件读取器的完整指纹复用，避免保留另一份
大型解码正文。说明见 [活跃看板的任务事件复用](../../docs/impl/board-task-event-reuse.md)。

- 两个项目轮流读取，解码缓存淘汰但账本未变 -> verify: 不重扫正文，返回本次事件对象。
- 指纹变化且 event ID 相同 -> verify: 不复用旧匹配；原追加及引用上限回归仍通过。
- 淘汰后账本追加 -> verify: 逐行 SHA-256 证明原匹配输入前缀，随后仅匹配新增行。
- 正式服务同时访问 dl/datal -> verify: dl 完整快照继续推进，Docker 浏览器无脚本错误。
- premerge 与合并后聚焦验证 -> verify: 不将既有无关启动审批红灯称为通过。

该项补齐上一项修复的部署限制；主流程 Critic 预算阻塞独立保留。

## 验证

- 原版本新回归 2 failed；最终聚焦看板/事件/SQLite/当前代次测试 53 passed。
- dev-verify 选择 100 个影响闭包文件；显式四批运行，1145 passed，1 项既有失败：
  `test_channel_execution_approval.py::test_controlled_start_accepts_later_owner_approval_and_preserves_prd`
  （已在无改动 dev `362e2fd0` 复现同样 409）。未运行 full pytest。
- 自动 Web typecheck/unit 通过；premerge sentinel 51 passed。
- 双项目真实数据回放：dl 初次 12.09 秒、切换返回 4.91 秒且不重复匹配；
  datal 追加 2 行只匹配这 2 行。
- 与正式服务相同的默认项目和工作目录下，Docker 同时打开 dl/datal；
  dl 快照 16:18:09 UTC / seq 25667、ready，94 卡片，0 page errors。
  证据：dl `artifacts/supervision/board-project-cache-browser-8002.json`、
  `board-project-cache-8002.png`、`board-project-cache-preview-2026-10-31.json`。

实现：`ac603c19 fix: preserve bounded board indexes across project switches`；
代码合并：`d4731914 fix: retain board progress during concurrent project viewing`。

正式 Web 已固定部署 d4731914。停止 8002 预览、重启正式 Web 后，Docker 双项目
测试确认新部署独立生成 16:20:32 UTC / seq 25670 的 ready 快照，94 卡片，
0 page errors；生成时间晚于新进程启动，没有复用预览快照冒充部署验证。
证据：dl `artifacts/supervision/board-project-cache-browser-8001.json`、
`board-project-cache-web-restart-2026-10-31.json`。
主 runtime PID 3273412 保持不变，Critic 的预算阻塞尚未恢复。
