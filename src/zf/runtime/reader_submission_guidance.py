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
        "evidence_refs": ["<actual tool-call/output ID or persisted probe ref; command, exit code and diagnostic>"],
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
        "- Required reads: complete=false with next_cursor is remaining work, not an execution failure. "
        "Continue the next bounded batch with that cursor. A corrected pagination option error is no longer a blocker; "
        "use the accepted option/default and finish the reads. A batch boundary or unfinished evaluation alone "
        "does not justify a terminal failed/abstained submission. Report an execution failure only for an "
        "actual unresolved obstruction, with its current evidence.\n"
        "- Runner unavailable before evaluation: execution_status=failed, verdict=abstained, "
        "failure_class=verifier_execution_failure, concrete summary and actual failure evidence_refs. "
        "Incomplete product reads do not prevent reporting this execution failure. "
        "Reference the actual tool-call ID or returned output/chunk ID with the command, "
        "exit code and observed diagnostic; use a persisted probe ref when one is provided. "
        "A concrete reference to the current attempt tool output does not require creating "
        "another evidence file or discovering a ledger event ID. Never invent an ID or "
        "cite another attempt as this attempt. If no identifier is available, report that "
        "limitation instead of fabricating a reference. "
        "If the current manifest pins critical-reader-repair-context, first consume every page "
        "and independently assess its bound execution evidence. A Task retry can instead carry "
        "host facts in task-handoff: use its host_execution_evidence_reads and page to completion. "
        "The absence of an unpinned repair source is not a read failure. If a pinned input cannot be read, "
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
