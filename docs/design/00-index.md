# ZaoFu Design Index

This index is the canonical route for architecture and runtime design notes in
the v7 source tree. Historical manual pages remain under `docs/manual/`, but
new architecture routing should start here.

## Current Routes

- [144-github-issue-workflow-ingress-and-controlled-fix.md](144-github-issue-workflow-ingress-and-controlled-fix.md) — `canonical-current` — GitHub Issue ingress, automatic read-only triage, and approved local fix delivery
- [145-verified-candidate-human-review-and-forge-delivery.md](145-verified-candidate-human-review-and-forge-delivery.md) — `accepted` — Verified Candidate 人工审核、本地不可变 review branch、人工 GitHub PR/merge 与只读状态同步
- [146-external-issue-evidence-package.md](146-external-issue-evidence-package.md) — `defer` — External Issue 用户、worker 与运行时证据统一打包、校验和受控发布（暂不实现）
