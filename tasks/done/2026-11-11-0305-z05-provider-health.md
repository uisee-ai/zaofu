# Z05 实例 Provider 健康采集上报

> 状态: done

实现 commit：`5dd1fcc0 feat: apply runtime settings and report provider health`

来源：203/204 复盘缺口 1–3；用户明确授权分别生成 backlog 并实施。

## 验收

1. 真实调用边界和生命周期采集健康 -> verify: 成功、401、429、明确额度耗尽、超时及未知分类；不上传原始错误/密钥/正文。
2. 持久化版本化健康报告并由 Connector 投递 -> verify: 失败重试、恢复、过期不刷健康；中心 API 可见。

CPA 产品、SMTP/MFA、目标云集群与灾备容量仍归 Q01，不用本批确定性/隔离联验替代。

## 实施与验证

实现与合同见 `docs/impl/205-controller-policy-health-settings.md`。
- `uv run pytest tests/test_controller_policy_health.py -q --no-cov`：18 passed。
- Controller/managed/workspace/native/OTLP/config/EventLog 影响闭包：291 passed。
- 后续调用边界回归：91 passed；均与上述用例有重叠。
- `uv run python scripts/dev-verify.py run --base dev --workers 4 --skip-tier deterministic`：serial 10、sentinel 53、mock E2E 10、前端 typecheck/unit 通过。
- `uv run --with <controller-worktree> python scripts/check-controller-workspace.py --controller-root <controller-worktree> --managed`：隔离 PostgreSQL/Keycloak/HTTP 联验通过，含 settings digest 与 health。250 tokens 为测试回执，不是真实模型消耗。
- 本批未跑全量 Runtime pytest；前序已记录的六项基线失败不在本批范围内。

实现已在独立 worktree 中验证、提交并归档；Q01 生产资格保持独立。

最终模式切换与本地解绑回归：2 passed；观察模式移除项目无需读取已移除配置。

提交后联合复核：本仓库实现提交与 Controller `81859a14be06d83484d2986b0023c65cca7660d6`（干净工作树）实际 Compose/HTTP 联验通过。日志 `/tmp/zf-policy-health-committed-joint.log`；隔离服务与临时凭据已清理。
