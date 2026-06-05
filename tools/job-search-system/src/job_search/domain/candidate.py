from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candidate:
    candidate_id: str
    display_name: str
    status: str
    created_at: str
    updated_at: str
