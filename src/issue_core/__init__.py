"""Portable `/issue` core contracts.

This package intentionally has no dependency on the ZaoFu host.  A host may
adapt its Draft model and persistence layer while reusing these pure helpers.
"""

from .environment import detect_environment, detect_project_version
from .draft import DraftRevisionConflict, IssueDraft
from .flow import IssueFlow
from .contracts import (
    ASSESSMENT_DECISION_SCHEMA,
    AssessmentDecision,
    EnvironmentDetector,
    ForgeAdapter,
    FORGE_PUBLICATION_STATUSES,
    ForgeIssuePayload,
    ForgePublishResult,
    HostAdapter,
    ProjectAssessmentAgent,
    ProjectInspector,
    PublishedIssueRef,
)
from .assessment import (
    ASSESSMENT_RESULT_SCHEMA,
    ASSESSMENT_RESULT_STATUSES,
    AssessmentFinding,
    ProjectAssessmentResult,
)
from .normalizer import (
    DeterministicIssueUnderstandingAgent,
    IssueUnderstandingAgent,
    normalize_unstructured_report,
)
from .enrichment import (
    Claim,
    Conflict,
    DraftFieldState,
    DraftRevision,
    EvaluationRun,
    Evidence,
    Gap,
    IssueDraftV3,
    Resolution,
    Suggestion,
    SuggestionDismissal,
    dismiss_suggestion,
    stable_content_digest,
)
from .publication import (
    HandoffBundle,
    HandoffItem,
    PUBLICATION_STATES,
    PublicationSnapshot,
    PublicationState,
)
from .investigation import (
    InvestigationBudget,
    InvestigationPlan,
    InvestigationRun,
    evidence_for_context,
)
from .enrichment_policy import AUTO_FILL_FIELDS, can_auto_fill
from .retention import IssueRetentionPolicy
from .session import (
    AGENT_RESULT_SCHEMA,
    AttachmentRef,
    AgentResult,
    InMemoryIssueSessionStore,
    JsonIssueSessionStore,
    InvestigationTools,
    IssueAgent,
    IssueSession,
    IssueSessionRunner,
    IssueSessionStore,
    IssueWorkingSet,
    SESSION_SCHEMA,
    SessionStatus,
    WORKING_SET_SCHEMA,
)
from .worker import IssueSessionWorker, WorkerResult
from .agent import ContinuousIssueAgent, Investigator, default_read_only_investigator
from .tools import ReadOnlyInvestigationTools
from .provider import (
    IssueAgentProvider,
    JsonProviderAgent,
    ProviderRequest,
    ProviderResponse,
    SubprocessJsonProvider,
)
from .provider_agent import ToolCallingIssueAgent
from .report_snapshot import IssueReportSnapshot, REPORT_SNAPSHOT_SCHEMA, attachment_manifest_digest
from .validator import ValidationFailure, ValidationResult, validate_issue_report

__all__ = [
    "DeterministicIssueUnderstandingAgent",
    "IssueUnderstandingAgent",
    "ASSESSMENT_DECISION_SCHEMA",
    "AssessmentDecision",
    "EnvironmentDetector",
    "ForgeAdapter",
    "FORGE_PUBLICATION_STATUSES",
    "ForgeIssuePayload",
    "ForgePublishResult",
    "HostAdapter",
    "ProjectAssessmentAgent",
    "ProjectInspector",
    "PublishedIssueRef",
    "DraftRevisionConflict",
    "IssueDraft",
    "IssueFlow",
    "detect_environment",
    "detect_project_version",
    "normalize_unstructured_report",
    "PUBLICATION_STATES",
    "PublicationSnapshot",
    "PublicationState",
    "HandoffBundle",
    "HandoffItem",
    "Claim",
    "Conflict",
    "DraftFieldState",
    "DraftRevision",
    "EvaluationRun",
    "Evidence",
    "Gap",
    "IssueDraftV3",
    "Resolution",
    "Suggestion",
    "SuggestionDismissal",
    "dismiss_suggestion",
    "stable_content_digest",
    "ASSESSMENT_RESULT_SCHEMA",
    "ASSESSMENT_RESULT_STATUSES",
    "AssessmentFinding",
    "ProjectAssessmentResult",
    "InvestigationBudget",
    "InvestigationPlan",
    "InvestigationRun",
    "evidence_for_context",
    "AUTO_FILL_FIELDS",
    "can_auto_fill",
    "IssueRetentionPolicy",
    "AGENT_RESULT_SCHEMA",
    "AttachmentRef",
    "AgentResult",
    "InMemoryIssueSessionStore",
    "JsonIssueSessionStore",
    "InvestigationTools",
    "IssueAgent",
    "IssueSession",
    "IssueSessionRunner",
    "IssueSessionStore",
    "IssueWorkingSet",
    "SESSION_SCHEMA",
    "SessionStatus",
    "WORKING_SET_SCHEMA",
    "IssueSessionWorker",
    "WorkerResult",
    "ContinuousIssueAgent",
    "Investigator",
    "default_read_only_investigator",
    "ReadOnlyInvestigationTools",
    "IssueAgentProvider",
    "JsonProviderAgent",
    "ProviderRequest",
    "ProviderResponse",
    "SubprocessJsonProvider",
    "ToolCallingIssueAgent",
    "IssueReportSnapshot",
    "ValidationFailure",
    "ValidationResult",
    "validate_issue_report",
    "REPORT_SNAPSHOT_SCHEMA",
    "attachment_manifest_digest",
]
