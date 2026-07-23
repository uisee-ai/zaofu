# Layered Runtime Authority and Orchestration Modes

The Kernel owns deterministic dispatch, state transitions, evidence checks,
replay/resume behavior, and external side effects.

Configured role agents may plan, implement, review, verify, and summarize.
Their decisions become runtime inputs only through sanctioned events,
artifacts, sidecar refs, or controlled actions.

Current route families:

- Product/light flow: Kernel dispatches work and gates completion.
- Legacy safe-team: a Layer 2 decision maker may be explicitly enabled.
- Run Manager and Autoresearch: recovery and diagnosis flows request bounded
  deterministic actions instead of mutating canonical state directly.
