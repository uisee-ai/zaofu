---
name: zf-project-suggest
description: "ZaoFu project-grounded suggestion workflow for Claude or Codex. Use when the user asks to turn external context, docs/refer material, GitHub projects, articles, PRs, research notes, competitor signals, bug reports, or raw ideas into prioritized suggestions for ZaoFu. Produces evidence-backed recommendations and possible backlog candidates; does not implement code by default."
---

# ZaoFu Project Suggest

## Objective

Turn external or newly supplied context into a small, prioritized list of
suggestions grounded in ZaoFu's actual architecture, rules, docs, skills, and
in-flight work.

Default repository-facing output is Chinese unless the user asks otherwise.

## Boundary vs zf-harness-self-improve

Keep the split clean so the two skills do not drift into overlap:

- `zf-project-suggest` starts from **external signals** (docs/refer material,
  GitHub projects, articles, PRs, competitor moves, raw ideas) and turns them
  into grounded suggestions.
- `zf-harness-self-improve` starts from **internal evidence** (session history,
  runtime events, manual operations, repeated agent workflows) and turns them
  into harness assets.

If the driving input is an internal, evidence-first review of recent ZaoFu work,
use `zf-harness-self-improve` instead of this skill.

## Grounding First

Before drafting suggestions, read enough project context to avoid generic
advice:

- `AGENTS.md` for hard rules, backlog/doc discipline, commit policy, and
  ZaoFu invariants.
- `zf.yaml` when the suggestion touches workflow, roles, skills, runtime,
  triggers, or control-plane behavior.
- `docs/design/00-index.md` and relevant recent design docs when the input
  overlaps existing architecture.
- `docs/manual/`, `docs/impl/`, `ideas/`, `backlogs/`, and `tasks/` when the
  suggestion may already be planned or shipped.
- `skills/**/SKILL.md`, `.codex/skills/**/SKILL.md`, and
  `.claude/skills/**/SKILL.md` when the suggestion changes agent-facing
  workflows or documented user-facing behavior.
- Relevant source modules if the suggestion names a concrete subsystem.

Run the freshness check on external material through `zf-research-preflight-law`
(its preflight + LAW sections). Mark stale or degraded sources accordingly, and
do not promote an out-of-date external signal into a strong fact.

Do not re-propose existing accepted designs. Reference the existing doc and
propose only the delta.

## Workflow

1. Extract concrete signals from the external input: dates, behaviors, repo
   patterns, bug claims, product decisions, quotes, metrics, or code structures.
2. Map each signal to a ZaoFu subsystem, invariant, skill, manual, design doc,
   CLI, Web surface, Supervisor check, Autoresearch scenario, or backlog area.
3. Drop vague signals that cannot be tied to ZaoFu.
4. Rank by urgency and leverage, not by recency.
5. Produce at most 6 main suggestions plus a short "watch" tail if useful.
6. Stop after suggestions unless the user explicitly asks for backlog or
   implementation.

## Output Format

Use this shape for each main item:

```markdown
## N. <short title> (urgency: P0 / P1 / P2)

**Signal**: <source-backed fact, with path/link/date/quote summary where possible>

**Project impact**: <why this matters for ZaoFu, naming files/docs/contracts>

**Action**: <smallest concrete change or first step; mention tests/docs/skills>
```

Priority guide:

- `P0`: time-bound breakage, security, data loss, production blocker, or
  control-plane integrity risk.
- `P1`: clear user pain, recurring harness friction, multi-agent reliability
  gap, or high-leverage product-delivery improvement.
- `P2`: strategic improvement, polish, optional capability, or watch item.

## Backlog Conversion

If the user asks to generate backlogs from suggestions:

- Use `backlogs/YYYY-MM-DD-HHMM-<slug>.md`.
- First paragraph must contain `> 状态: proposed`.
- Include evidence references from the suggestion.
- Use concrete acceptance criteria in `step -> verify: check` form.
- Do not commit unapproved backlog candidates.

## Anti-Patterns

- Vague suggestions with no source signal or ZaoFu file/doc anchor.
- Recommending a second control plane or direct runtime truth writes.
- Proposing a new skill when an existing skill only needs better description,
  scope, or trigger language.
- Treating external recency as urgency.
- Starting implementation before the user chooses a suggestion.

How to test: ask "使用 zf-project-suggest 分析 docs/refer 里的材料, 给出 ZaoFu 可借鉴建议。"
