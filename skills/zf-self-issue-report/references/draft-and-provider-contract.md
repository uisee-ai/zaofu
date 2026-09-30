# Compatibility contract

This historical reference must not define a second `/issue` workflow.

Use the provider-neutral contract bundled with the generic skill:

- `skills/issue/SKILL.md`
- `issue_core.session`
- `issue_core.agent`
- `issue_core.tools`
- `issue_core.provider`
- `issue_core.publication`

Compatibility rules:

1. Public `/issue` creates one persistent Issue Session from the complete
   free-form report and runs one continuous Agent. A Draft/Working Set may be
   retained internally for resume/CAS, but is not a required user step.
2. The Agent independently investigates with authorized read-only tools and
   reconciles Raw Report, Working Set, and Evidence; it must not trust a
   structure-only interpretation.
3. User questions resume the same Session. Final confirmation freezes an
   immutable Publication Snapshot bound to revision and evidence digests.
4. The installing host supplies persistence, permissions, project inspection,
   provider execution, and Forge adapters; the portable Core never imports
   ZaoFu.
5. Publication remains a separate host-side side effect. Stale revisions,
   cancelled runs, and private attachments fail closed.

The old eight-answer Intake, ZaoFu-specific version field, implicit runtime
assessment, and separate confirm-preview UI are legacy-only implementation
details. They are not part of this compatibility alias and must not be
reintroduced by reading this reference.
