from __future__ import annotations

from collections import defaultdict
from typing import Any


class CandidateConflictResolutionService:
    def group_conflicts(self, field_conflicts: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = defaultdict(list)
        for field in field_conflicts:
            category = field.split("_", 1)[0]
            grouped[category].append(field)
        return dict(grouped)
