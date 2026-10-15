> 状态: active

# 协作闭环验收与运行手册

来源：2026-10-15 Channel Group 实跑评估；用户已授权“生成 backlog，基于 backlog 实施”。

## 范围

补 deterministic 生命周期与 Docker Playwright：暂缓→新 PRD→同版本评审→Owner 关口，重放、缺失贡献、错误恢复。沿用已有执行与交付测试验证证据链，记录真实 provider 未复跑限制。

## 验收

运行相关 impact closure 与 sentinel → verify: 通过；Docker 浏览器验证暂缓/回执和 review/restart → verify: 通过；逐项归档验证与实现 commit。

## 边界

在独立 worktree 实施，不改真实项目业务状态、不自动合并部署或 push；语义判断留在 agent/skill。

## 实施与验证

- 影响闭包：`/tmp/zf-channel-collab-tests.txt` 中 173 个文件，pytest `-m 'not serial and not perf and not host and not real_provider' -n 4 --dist loadfile -q --no-cov` → 2241 passed, 11 skipped。
- `scripts/dev-verify.py plan --base dev --tests tests/test_channel_collaboration_closure.py` 识别了闭包。planner 因超过 80 文件自动建议全量；依照本次 AGENTS.md 的影响闭包规则，显式执行这 173 个文件，未运行整个仓库全量。无受影响 serial 文件。
- `scripts/dev-verify.py run --base dev --tests tests/test_channel_collaboration_closure.py --skip-tier deterministic --skip-tier serial` → Web 类型和 unit 通过；Python tiers 由上述显式闭包替代。
- `npm run build` → 类型、构建、bundle budget 通过；`bash scripts/dev-premerge-gate.sh` → 50 passed, 1 skipped。
- Docker `mcp/playwright:latest` / host network / 8002：`channel-owner-deferral.spec.ts`、`channel-review-restart.spec.ts`、`channel-fact-scope.spec.ts` → 6 passed。暂缓用真实 API/EventWriter/投影，review/restart UI 使用受控 fixtures。
- 生命周期 `test_channel_prd_file.py` 使用真实 review reactor 与 fake provider：带暂缓完整草案进入同版本角色评审，Owner 未明确接受风险时不确认、不建 Task/启动 Workflow。
- participant / synthesizer 分发副本在验证时与 canonical `skills/` 字节一致；临时生成副本与依赖 symlink 在交付前清理。

验证日志：`/tmp/zf-channel-collab-impact-final.log`、`/tmp/zf-channel-collab-browser-final.log`、`/tmp/zf-channel-collab-auto.log`、`/tmp/zf-channel-collab-build.log`、`/tmp/zf-channel-collab-sentinel-final.log`。

## 实跑边界

未在真实 newloshu 群组重启讨论或重跑付费 provider，未改 8001 服务。暂缓继承和提问质量的语义判断由增强后的技能与 prompt 承担，单次确定性测试不等价于真实模型质量保证；文件 composition 证明全文保留与减少输出重抄的机制，不声称已测得真实 token/耗时下降。历史设计索引未包含在当前 checkout，本批以现行代码、tests 与已有 impl 文档核验。
