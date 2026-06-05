from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CandidateProfileView:
    candidate_id: str
    display_name: str
    core_profile: dict[str, Any]
    external_profiles: list[dict[str, Any]] = field(default_factory=list)
    work_authorizations: list[dict[str, Any]] = field(default_factory=list)
    languages: list[dict[str, Any]] = field(default_factory=list)
    targets: dict[str, Any] = field(default_factory=dict)
    compensation: dict[str, Any] = field(default_factory=dict)
    platform_preferences: dict[str, Any] = field(default_factory=dict)
    search_preferences: dict[str, Any] = field(default_factory=dict)
