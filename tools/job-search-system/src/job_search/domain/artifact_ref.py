from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    artifact_id: str
    artifact_type: str
    candidate_id: str | None
    storage_path: str
    content_hash: str
    created_at: str
