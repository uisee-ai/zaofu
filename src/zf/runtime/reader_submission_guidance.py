"""Submission ABI guidance shared by fresh and retried reader briefings."""


def submission_authority(run_id: str) -> str:
    return (
        f"This dispatch authorizes one submission for `{run_id}`. "
        "Previous attempt failures do not cancel this authorization. "
        "Command exit 0 is success; there is no asynchronous ack. "
        "Do not repeat a successful submission or reuse stale identities. "
        "A failed preflight has not submitted a result or consumed its capability: "
        "correct the same scratch from structured diagnostics and rerun validation "
        "in this turn. Do not stop merely because preflight exited nonzero. "
        "If actual submission fails, preserve the exact command/error and stop "
        "this attempt; never bypass expired identity or denied authority."
    )


def verifier_failure_schema_guidance() -> str:
    return (
        "- Runner unavailable before evaluation: execution_status=failed, verdict=abstained, "
        "failure_class=verifier_execution_failure, concrete summary and actual failure evidence_refs. "
        "Incomplete product reads do not prevent reporting this execution failure. "
        "Clear unperformed requirement_results/probe_receipts/reused_command_receipt_ids; "
        "omit product_acceptance_report, product_acceptance_report_ref and "
        "product_acceptance_report_digest if evaluation never ran. Preserve pinned identity, "
        "not unperformed template assertions. Use this operation's validate/submit command "
        "and confirm submission success; a final chat message alone is not a result. "
        "After recovery, product judgment still requires all mandatory reads and evidence."
    )
