# Z06 集中配置消费与模式切换

> 状态: done

实现 commit：`5dd1fcc0 feat: apply runtime settings and report provider health`

来源：203/204 复盘缺口 1–3；用户明确授权分别生成 backlog 并实施。

## 验收

1. 消费中心版本/摘要配置并返回应用结果 -> verify: 重启、回滚、同版本冲突、不兼容及本地许可。
2. 心跳/批次/诊断采样实际生效 -> verify: watch/serve 与 OTLP 实际消费者；全量计量不受采样影响。
3. 接入模式在无在途责任时应用 -> verify: 历史/迟到回执保持原模式，旧调用不被改账，新调用使用新模式；不自动更换个人凭据或虚构 CPA 适配。

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
