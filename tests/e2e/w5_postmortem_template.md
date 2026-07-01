# W5-E2E Baseline Post-Mortem

> Copy this file to `backlogs/experiment-artifacts/w5e2e-<timestamp>/postmortem.md`
> and fill in after the run. See `docs/runbooks/w5-e2e-baseline.md` §Post-Run.

- **Run start (UTC)**:
- **Run end (UTC)**:
- **Duration**:
- **Mode**: MVP (safe-team, 6 role) / Full (W5E2E-T1 shipped: critic + test_spec)
- **Worktree**: `/tmp/zaofu-w5e2e`
- **Branch**:

---

## 1. Per-Phase Result

Paste output of `python -m tests.e2e.w5_phase_report --state-dir /tmp/zaofu-w5e2e/.zf`:

```
<output>
```

Summary:

| Phase | Status | Reason (if not pass) |
|---|---|---|
| P0 Preflight | | |
| P1 Spec | | |
| P2 Arch ⇄ Critic (GAN) | | |
| P3 Test Spec (Full mode) | | |
| Build (dev) | | |
| Verify (gate + test + discriminator) | | |
| Review | | |
| Ship | | |

---

## 2. Critical Flags

From phase report "Key metrics" section. Fill counts (0 = good):

- scope.violation: __
- discriminator.failed: __
- worker.stuck (unexpected — 1-2 is tolerated as self-heal): __
- human.escalate: __
- review.suspended: __
- test.suspended: __
- hook.write_failed: __
- task.rework.capped: __

---

## 3. Cost

From `zf cost --by-instance` (approximate, based on usage events):

| Role/Instance | Backend | Token in | Token out | USD |
|---|---|---|---|---|
| orchestrator | | | | |
| arch | | | | |
| dev-1 | | | | |
| dev-2 | | | | |
| dev-3 | | | | |
| review | | | | |
| test | | | | |
| judge | | | | |
| **Total** | | | | |

---

## 4. Rework & Throughput

- Tasks dispatched: __
- Tasks done: __
- Rework ratio (rejected+failed / done): __  (target ≤ 1.5 per W5 acceptance)
- Wall-clock / done: __ min

---

## 5. Answers to Key Questions

1. **Did Layer 2 split user intent into tasks correctly?**
   - Feature F-XXXX created? yes/no
   - Task count reasonable (3-5)? yes/no
   - Each task had contract (behavior / verification / scope) set before dispatch? yes/no

2. **Did the GAN loop actually happen?**
   - `gan.round.started` seen with round=1? yes/no
   - Did reviewer reject round 1 (per spec, `gan_rounds=2`)? yes/no
   - Did round 2 converge (arch.proposal.done + review.approved)? yes/no

3. **Did dev workers touch only their assigned files?**
   - `scope.violation` count: __
   - Actual file overlap between 3 dev tasks? (inspect `git diff`)

4. **Did review / test / judge each gate correctly?**
   - Any task reach `done` bypassing a gate? (look for `task.done` without prior `discriminator.passed`)
   - review.suspended triggered anywhere? (unexpected for baseline)

5. **Did Layer 2 close the feature when all tasks were done?**
   - `feature.status_changed` to done? yes/no
   - progress.md reflects completion? yes/no

---

## 6. New Bugs Found

Format: **B-W5-NN**: 1-line title (file:line or reproduce cmd)

- B-W5-01:
- B-W5-02:

---

## 7. Zaofu Self-Heal Events

Count how often zaofu recovered without human intervention (these are
good — they prove the harness self-corrects):

- worker.respawned: __
- worker.recycled: __
- context.warning → recycle: __
- discriminator.failed → rework: __

---

## 8. Suggested Next Steps

- Upgrade MVP → Full mode (ship W5E2E-T1 critic/test_spec roles)? yes/no
- Re-run with chaos injection (--inject=stuck / codex-500)? yes/no
- Specific bug sprints to open (list B-W5-NN IDs):
- Long-horizon baseline (LH-6 autoresearch loop) ready to attempt? yes/no

---

## 9. Artifacts Archived

Paths of preserved state files (run `cp` commands from runbook §Post-Run):

- events.jsonl: `backlogs/experiment-artifacts/w5e2e-<ts>/events.jsonl`
- kanban.json: `.../kanban.json`
- progress.md: `.../progress.md`
- role_sessions.yaml: `.../role_sessions.yaml`
- session transcripts: `.../sessions/`
