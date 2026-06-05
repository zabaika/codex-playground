from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import sqlite3
import uuid
from urllib.parse import urlsplit, urlunsplit

from job_search.domain.candidate import Candidate
from job_search.domain.candidate_profile import CandidateProfileView
from job_search.domain.enums import CandidateStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, object] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


class CandidateRepository:
    _UPSERT_SQL = {
        "candidate_targets": {
            "select": "SELECT candidate_id FROM candidate_targets WHERE candidate_id = ?",
            "insert": (
                "INSERT INTO candidate_targets(candidate_id, target_roles_json, target_markets_json, updated_at) "
                "VALUES(?, ?, ?, ?)"
            ),
            "update": (
                "UPDATE candidate_targets SET target_roles_json = ?, target_markets_json = ?, updated_at = ? "
                "WHERE candidate_id = ?"
            ),
            "columns": ("target_roles_json", "target_markets_json"),
        },
        "candidate_platform_preferences": {
            "select": "SELECT candidate_id FROM candidate_platform_preferences WHERE candidate_id = ?",
            "insert": (
                "INSERT INTO candidate_platform_preferences("
                "candidate_id, linkedin_enabled, hh_enabled, other_platforms_json, public_profile_preference, updated_at"
                ") VALUES(?, ?, ?, ?, ?, ?)"
            ),
            "update": (
                "UPDATE candidate_platform_preferences SET linkedin_enabled = ?, hh_enabled = ?, "
                "other_platforms_json = ?, public_profile_preference = ?, updated_at = ? WHERE candidate_id = ?"
            ),
            "columns": ("linkedin_enabled", "hh_enabled", "other_platforms_json", "public_profile_preference"),
        },
    }

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create_candidate(self, display_name: str) -> Candidate:
        candidate_id = str(uuid.uuid4())
        timestamp = _now()
        self._conn.execute(
            """
            INSERT INTO candidates(candidate_id, display_name, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            """,
            (candidate_id, display_name, CandidateStatus.ACTIVE.value, timestamp, timestamp),
        )
        row = self._conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        return Candidate(**_row_to_dict(row))

    def get_candidate(self, candidate_id: str) -> dict[str, object] | None:
        row = self._conn.execute("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,)).fetchone()
        return _row_to_dict(row)

    def list_candidates(self) -> list[dict[str, object]]:
        rows = self._conn.execute("SELECT * FROM candidates ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(row) for row in rows]

    def upsert_core_profile(self, candidate_id: str, profile: dict[str, object]) -> None:
        current = self._conn.execute(
            "SELECT candidate_id FROM candidate_profiles WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        timestamp = _now()
        values = (
            candidate_id,
            profile.get("full_name"),
            profile.get("primary_email"),
            profile.get("primary_phone"),
            profile.get("current_location"),
            profile.get("current_title"),
            profile.get("summary_text"),
            timestamp,
        )
        if current is None:
            self._conn.execute(
                """
                INSERT INTO candidate_profiles(
                    candidate_id, full_name, primary_email, primary_phone,
                    current_location, current_title, summary_text, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
        else:
            self._conn.execute(
                """
                UPDATE candidate_profiles
                SET full_name = ?, primary_email = ?, primary_phone = ?, current_location = ?,
                    current_title = ?, summary_text = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (
                    profile.get("full_name"),
                    profile.get("primary_email"),
                    profile.get("primary_phone"),
                    profile.get("current_location"),
                    profile.get("current_title"),
                    profile.get("summary_text"),
                    timestamp,
                    candidate_id,
                ),
            )

    def replace_external_profiles(self, candidate_id: str, profiles: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_external_profiles WHERE candidate_id = ?", (candidate_id,))
        timestamp = _now()
        merged_profiles: dict[tuple[str, str], dict[str, object]] = {}
        for profile in profiles:
            platform = str(profile.get("platform") or "").strip().lower()
            profile_url = self._normalize_profile_url(str(profile.get("profile_url") or ""))
            if not platform or not profile_url:
                continue
            key = (platform, profile_url)
            existing = merged_profiles.get(key)
            if existing is None:
                merged_profiles[key] = {
                    "platform": platform,
                    "profile_url": profile_url,
                    "handle_or_slug": profile.get("handle_or_slug"),
                    "is_primary": bool(profile.get("is_primary")),
                    "visibility_status": profile.get("visibility_status"),
                    "last_checked_at": profile.get("last_checked_at"),
                    "last_source_artifact_id": profile.get("last_source_artifact_id"),
                }
                continue
            if not existing.get("handle_or_slug") and profile.get("handle_or_slug"):
                existing["handle_or_slug"] = profile.get("handle_or_slug")
            existing["is_primary"] = bool(existing.get("is_primary")) or bool(profile.get("is_primary"))
            if not existing.get("visibility_status") and profile.get("visibility_status"):
                existing["visibility_status"] = profile.get("visibility_status")
            if not existing.get("last_checked_at") and profile.get("last_checked_at"):
                existing["last_checked_at"] = profile.get("last_checked_at")
            if not existing.get("last_source_artifact_id") and profile.get("last_source_artifact_id"):
                existing["last_source_artifact_id"] = profile.get("last_source_artifact_id")

        for profile in merged_profiles.values():
            self._conn.execute(
                """
                INSERT INTO candidate_external_profiles(
                    external_profile_id, candidate_id, platform, profile_url, handle_or_slug,
                    is_primary, visibility_status, last_checked_at, last_source_artifact_id, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    profile.get("platform"),
                    profile.get("profile_url"),
                    profile.get("handle_or_slug"),
                    int(bool(profile.get("is_primary"))),
                    profile.get("visibility_status"),
                    profile.get("last_checked_at"),
                    profile.get("last_source_artifact_id"),
                    timestamp,
                ),
            )

    def _normalize_profile_url(self, profile_url: str) -> str:
        raw = profile_url.strip()
        if not raw:
            return ""
        parts = urlsplit(raw)
        scheme = parts.scheme.lower() or "https"
        netloc = parts.netloc.lower()
        path = parts.path.rstrip("/")
        if not path:
            path = "/"
        return urlunsplit((scheme, netloc, path, "", ""))

    def replace_work_authorizations(self, candidate_id: str, records: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_work_authorizations WHERE candidate_id = ?", (candidate_id,))
        timestamp = _now()
        for record in records:
            self._conn.execute(
                """
                INSERT INTO candidate_work_authorizations(
                    work_authorization_id, candidate_id, country_or_region, authorization_status,
                    authorization_basis, valid_until, is_primary, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    record.get("country_or_region"),
                    record.get("authorization_status"),
                    record.get("authorization_basis"),
                    record.get("valid_until"),
                    int(bool(record.get("is_primary"))),
                    timestamp,
                ),
            )

    def replace_languages(self, candidate_id: str, languages: list[dict[str, object]]) -> None:
        self._conn.execute("DELETE FROM candidate_language_proficiencies WHERE candidate_id = ?", (candidate_id,))
        timestamp = _now()
        merged_languages: dict[str, dict[str, object]] = {}
        for language in languages:
            raw_language_name = str(language.get("language_name") or "").strip()
            if not raw_language_name:
                continue
            key = raw_language_name.casefold()
            existing = merged_languages.get(key)
            if existing is None:
                merged_languages[key] = {
                    "language_name": raw_language_name,
                    "proficiency_level": language.get("proficiency_level"),
                    "is_primary": bool(language.get("is_primary")),
                }
                continue
            if not existing.get("proficiency_level") and language.get("proficiency_level"):
                existing["proficiency_level"] = language.get("proficiency_level")
            existing["is_primary"] = bool(existing.get("is_primary")) or bool(language.get("is_primary"))

        for language in merged_languages.values():
            self._conn.execute(
                """
                INSERT INTO candidate_language_proficiencies(
                    language_proficiency_id, candidate_id, language_name, proficiency_level, is_primary, created_at
                ) VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    candidate_id,
                    language.get("language_name"),
                    language.get("proficiency_level"),
                    int(bool(language.get("is_primary"))),
                    timestamp,
                ),
            )

    def upsert_targets(self, candidate_id: str, targets: dict[str, object]) -> None:
        self._upsert_json_record(
            table="candidate_targets",
            candidate_id=candidate_id,
            values={
                "target_roles_json": json.dumps(targets.get("target_roles", []), ensure_ascii=False),
                "target_markets_json": json.dumps(targets.get("target_markets", []), ensure_ascii=False),
            },
        )

    def upsert_compensation(self, candidate_id: str, compensation: dict[str, object]) -> None:
        row = self._conn.execute("SELECT candidate_id FROM candidate_compensation WHERE candidate_id = ?", (candidate_id,)).fetchone()
        timestamp = _now()
        if row is None:
            self._conn.execute(
                """
                INSERT INTO candidate_compensation(
                    candidate_id, salary_floor, salary_target, salary_aspiration, currency,
                    compensation_notes, compensation_by_currency_json, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    compensation.get("salary_floor"),
                    compensation.get("salary_target"),
                    compensation.get("salary_aspiration"),
                    compensation.get("currency"),
                    compensation.get("compensation_notes"),
                    json.dumps(compensation.get("compensation_by_currency", {}), ensure_ascii=False),
                    timestamp,
                ),
            )
        else:
            self._conn.execute(
                """
                UPDATE candidate_compensation
                SET salary_floor = ?, salary_target = ?, salary_aspiration = ?, currency = ?,
                    compensation_notes = ?, compensation_by_currency_json = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (
                    compensation.get("salary_floor"),
                    compensation.get("salary_target"),
                    compensation.get("salary_aspiration"),
                    compensation.get("currency"),
                    compensation.get("compensation_notes"),
                    json.dumps(compensation.get("compensation_by_currency", {}), ensure_ascii=False),
                    timestamp,
                    candidate_id,
                ),
            )

    def upsert_platform_preferences(self, candidate_id: str, preferences: dict[str, object]) -> None:
        self._upsert_json_record(
            table="candidate_platform_preferences",
            candidate_id=candidate_id,
            values={
                "linkedin_enabled": int(bool(preferences.get("linkedin_enabled", True))),
                "hh_enabled": int(bool(preferences.get("hh_enabled", True))),
                "other_platforms_json": json.dumps(preferences.get("other_platforms", []), ensure_ascii=False),
                "public_profile_preference": preferences.get("public_profile_preference"),
            },
        )

    def upsert_search_preferences(self, candidate_id: str, preferences: dict[str, object]) -> None:
        row = self._conn.execute("SELECT candidate_id FROM candidate_search_preferences WHERE candidate_id = ?", (candidate_id,)).fetchone()
        timestamp = _now()
        values = (
            preferences.get("base_location"),
            json.dumps(preferences.get("target_geographies", []), ensure_ascii=False),
            preferences.get("remote_preference"),
            preferences.get("relocation_preference"),
            preferences.get("travel_preference"),
            preferences.get("commute_preference"),
            json.dumps(preferences.get("employment_type_preferences", []), ensure_ascii=False),
            json.dumps(preferences.get("work_model_preferences", []), ensure_ascii=False),
            json.dumps(preferences.get("company_avoid_list", []), ensure_ascii=False),
            json.dumps(preferences.get("company_priorities", []), ensure_ascii=False),
            preferences.get("hybrid_policy"),
            int(bool(preferences.get("confidential_search", False))),
            timestamp,
        )
        if row is None:
            self._conn.execute(
                """
                INSERT INTO candidate_search_preferences(
                    candidate_id, base_location, target_geographies_json, remote_preference,
                    relocation_preference, travel_preference, commute_preference,
                    employment_type_preferences_json, work_model_preferences_json,
                    company_avoid_list_json, company_priorities_json, hybrid_policy,
                    confidential_search, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (candidate_id, *values),
            )
        else:
            self._conn.execute(
                """
                UPDATE candidate_search_preferences
                SET base_location = ?, target_geographies_json = ?, remote_preference = ?,
                    relocation_preference = ?, travel_preference = ?, commute_preference = ?,
                    employment_type_preferences_json = ?, work_model_preferences_json = ?,
                    company_avoid_list_json = ?, company_priorities_json = ?, hybrid_policy = ?,
                    confidential_search = ?, updated_at = ?
                WHERE candidate_id = ?
                """,
                (*values, candidate_id),
            )

    def get_candidate_profile_view(self, candidate_id: str) -> CandidateProfileView | None:
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            return None
        core = _row_to_dict(
            self._conn.execute("SELECT * FROM candidate_profiles WHERE candidate_id = ?", (candidate_id,)).fetchone()
        ) or {}
        targets = _row_to_dict(
            self._conn.execute("SELECT * FROM candidate_targets WHERE candidate_id = ?", (candidate_id,)).fetchone()
        ) or {}
        compensation = _row_to_dict(
            self._conn.execute("SELECT * FROM candidate_compensation WHERE candidate_id = ?", (candidate_id,)).fetchone()
        ) or {}
        platform_preferences = _row_to_dict(
            self._conn.execute("SELECT * FROM candidate_platform_preferences WHERE candidate_id = ?", (candidate_id,)).fetchone()
        ) or {}
        search_preferences = _row_to_dict(
            self._conn.execute("SELECT * FROM candidate_search_preferences WHERE candidate_id = ?", (candidate_id,)).fetchone()
        ) or {}
        ext_rows = self._conn.execute(
            "SELECT * FROM candidate_external_profiles WHERE candidate_id = ? ORDER BY is_primary DESC, created_at",
            (candidate_id,),
        ).fetchall()
        lang_rows = self._conn.execute(
            "SELECT * FROM candidate_language_proficiencies WHERE candidate_id = ? ORDER BY is_primary DESC, created_at",
            (candidate_id,),
        ).fetchall()
        auth_rows = self._conn.execute(
            "SELECT * FROM candidate_work_authorizations WHERE candidate_id = ? ORDER BY is_primary DESC, created_at",
            (candidate_id,),
        ).fetchall()
        return CandidateProfileView(
            candidate_id=candidate_id,
            display_name=str(candidate["display_name"]),
            core_profile=core,
            external_profiles=[_row_to_dict(row) for row in ext_rows],
            work_authorizations=[_row_to_dict(row) for row in auth_rows],
            languages=[_row_to_dict(row) for row in lang_rows],
            targets=self._decode_json_fields(targets),
            compensation=self._decode_json_fields(compensation),
            platform_preferences=self._decode_json_fields(platform_preferences),
            search_preferences=self._decode_json_fields(search_preferences),
        )

    def get_external_profiles(self, candidate_id: str) -> list[dict[str, object]]:
        rows = self._conn.execute(
            "SELECT * FROM candidate_external_profiles WHERE candidate_id = ? ORDER BY is_primary DESC, created_at",
            (candidate_id,),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]

    def _upsert_json_record(self, *, table: str, candidate_id: str, values: dict[str, object]) -> None:
        statements = self._UPSERT_SQL.get(table)
        if statements is None:
            raise ValueError(f"Unsupported candidate table: {table}")
        row = self._conn.execute(statements["select"], (candidate_id,)).fetchone()
        timestamp = _now()
        ordered_values = tuple(values[key] for key in statements["columns"])
        if row is None:
            self._conn.execute(
                statements["insert"],
                (candidate_id, *ordered_values, timestamp),
            )
        else:
            self._conn.execute(
                statements["update"],
                (*ordered_values, timestamp, candidate_id),
            )

    def _decode_json_fields(self, record: dict[str, object]) -> dict[str, object]:
        decoded = dict(record)
        for key, value in list(decoded.items()):
            if key.endswith("_json") and isinstance(value, str):
                decoded[key[:-5]] = json.loads(value)
                del decoded[key]
        return decoded
