> 状态: done
> 实现: `96020efe feat: close PRD quality review gaps`
> 完成范围: ZaoFu 平台能力与确定性验证；真实模型/浏览器和 DL 外部证据仍按文末记录待验证。

# Spec-kit/OpenSpec 质量清单与阶段交接

## 来源与边界

原问题 5：生成并审核 Spec-kit checklist、spec/plan/tasks 交接。
用户已授权按 1–5 生成 backlog 并实施。设计依据：`docs/design/197-prd-spec-contract-and-readable-views.md`。

本项完成指 ZaoFu 方法、实际调用与验证能力完成；DL v1.3 实物基线、产品数值/部署批准、真实数据授权及产品测试证据仍需实际取得，不能据平台实现标为已完成。

## 实施范围

独立评估使用版本化质量清单，与源需求清单分离并逐项输出；PRD 附录保留来源基线、验收数据计划与交接索引。映射 canonical PRD → prd-plan/task_map/source_index；plan/tasks 尚未生成时记录后续生产者与触发条件，不要求虚构 spec.md 等固定文件。

## 验收

- 全文评估有源需求但遗漏通用质量检查 -> verify: 独立评估必须覆盖冻结质量 ID，报告绑定相同 PRD 与方法摘要
- PRD 导出及 Task 交接 -> verify: 同版 Markdown 保留 companion；原有 artifact refs/digests/AC 命令编译与 stale 保护回归通过

## 验证记录

- `uv run python scripts/dev-verify.py plan --base dev`：选择 41 个测试文件，覆盖变更模块、调用者、Web API、Task/Workflow mock E2E 及 Skill/文档合同。
- `uv run python scripts/dev-verify.py run --base dev`：607 passed，1 skipped（非 full pytest，未启用 host/real_provider）。
- 补充方法 ID 隔离、真实角色模板和未读 Owner 证据回归后，`uv run pytest tests/test_prd_quality_review.py tests/test_output_evaluation.py -q --no-cov`：25 passed。
- `bash scripts/dev-premerge-gate.sh`：51 passed。
- 另行核验原有 PRD→Task→Workflow mock 闭环：`uv run pytest tests/test_channel_prd_task_contract.py tests/e2e/test_channel_group_product_mock_e2e.py -q --no-cov`：18 passed。
- Ruff、diff whitespace 和五份 Skill 的 source/Codex/Claude 字节一致性通过。

以上证明机械合同、方法接入和确定性行为；不证明真实模型必然作出正确语义判断。

## 未完成的真实验证

- `docker image inspect mcp/playwright:latest`：指定镜像不存在。
- `docker pull mcp/playwright:latest`：registry 请求使用的代理 `10.9.100.141:31787` 拒绝连接；未启动浏览器，未安装宿主浏览器。
- `codex login status`：`Not logged in`；本轮未完成真实 Codex 讨论/评估复跑，也未生成新的 DL 正式 PRD。
- DL 旧版本部署/Schema/接口证据、产品阈值与策略批准、独立开发/安全真实签收及真实/脱敏数据验收仍须按实际产物完成。

恢复条件：Docker 能拉取指定镜像、Codex 登录可用；启动隔离 API/UI（监听 0.0.0.0，端口 8002+），通过 Playwright 继续 conversation/multi_lens 真实场景。产品验证还需取得上述外部事实和数据授权。
