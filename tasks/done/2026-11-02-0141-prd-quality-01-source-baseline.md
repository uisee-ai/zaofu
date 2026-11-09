> 状态: done
> 实现: `96020efe feat: close PRD quality review gaps`
> 完成范围: ZaoFu 平台能力与确定性验证；真实模型/浏览器和 DL 外部证据仍按文末记录待验证。

# 来源与 v1.3 兼容基线的证据分级

## 来源与边界

原问题 1：补齐并核验 v1.3 兼容基线和稳定 artifact。
用户已授权按 1–5 生成 backlog 并实施。设计依据：`docs/design/197-prd-spec-contract-and-readable-views.md`。

本项完成指 ZaoFu 方法、实际调用与验证能力完成；DL v1.3 实物基线、产品数值/部署批准、真实数据授权及产品测试证据仍需实际取得，不能据平台实现标为已完成。

## 实施范围

在方法与评估中区分来源已读、内容可读、事实已核验；基线逐项记录版本、接口/Schema、环境、来源 ref/digest、核验方法与未取得证据。不得将目标设计或作者自述作为旧系统事实。

## 验收

- 来源 refs 存在但缺部署/版本证据 -> verify: 评估方法要求列证据缺口，不推导基线已核验
- 冻结评估方法与输入 -> verify: manifest/input-bundle 保存完整方法及摘要，三个阶段使用同一份方法

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
