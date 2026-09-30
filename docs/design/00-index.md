# ZaoFu Design Index

This index is the canonical route for architecture and runtime design notes in
the v7 source tree. Historical manual pages remain under `docs/manual/`, but
new architecture routing should start here.

## Current Routes

- [144-github-issue-workflow-ingress-and-controlled-fix.md](144-github-issue-workflow-ingress-and-controlled-fix.md) — `canonical-current` — GitHub Issue ingress, automatic read-only triage, and approved local fix delivery
- [145-verified-candidate-human-review-and-forge-delivery.md](145-verified-candidate-human-review-and-forge-delivery.md) — `accepted` — Verified Candidate 人工审核、本地不可变 review branch、人工 GitHub PR/merge 与只读状态同步
- [146-external-issue-evidence-package.md](146-external-issue-evidence-package.md) — `defer` — External Issue 用户、worker 与运行时证据统一打包、校验和受控发布（暂不实现）
- [147-self-issue-canonical-zaofu-root.md](147-self-issue-canonical-zaofu-root.md) — `superseded` — 历史统一源码/Fix 根规则，已由 152 替代
- [148-self-issue-external-issue-consolidated-pending-plan.md](148-self-issue-external-issue-consolidated-pending-plan.md) — `proposed` — Self-Issue 与 External Issue 待实现/修复功能、环境边界、证据、重试和全链路审查总清单
- [149-github-oauth-device-attachment-publication.md](149-github-oauth-device-attachment-publication.md) — `accepted` — GitHub OAuth App Device Flow、附件上传授权、发布前兼容性校验与可恢复降级
- [150-issue-triage-v22-controlled-projections-and-evidence-assets.md](150-issue-triage-v22-controlled-projections-and-evidence-assets.md) — `accepted` — v22 Issue Triage 受控筛选、queued 取消、事件生命周期与原文件资产下载
- [151-feishu-issue-merge-settings.md](151-feishu-issue-merge-settings.md) — `accepted` — Settings 驱动的 `/issue-merge` 飞书接入与受控 bridge 启动

- [152-issue-report-source-and-developer-execution.md](152-issue-report-source-and-developer-execution.md) — `implemented` — 用户上报源码与开发者 Triage/Fix 执行项目独立配置、跨项目入队和归属固定
