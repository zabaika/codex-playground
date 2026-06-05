from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GetActiveCandidate:
    pass


@dataclass(frozen=True, slots=True)
class ListCandidates:
    pass


@dataclass(frozen=True, slots=True)
class GetCandidateProfile:
    candidate_id: str


@dataclass(frozen=True, slots=True)
class GetCandidateSources:
    candidate_id: str


@dataclass(frozen=True, slots=True)
class GetLatestCandidateDraft:
    candidate_id: str


@dataclass(frozen=True, slots=True)
class GetCandidateDraftReview:
    candidate_id: str
    draft_id: str | None = None


@dataclass(frozen=True, slots=True)
class GetCandidateExternalProfiles:
    candidate_id: str


@dataclass(frozen=True, slots=True)
class SearchResumeKbEvidence:
    candidate_id: str
    target_role: str | None = None
    query: str | None = None
    limit: int = 5
