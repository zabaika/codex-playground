from __future__ import annotations

from enum import Enum


class CandidateStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class SourceKind(str, Enum):
    RESUME = "resume"
    LINKEDIN = "linkedin"
    PROFILE = "profile"
    OTHER = "other"


class SourceOrigin(str, Enum):
    FILE = "file"
    TEXT = "text"
    URL = "url"
    EXISTING_ARTIFACT = "existing_artifact"


class ArtifactType(str, Enum):
    RESUME_SOURCE = "resume_source"
    LINKEDIN_SOURCE = "linkedin_source"
    PROFILE_SOURCE = "profile_source"
    CANDIDATE_PROFILE_DRAFT = "candidate_profile_draft"
    RESUME_MARKDOWN = "resume_markdown"
    RESUME_MARKDOWN_FINAL = "resume_markdown_final"
    RESUME_VACANCY = "resume_vacancy"
    RESUME_VACANCY_FINAL = "resume_vacancy_final"
    RESUME_ROAST_REPORT = "resume_roast_report"


class FieldStatus(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    CONFLICTING = "conflicting"
    MISSING = "missing"


class VacancyWorkflowStage(str, Enum):
    NEW = "new"
    SHORTLISTED = "shortlisted"
    REJECTED = "rejected"
    CLOSED = "closed"


class ApplicationState(str, Enum):
    DRAFTED = "drafted"
    SUBMITTED = "submitted"
    INTERVIEWING = "interviewing"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    CLOSED = "closed"


class TouchpointDirection(str, Enum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"


class TouchpointState(str, Enum):
    PLANNED = "planned"
    SENT = "sent"
    RECEIVED = "received"
    REPLIED = "replied"
    CLOSED = "closed"


class ReminderStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class InterviewRoundState(str, Enum):
    PLANNED = "planned"
    SCHEDULED = "scheduled"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
