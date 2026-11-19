"""Submission ABI guidance shared by fresh and retried reader briefings."""

import json
import shlex


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


def verifier_failure_schema_guidance(
    *, owner: str = "task_verify", tier: str = "runtime", submit_command: str = "",
) -> str:
    body = json.dumps({
        "schema_version": "verification-result.v1",
        "execution_status": "failed", "verdict": "abstained",
        "failure_class": "verifier_execution_failure",
        "summary": "<exact observed runner failure>",
        "evidence_refs": ["<durable actual failed probe output ref>"],
        "verification_owner": owner, "verification_tier": tier,
        "requirement_results": [], "probe_receipts": [],
        "reused_command_receipt_ids": [], "rework_items": [],
    }, ensure_ascii=False, indent=2)
    if submit_command:
        args = ["--stdin" if arg == "--scratch" else arg
                for arg in shlex.split(submit_command)]
        example = (
            "Execution-failure command: fill summary/evidence_refs in this stdin body, "
            "then execute it directly. No scratch editing is required for this path. "
            "The quoted heredoc supplies CLI input only; it writes no filesystem path. "
            "It uses the same authorization, preflight and one-shot admission. "
            "Do not run the Success command after an admitted failure.\n```bash\n"
            + shlex.join(args) + " <<'ZF_EXECUTION_FAILURE'\n"
            + body + "\nZF_EXECUTION_FAILURE\n```"
        )
    else:
        example = "```json\n" + body + "\n```"
    return (
        "- Runner unavailable before evaluation: execution_status=failed, verdict=abstained, "
        "failure_class=verifier_execution_failure, concrete summary and actual failure evidence_refs. "
        "Incomplete product reads do not prevent reporting this execution failure. "
        "On a recovery attempt, first consume every page of critical-reader-repair-context "
        "and independently assess its bound execution evidence. If that input cannot be read, "
        "report reader_execution_failure with the actual read failure evidence. "
        "Clear unperformed requirement_results/probe_receipts/reused_command_receipt_ids; "
        "omit product_acceptance_report, product_acceptance_report_ref and "
        "product_acceptance_report_digest if evaluation never ran. Preserve pinned identity, "
        "not unperformed template assertions. Use this operation's validate/submit command "
        "and confirm submission success; a final chat message alone is not a result. "
        "After recovery, product judgment still requires all mandatory reads and evidence.\n"
        "- For this execution-failure case, use the compact body below, filling summary and evidence_refs "
        "from the actual failed probe. Do not retain the success template's assertions. "
        "Use structural JSON editing: omit means delete the key, not set null or rename "
        "the report into findings. Never append duplicate keys. Keep the scratch path; "
        "do not add identity fields. A socket path or placeholder is not failure evidence.\n"
        + example
    )
