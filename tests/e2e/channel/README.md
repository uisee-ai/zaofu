# Channel + Kanban Agent — Playwright Regression Scripts

UI behaviour regressions discovered 2026-06-05 (channel review w4xl2gi11
follow-up + user reports). Run them against a live `zf web --host 0.0.0.0
--port 8001` when triaging the same bug class — each script exits non-zero
on regression and writes a screenshot to `/snapshots/<scenario>.png`.

| Script | Bug class | Fix commit |
|---|---|---|
| `channel-dedup-verify.js`   | Channel @-reply body rendered twice when both local placeholder reply_request and real backend reply_request coexist for the same `(target_member_id, message_id)`. | `f760063 fix(web/channel): dedup placeholder reply_request against real backend entry` |
| `channel-dedup-negative.js` | Same UI shows two "Working" badges per single run when only the local placeholder exists (run header status + run activity collapse both render the status label). | `ba94611 fix(web/channel): drop duplicate Working badge from run activity collapse` |
| `kanban-agent-repro.js`     | First message in the Kanban Agent panel feels "hung" — typing + Enter produces zero `/operator/input` calls when the client-side action token isn't saved. | (reproduction-only — no fix shipped from this script) |
| `kanban-agent-verify-fix.js`| Verifies the alert (`headless-composer-alert`) surfaces immediately when the panel opens without a saved token, with `aria-invalid="true"` on the textarea. | `1c0b575 fix(web/kanban-agent): surface action-token gate before first send` |

## How to run

These scripts are pure Node + Playwright; they intercept the channel
API responses via `page.route()` and inject synthetic ChannelDetail data
to exercise the duplicate-render paths without needing a real channel
member or backend.

```bash
# 1. Start zf web (bound on 0.0.0.0, port 8001). Token only needed for
#    the kanban-agent-* scripts to exercise the post path; the channel-*
#    scripts mock the responses so token is irrelevant.
ZF_WEB_ACTION_TOKEN="$(cat ~/.zaofu/web-action-token)" \
  PYTHONPATH="$PWD/src" \
  .venv/bin/zf web --host 0.0.0.0 --port 8001 \
  --state-dir /absolute/path/to/.zf-state-dir

# 2. Run one of the scripts inside mcp/playwright (host network).
mkdir -p /tmp/zf-regression-shots
cp tests/e2e/channel/*.js /tmp/zf-regression-shots/

docker run --rm --entrypoint node \
  -v /tmp/zf-regression-shots:/snapshots --network host \
  mcp/playwright:latest \
  /snapshots/channel-dedup-verify.js
# exit 0 = PASS, exit 1 = behaviour regression, exit 2 = harness error
```

Each script writes its evidence screenshot beside itself in `/snapshots/`.

## Configuration

The scripts default to `http://127.0.0.1:8001` and `sample-project`.
Override them when running against a real dashboard:

```bash
ZF_E2E_BASE_URL=http://127.0.0.1:8001 \
ZF_E2E_PROJECT_ID=<project-id> \
node /snapshots/channel-dedup-verify.js
```

## Why these live under `tests/e2e/channel/` and not in `web/`

- The fix sites span `web/src/components/agent-session/channelProjection.ts`
  and `web/src/components/agent-session/AgentSessionTimeline.tsx`, but the
  symptom is end-to-end (browser → app bundle → mocked HTTP response).
- `web/` currently lacks a Vitest/Jest harness — `web/package.json`'s
  `test` script is just `tsc --noEmit && check-no-raw-font-size.mjs`.
- The existing `tests/e2e/` directory already houses Playwright scripts
  for similar channel / agent scenarios (`web_interactive_e2e_audit.spec.ts`,
  `run_web_interactive_e2e_audit.sh`).
