from __future__ import annotations

from pathlib import Path
import json
import subprocess
from typing import Any, Callable


SearchRunner = Callable[[list[str], int], subprocess.CompletedProcess[str]]


class KbEvidenceRetrievalService:
    _SURFACE_TERMS = ("job-search", "hiring")
    _NOTE_TYPES = ("job", "idea", "concept")

    def __init__(
        self,
        *,
        config_path: Path | None,
        search_bin: Path | None = None,
        runner: SearchRunner | None = None,
        timeout_seconds: int = 10,
    ) -> None:
        self._config_path = config_path
        self._search_bin = search_bin or self._default_search_bin()
        self._runner = runner or self._run_subprocess
        self._timeout_seconds = timeout_seconds

    def search(
        self,
        *,
        candidate_profile: dict[str, Any],
        evidence: dict[str, list[dict[str, Any]]],
        target_role: str | None,
        query: str | None,
        limit: int,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 5), 10))
        effective_query = self._build_query(
            candidate_profile=candidate_profile,
            evidence=evidence,
            target_role=target_role,
            query=query,
        )
        base = {
            "status": "ok",
            "query": query,
            "effective_query": effective_query,
            "target_role": target_role,
            "limit": safe_limit,
            "evidence_surface": list(self._SURFACE_TERMS),
            "note_types": list(self._NOTE_TYPES),
            "results": [],
        }
        if self._config_path is None:
            return {**base, "status": "unavailable", "reason": "kb_index_config_path is not configured"}
        if not self._config_path.exists():
            return {**base, "status": "unavailable", "reason": "kb_index_config_path does not exist"}
        if not self._search_bin.exists():
            return {**base, "status": "unavailable", "reason": "search_kb binary was not found"}

        merged: dict[str, dict[str, Any]] = {}
        for note_type in self._NOTE_TYPES:
            payload = self._search_note_type(effective_query=effective_query, note_type=note_type, limit=safe_limit)
            for item in payload:
                path = str(item.get("path") or "")
                if not path:
                    continue
                existing = merged.get(path)
                score = float(item.get("score") or 0)
                if existing is None or score > float(existing.get("score") or 0):
                    merged[path] = {**item, "note_type_filter": note_type}
        results = sorted(merged.values(), key=lambda item: float(item.get("score") or 0), reverse=True)[:safe_limit]
        return {
            **base,
            "results": results,
            "candidate_review_suggestions": self._candidate_review_suggestions(results),
        }

    def _search_note_type(self, *, effective_query: str, note_type: str, limit: int) -> list[dict[str, Any]]:
        args = [
            str(self._search_bin),
            "--config-path",
            str(self._config_path),
            "--note-type",
            note_type,
            "--limit",
            str(limit),
            "--json",
            effective_query,
        ]
        try:
            completed = self._runner(args, self._timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"kb-index search timed out after {self._timeout_seconds}s") from exc
        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "unknown kb-index error"
            raise RuntimeError(f"kb-index search failed: {message}")
        payload = json.loads(completed.stdout or "[]")
        if not isinstance(payload, list):
            raise RuntimeError("kb-index search returned a non-list JSON payload")
        return [item for item in payload if isinstance(item, dict)]

    def _build_query(
        self,
        *,
        candidate_profile: dict[str, Any],
        evidence: dict[str, list[dict[str, Any]]],
        target_role: str | None,
        query: str | None,
    ) -> str:
        terms: list[str] = []
        if query:
            terms.append(query)
        if target_role:
            terms.append(target_role)
        for role in candidate_profile.get("target_roles") or []:
            if isinstance(role, str):
                terms.append(role)
        for skill in evidence.get("skill_signals", [])[:8]:
            skill_name = str(skill.get("skill_name") or "").strip()
            if skill_name:
                terms.append(skill_name)
        terms.extend(self._SURFACE_TERMS)
        return " ".join(dict.fromkeys(term.strip() for term in terms if term and term.strip()))

    def _candidate_review_suggestions(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for result in results:
            text = " ".join(
                [
                    str(result.get("title") or ""),
                    str(result.get("lead_summary") or ""),
                    str(result.get("snippet") or ""),
                    " ".join(str(item) for item in result.get("tags") or []),
                    " ".join(str(item) for item in result.get("headings") or []),
                ]
            ).casefold()
            for signal, target_area in self._candidate_signal_targets().items():
                if signal not in text:
                    continue
                key = (signal, str(result.get("path") or ""))
                if key in seen:
                    continue
                seen.add(key)
                suggestions.append(
                    {
                        "suggested_action": "ask_user_to_confirm_candidate_evidence",
                        "candidate_target_area": target_area,
                        "signal": signal,
                        "source_kb_path": result.get("path"),
                        "source_kb_title": result.get("title"),
                        "reason": (
                            "KB context suggests this may be relevant for positioning, "
                            "but it is not a confirmed candidate fact."
                        ),
                        "required_next_step": (
                            "Ask the user whether the candidate has evidence for this signal. "
                            "If yes, import or confirm it through candidate-intake before using it in resumes."
                        ),
                    }
                )
        return suggestions[:10]

    def _candidate_signal_targets(self) -> dict[str, str]:
        return {
            "p&l": "experience_entries / achievement_evidence",
            "profit and loss": "experience_entries / achievement_evidence",
            "budget": "experience_entries / achievement_evidence",
            "revenue": "achievement_evidence",
            "board": "experience_entries / recommendations",
            "hiring": "experience_entries / skill_signals",
            "org design": "experience_entries / skill_signals",
            "scaling": "experience_entries / achievement_evidence",
            "platform": "skill_signals / achievement_evidence",
            "finops": "skill_signals / achievement_evidence",
            "cloud": "skill_signals / achievement_evidence",
            "security": "skill_signals / certifications",
        }

    def _run_subprocess(self, args: list[str], timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout_seconds, check=False)

    def _default_search_bin(self) -> Path:
        playground_root = Path(__file__).resolve().parents[6]
        return playground_root / "tools" / "kb-index" / "bin" / "search_kb"
