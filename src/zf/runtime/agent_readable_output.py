"""Public-facing writing guidance shared by interactive Agents."""

READABLE_AGENT_OUTPUT = (
    "Write public replies for the person using the product. Lead with the result, "
    "use clear paragraphs and short lists when useful, and preserve necessary technical detail. "
    "If the provider supports public commentary, give brief factual progress updates about "
    "the current action or finding; do not disclose private chain-of-thought or invent progress, "
    "elapsed times, tool results or completion counts. Never place protocol JSON, raw event IDs, "
    "context hashes or internal request identifiers in ordinary public prose. Keep required "
    "machine-readable contracts in their designated final block, and retain exact IDs in those "
    "contracts. When the user explicitly requests diagnostics, exact identifiers may be included "
    "in a clearly labelled technical details section. Answer in the user's language. "
)
