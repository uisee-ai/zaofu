# 任务驱动 Workflow 准备、澄清与完整 YAML 预览

> 状态: done；实现提交 `56656f5 feat: unify workflow preparation and YAML previews`；用户于 2026-10-25 明确批准“生成 backlog 实施”。

## 目标与边界

Channel Group、Kanban Agent、CLI 的 Workflow 意图统一经过需求收集、语义选路、必要问答、配置装配、Preview、明确批准启动。复用 Workflow Request/Synthesis/Proposal 和受控配置应用，不新增运行状态机、不依关键词判断、不修改运行后的 plan_approval。仅跟踪/direct Task 不自动发起模型或执行。

现有显式 route/propose/apply 保留兼容；自动准备不得隐式覆盖 zf.yaml 或触发 Run。语义决定由 Agent/skill 承担；Kernel 校验 Task/Channel 来源、版本、摘要、配置和目录。完整 YAML 对外脱敏，原候选保持不可变并用于精确批准。

## 实施项

1. 统一 Workflow 准备入口，CLI 支持从 Task/需求发起、查看与回答；Kanban/Channel 创建 Workflow Task 后接入同一准备流程。
2. 澄清答案持久化并按新 revision 续行 Synthesis；未决、取消、旧版本与失败不启动。
3. Preview 展示完整装配 YAML、配置 Diff、流程与预检；复用既有 Apply config / Approve & Run。
4. 更新 Agent 方法/提示及文档，覆盖三入口与重试/来源/批准边界。

## 验收

- 三入口提交 Workflow 需求 -> verify: 同一 Request/Synthesis 合同，创建与准备期间无 invoke、无 canonical YAML 写入。
- 模型要求澄清并回答 -> verify: AskUserQuestion 可见，答案进入新 Requirement，重合成后 Preview 绑定当前 revision。
- 配置候选完成 -> verify: Web 和 CLI 可读完整脱敏 YAML，Diff 与候选摘要一致，旧候选不得替代新批准。
- 明确批准 -> verify: 复用受控配置应用及启动路径；三入口 mock E2E 验证，无隐式 Run。
- 仅跟踪/direct 与旧显式 route -> verify: 不额外调用模型，既有回归保持通过。
- 验证 -> verify: dev-verify plan/run 的相关闭包、premerge sentinel、前端 build/typecheck、Docker 浏览器测试。

## 关联

- docs/impl/184-intake-semantic-routing.md
- docs/impl/185-preview-chat-revision.md
- skills/zf-workflow-start-planner/SKILL.md

## 实现记录

- 复用 Request/Synthesis/Proposal，新增共享准备函数、CLI prepare/preview、建单 prepare_workflow 标记和 Web 准备消费者；语义选路仍由 Agent 承担。
- 修复问题仅在 projection、页面读取旧 Requirement 的衔接；答案进入新 revision 后重新合成。
- 完整候选 YAML 在不可变 effective_config 快照内脱敏展示；Apply config/Approve & Run 沿用既有路径。
- Web 准备消费者不启动 zf runtime，两个项目并发、轮询覆盖所有已打开项目，未变化事件不重复消费。
- Task/Channel 来源、Owner、当前合同和版本在提交时重新校验；仅跟踪与旧显式路线兼容。

## 验证记录

工作目录：/home/uisee/workspace/zaofu-workflow-intake；Python 使用 PYTHONPATH=src .venv/bin/python。

- 初轮跨入口闭包 19 个模块（Workflow/CLI/Web/Channel/Task/Proposal/config apply/mock E2E）：288 passed、2 failed。失败是旧 Proposal 测试夹具缺少 objective 却声称 ready；补齐夹具后相关 34 项通过。
- 最终变更闭包：tests/test_workflow_prepare.py、test_control_actions_workflow_request.py、test_workflow_proposal.py、test_web_workflow_proposals.py、test_workflow_preparation_worker.py、test_channel_prd_task_contract.py、test_channel_task_plan.py：68 passed。
- 后续版本/来源边界及 mock E2E：37 passed；Channel Owner 批准与未授权无状态写入增补 1 passed；准备消费者多项目覆盖/自定义 state_dir 2 passed。
- dev-verify plan --base dev --tests tests/test_workflow_prepare.py --tests tests/test_workflow_preparation_worker.py：规划 292 个影响测试，按文件数扩大为全量。遵循 AGENTS.md 的领域闭包规则，使用上述显式闭包替代全量，不运行完整 pytest。
- dev-verify run 同参数 --skip-tier deterministic --keep-going --receipt /tmp/zf-workflow-prepare-automatic.json：serial、Web typecheck、Web unit 通过。并非全量验证。
- bash scripts/dev-premerge-gate.sh：manual docs currentness 通过，51 passed。
- npm run build：typecheck、Vite 构建、bundle budget 通过；Ruff、git diff --check 通过。
- 最终 CLI Task 入口问答/预览用例：1 passed；Skill validator 与三份分发一致性通过。
- Docker 浏览器最终结果：1 passed（9.4s）。mcp/playwright:latest、host network、API 0.0.0.0:8002。使用容器现有 Chromium 路径，不安装宿主浏览器。真实页面/API，Provider 和 runtime 启动边界为 mock；验证问答、新版 Preview、完整 YAML、390px 布局及批准前不启动。
- 真实 Provider 质量及真实运行器交付不属于本轮验证层。初次 Docker 运行遇到客户端默认浏览器版本路径不匹配，改为容器现存 Chromium；测试夹具补齐验证层字段并使用独立幂等键后通过。

- 临时测试服务已停止，4 个自建模拟目录记录 simulation.done 后清理；关闭钩子经独立启动/停止验证。未启动 tmux，未触碰 8001 开发服务。清理回执：/tmp/zf-workflow-prepare-cleanup.json。

原始日志：/tmp/zf-workflow-prepare-{focused,final-focused,boundary,automatic,sentinel,build,browser-final}.log。
