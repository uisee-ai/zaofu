# Report a problem with the generic Self-Issue skill

Enter `/issue <free-form description>` in the host's composer, or use an empty
`/issue` and edit the generated Draft. The skill immediately creates an editable
Draft with title, bug description, reproduction steps, expected behavior,
attachment descriptions, operating system/version, project version, and an
empty `additional_context`. It does not open the fixed eight-question form;
that form is available only through an explicit legacy command/API.

The host may also create a local `system_detected` candidate for a small,
explicit allowlist of strong internal failures or an evidence-backed
`worker.self_issue.detected` event. It never creates a Draft, uploads, starts
OAuth, or publishes automatically. Web polls for such candidates while Kanban
Agent chat remains usable.

Draft edits persist across refresh. Choose exactly one next action after Draft
creation: **Continue with project assessment** or **Continue with limited report**.
A live runtime, heartbeat, worker, or existing login is only an available
execution carrier and never implies authorization. Only the first choice may
invoke the generic read-only project-assessment agent in an isolated,
revision-pinned source workspace; the second choice skips that agent and records
the limitation. Web and CLI do not start assessment implicitly. Interrupt then
offers both **Resume from checkpoint**, which keeps the same evidence snapshot
and remaining reproduction budget, and **Restart with fresh evidence**, which
creates a new run and recollects the current mechanical scene. The activity panel
updates without a page refresh. An interrupted or failed terminal run may still continue to
preview and publication. The exact Markdown states either `Not collected because
the user interrupted evidence collection.` or `No incident evidence was
collected.`; partial raw output remains local.

Use the Draft tab to edit the report and Save draft. User input is arranged in
the left column; reporter and orchestrator assessment fields and activity are in
the right column. Choose GitLab.com, GitHub, or both under Publish destination;
both repository targets are centrally locked. Selecting Preview displays the
exact Markdown for each selected provider. Attachments require a separate
manifest preview and confirmation when GitLab is selected. GitHub has no normal
REST Issue attachment API, so ZaoFu uses the supported GitHub CLI 2.99+ comment
attachment path. Confirmed images and videos up to 20 MB are added to one marked
Evidence comment; redacted, bounded text formats are included as comment text.
The final provider batch is frozen by the single **Publish** action. If
authorization is required, the button becomes **Connect** and shows the code;
authorization success resumes that exact batch as **Publishing** without a
second confirmation. Successful publication exposes a **View** link per
provider in new tabs. If the Issue exists but label/attachment post-steps fail,
the UI keeps the View link, shows the failure reason and **Retry label
reconciliation**, and never creates a duplicate Issue.
While the current canonical publication state is `published`, all Draft fields
and Save draft, Safe preview, and Restart are read-only. A notice explains the
immutability, while Published & View remains available. The exact published
preview is restored from its PublicationIntent after refresh. A historical
`published_issue_ref` alone does not lock a later non-published Draft cycle.
Draft and Preview remain freely switchable after publication; Preview reuses the
actual immutable published payload and does not prepare it again.

The completed read-only collection may add a bounded redacted log/timing/event
summary and existing trusted Playwright screenshots to that same attachment
manifest. Nothing is disclosed until the user confirms the manifest. Uploaded
items use clickable GitLab URLs; access follows the target project's visibility.
The attachment manifest shows the absolute path of the host's controlled local
copy and links to a Draft-and-digest-verified local preview in a new tab. Browsers
do not disclose the originally selected absolute path, and this local path never
enters the Draft or provider payload. Final Markdown uses the provider's absolute
HTTPS URL rather than a GitLab-relative `/uploads/...` link.
Empty optional user fields remain in the exact Preview as
`(User did not provide this information.)`; impact scope and assessment
confidence are included. Kernel-local sidecar paths are never published.

For an allowlisted `kanban_board` request, the Kernel may run one passive
Playwright capture against an explicit loopback HTTP URL only after the live
Orchestrator classifies a Web/UI incident or safe Web/Kanban component. It uses a clean
context without user cookies, blocks non-local requests, masks input/chat/
identity areas, performs no click or input, and discards the image when visible
text looks secret-shaped. It is labeled `playwright_clean_reproduction`, not a
capture of the user's existing tab. Failure is non-fatal; success still enters
the attachment confirmation flow.

Automatic candidates deduplicate by diagnostic fingerprint for 24 hours, use a
six-hour notification cooldown with severity-escalation bypass, and are bounded
to ten active candidates. Dismissing a candidate suppresses its fingerprint for
24 hours. Ordinary project test failures and transient recovered errors do not
auto-trigger. Configuration:

```yaml
self_issue:
  enabled: true
  automatic_detection_enabled: true
  browser_capture_enabled: true
  browser_capture_base_url: http://127.0.0.1:8002  # optional Kernel-signal origin
```

The intake UI is hosted in the host workspace, so the surrounding chat remains usable.
If the execution carrier is stopped, Web warns that Draft persistence and committed
source inspection still work while fresh events, logs, Traces, failure
screenshots, and live reproduction evidence may be unavailable, and suggests
`cd /path_to_project && <host-start-command>`.
Carrier, evidence, and assessment are shown as separate states. **Check runtime
again** rechecks availability. **Continue with limited report** skips project
assessment even when the carrier is already live, fixes confidence to low, and
explicitly discloses the missing dynamic evidence in the final body.

CLI uses the same state path:

```bash
uv run zf issue report "Short title"
uv run zf issue report "Short title" --non-interactive
# Explicit legacy questionnaire only:
uv run zf issue report "Short title" --legacy-intake --non-interactive
uv run zf issue answer <intake_id> --answers-file answers.json
uv run zf issue preview <draft_id> --provider gitlab  # github or both
uv run zf issue confirm <batch_id> --payload-digest <digest>
uv run zf issue publish <batch_id> --confirmation-id <confirmation_id>
```

GitLab uses Authorization Code + PKCE with the broad `api` scope. GitHub media
publication uses a ZaoFu GitHub OAuth App Device Flow; no client secret is
distributed. Enable Device Flow on the OAuth App, export its public client id as
`ZF_GITHUB_OAUTH_CLIENT_ID`, and configure a public target with:

```yaml
github:
  authorization_domain: github.com
  project: owner/repository
  oauth_client_id: ${ZF_GITHUB_OAUTH_CLIENT_ID}
  auth_mode: oauth_device_flow
  oauth_scope: public_repo
```

Private repositories require the explicit `repo` scope. Device transactions are
owner-only Kernel state, and both providers' tokens remain isolated in the
Secret Provider by user, workspace, provider, and authorization domain. A
compatible credential is shared by projects in the same workspace. Legacy
GitHub App `device_flow`/`ghu_` credentials cannot upload media and trigger one
new Connect after the target switches to OAuth. Credential and read-only
repository preflight checks complete before the Issue is created.

Public reporters cannot directly apply repository labels. The official target
repository should enable `.github/workflows/self-issue-labels.yml` and precreate
these exact labels: `runtime`, `kernel/state`, `provider/integration`, `web/ui`,
`configuration`, `security`, `performance`, `test/regression`, `unknown`, and
`p0`, `p1`, `p2`, `p3`. The workflow reads only the stable ZaoFu marker and the
allowlisted classification/severity values from a newly opened Issue.

Raw logs, traces, configuration, identities, uncommitted source, credentials,
and uncertain fields remain local. Text/JSON attachments are redacted;
PNG/JPEG metadata is stripped; videos require explicit public-disclosure
confirmation. The assessment role cannot edit code, access the network, read
Forge tokens, or publish an Issue. The Kernel-generated runner permits three
targeted tests per evidence run and rejects a fourth before execution. A
draft/run-scoped, owner-only Kernel ledger remains authoritative across Resume;
Restart creates a fresh ledger. Activity
distinguishes each numbered start and result while showing only a validated safe
target. Three inconclusive attempts, invalid JSON, or schema drift produce a
conservative `unknown` / `unverified` / `low` assessment; raw provider output and
unsafe field values are never retained.
Empty log heartbeat lines such as `[]`, `{}`, and `null` are omitted from the
public evidence Markdown.
Log evidence preserves a separately redacted final 4096-byte context and scans
bounded files for anomaly candidates outside that tail. The Kernel issues stable
candidate IDs, paths, line numbers, and digests without deciding relevance by
keyword overlap. The existing orchestrator compares candidates semantically with
the report, events, timing, and source. Only verified medium/high-confidence
candidate selections enter public evidence; otherwise the Markdown states that no
semantically related error location was identified and keeps the available tail.
