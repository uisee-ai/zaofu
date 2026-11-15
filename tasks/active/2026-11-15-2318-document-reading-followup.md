> 状态: active

# PRD / Plan 真实阅读缺口修复

依据用户批准的下一步与 `reports/spec207-generated-reading-20261115-1356/evaluation.md`；基线 a6a6dad5，原 worktree 实施。对应设计207。

- B01 空锚点兼容 -> verify: 实际Plan入口不再产生空锚点的 `[blocked]`，危险URL/事件属性仍不可执行，源码与下载保持原文。
- B02 默认审计信息减负 -> verify: PRD来源明细按需展开；宽目录以条目呈现，决定/风险/阻塞状态可见，所有字段可展开、原始表格可查；手机专注阅读保留退出和文档操作。
- B03 统一作者与评审方法 -> verify: PRD、Planner、Critic共享业务优先首屏方法，明确场景分段及真实原文审查证据；分发副本一致、依赖物化验证。生成方法改动不冒充新一轮真实全流程成功。
- B04 回归与交接 -> verify: focused build/unit/browser、上一轮真实API产物三屏宽复核，报告截图、源码绑定与未覆盖层级；本地提交并归档，不合并、不push。

不改已接纳PRD/Plan、事件或机器合同；不把阅读语义写成Kernel门禁。上一轮Channel runner退出143和其他流程问题另行诊断，本轮不扩展修复范围。Codex Desktop没有同文档参考，不能宣称像素对齐。

## 已完成实现与证据

B01：sanitizer后识别空具名锚点，保留安全ID及页面内跳转，不关闭URL过滤；真实Channel Plan占位10→0。
B02：宽目录按条目阅读且保留全部字段/原表；来源明细按需展开，风险/决定/异议可见；PRD手机正文654.22→760px，默认表格5→1。下载与原文不变。
B03：新增共享正文阅读方法并经既有依赖分发，作者/独立Critic记录实际原文位置与短引文；源与两份分发副本一致。本轮仅验证方法交付，不声称新生成稳定性。
B04：生产构建通过；精确改动闭包66 passed、1 skipped，81个前端单测通过；真实API三文档×三屏宽9项通过。Docker浏览器110/110通过（3.9分钟），模拟服务已停止并回收。

报告：`reports/spec207-reading-followup-20261115/evaluation.md`（gitignored，本地截图/命令/receipt）。
复现：`npm --prefix web run build`；`scripts/dev-verify.py run --base dev --paths <报告owned-paths.json的本轮路径>`；Docker浏览器入口与真实API探针见报告目录。
全worktree相对dev包含历史跨域改动；本批未跑905文件全量pytest。无新的Provider生成、产品实施或Plan批准。
