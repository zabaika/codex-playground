#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


ENGINEERING_RULES = [
    (
        "company_or_system_context",
        1.0,
        [
            r"\bplatform\b",
            r"\binfrastructure\b",
            r"\barchitecture\b",
            r"\bsystem\b",
            r"\bservice\b",
            r"\bproduction\b",
            r"\bdeploy(?:ment)?\b",
            r"\bincident\b",
            r"\bobservability\b",
            r"\bllmops\b",
            r"\bdeveloper\b",
            r"\bengineering\b",
            r"\bdevops\b",
            r"\bci/?cd\b",
            r"\bbackend\b",
            r"\bfrontend\b",
            r"\bapi\b",
            r"\bsre\b",
            r"\btech lead\b",
            r"\bsoftware engineer\b",
            r"\bdata engineer\b",
            r"\bкод\b",
            r"\bразработк[аеи]\b",
            r"\bинженер(?:н\w*)?\b",
            r"\bплатформ\w*\b",
            r"\bсервис\w*\b",
            r"\bсистем\w*\b",
            r"\bинфраструктур\w*\b",
            r"\bархитектур\w*\b",
            r"\bдепло\w*\b",
            r"\bрелиз\w*\b",
        ],
    ),
    (
        "operating_details",
        1.0,
        [
            r"\bmetric\b",
            r"\btooling\b",
            r"\bworkflow\b",
            r"\bfeedback loop\b",
            r"\bbuild vs buy\b",
            r"\bownership\b",
            r"\bprioriti[sz]ation\b",
            r"\bteam\b",
            r"\bon-call\b",
            r"\bsla\b",
            r"\bslo\b",
            r"\broadmap\b",
            r"\bthroughput\b",
            r"\bslack\b",
            r"\bsandbox\b",
            r"\bgovernance\b",
            r"\bметрик\w*\b",
            r"\bпроцесс\w*\b",
            r"\bтул\w*\b",
            r"\bкоманд\w*\b",
            r"\bприоритизац\w*\b",
            r"\bзона ответственности\b",
            r"\bстендап\w*\b",
            r"\bобратн\w* связ\w*\b",
            r"\bowned systems\b",
            r"\bа\/б\b",
        ],
    ),
    (
        "engineering_lessons",
        1.0,
        [
            r"\bdeveloper productivity\b",
            r"\bcode review\b",
            r"\bprompt\b",
            r"\bai rollout\b",
            r"\bplatform engineering\b",
            r"\borchestration\b",
            r"\bchoreography\b",
            r"\btechnical debt\b",
            r"\bdeveloper experience\b",
            r"\bdevex\b",
            r"\bllm\b",
            r"\bprompt hardening\b",
            r"\bagent-first\b",
            r"\bcodex\b",
            r"\bworktree\b",
            r"\bтехническ\w*\b",
            r"\bплатформенн\w*\b",
            r"\bразработчик\w*\b",
            r"\bcode review\b",
            r"\bдевопс\b",
        ],
    ),
    (
        "agent_operating_model",
        1.0,
        [
            r"\brepository\b",
            r"\brepo\b",
            r"\bdocs\b",
            r"\bdocumentation\b",
            r"\bconstraints?\b",
            r"\blinter\w*\b",
            r"\bmechanical checks?\b",
            r"\bquality gates?\b",
            r"\bfeedback loops?\b",
            r"\binternal agents?\b",
            r"\bagent ecosystem\b",
            r"\bagentic automation\b",
            r"\bcoordination layer\b",
            r"\bагент\w*\b",
            r"\bрепозитор\w*\b",
            r"\bдокументац\w*\b",
            r"\bограничител\w*\b",
            r"\bлинтер\w*\b",
            r"\bпроверк\w*\b",
            r"\bоркестрац\w*\b",
            r"\bстендап\w*\b",
        ],
    ),
]

GENERAL_RULES = [
    (
        "career_or_management",
        1.0,
        [
            r"\bcareer\b",
            r"\bjob\b",
            r"\binterview\b",
            r"\bresume\b",
            r"\blinkedin\b",
            r"\breferral\b",
            r"\bvisa\b",
            r"\bsalary\b",
            r"\brecruit(?:er|ing)\b",
            r"\bnetworking\b",
            r"\bmanager\b",
            r"\bcommunication\b",
            r"\bрынок труда\b",
            r"\bкарьер\w*\b",
            r"\bпоиск работы\b",
            r"\bработодател\w*\b",
            r"\bсобеседован\w*\b",
            r"\bрезюм\w*\b",
            r"\bреферал\w*\b",
            r"\bвиза\b",
            r"\bрелокац\w*\b",
            r"\bиммиграц\w*\b",
            r"\bнайм\w*\b",
            r"\bагентност\w*\b",
            r"\bконсульт\w*\b",
            r"\bкоуч\w*\b",
            r"\bменеджмент\w*\b",
            r"\bпродуктивност\w*\b",
        ],
    ),
    (
        "broad_expert_advice",
        1.0,
        [
            r"\badvice\b",
            r"\brecommend(?:ation)?\b",
            r"\banti-pattern\b",
            r"\bmistake\b",
            r"\bframework\b",
            r"\bpractical\b",
            r"\bexample\b",
            r"\bcase\b",
            r"\blesson\b",
            r"\bсовет\w*\b",
            r"\bрекомендац\w*\b",
            r"\bошиб\w*\b",
            r"\bантипаттерн\w*\b",
            r"\bпример\w*\b",
            r"\bкейс\w*\b",
            r"\bурок\w*\b",
        ],
    ),
]

GENERAL_PRIORITY_PATTERNS = [
    r"\bcareer\b",
    r"\bjob search\b",
    r"\binterview\b",
    r"\bresume\b",
    r"\blinkedin\b",
    r"\breferral\b",
    r"\bvisa\b",
    r"\brecruit(?:er|ing)\b",
    r"\bbackground check\b",
    r"\bрынок труда\b",
    r"\bпоиск работы\b",
    r"\bкарьер\w*\b",
    r"\bсобеседован\w*\b",
    r"\bрезюм\w*\b",
    r"\bреферал\w*\b",
    r"\bвиза\b",
    r"\bрелокац\w*\b",
    r"\bиммиграц\w*\b",
    r"\bнайм\w*\b",
    r"\bрекрутер\w*\b",
    r"\bоффер\w*\b",
]


def load_source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def normalize_text(raw_text: str) -> str:
    lines = raw_text.splitlines()
    filtered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- Subtitle "):
            continue
        if stripped.startswith("- Prepared transcript file:"):
            continue
        if stripped.startswith("- Source:"):
            continue
        if stripped.startswith("- Video URL:"):
            continue
        if stripped.startswith("- Video ID:"):
            continue
        filtered.append(stripped)
    collapsed = " ".join(filtered).lower()
    return re.sub(r"\s+", " ", collapsed).strip()


def rule_matches(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def matched_rule_names(text: str, rules: list[tuple[str, float, list[str]]]) -> list[str]:
    names: list[str] = []
    for rule_name, _weight, patterns in rules:
        if rule_matches(text, patterns):
            names.append(rule_name)
    return names


def weighted_score(text: str, rules: list[tuple[str, float, list[str]]]) -> float:
    score = 0.0
    for _rule_name, weight, patterns in rules:
        if rule_matches(text, patterns):
            score += weight
    return score


def detect_route(text: str, title: str) -> tuple[str, str, dict[str, object]]:
    normalized_title = title.lower().strip()
    normalized_text = normalize_text(text)
    combined = f"{normalized_title} {normalized_text}".strip()

    engineering_matches = matched_rule_names(combined, ENGINEERING_RULES)
    general_matches = matched_rule_names(combined, GENERAL_RULES)
    engineering_score = weighted_score(combined, ENGINEERING_RULES)
    general_score = weighted_score(combined, GENERAL_RULES)
    general_priority_hits = sum(1 for pattern in GENERAL_PRIORITY_PATTERNS if re.search(pattern, combined, flags=re.IGNORECASE))
    has_company_or_system = "company_or_system_context" in engineering_matches
    has_operating_model_signal = any(
        rule_name in engineering_matches
        for rule_name in ("operating_details", "engineering_lessons", "agent_operating_model")
    )
    has_strong_engineering_signal = any(
        rule_name in engineering_matches
        for rule_name in ("engineering_lessons", "agent_operating_model")
    )

    if general_priority_hits >= 2 and engineering_score < general_score + 2:
        reason = (
            "source is mostly broad expert content, career or market advice, "
            "or other general analysis without a concrete engineering operating model"
        )
        route = "general"
    elif has_company_or_system and has_operating_model_signal and has_strong_engineering_signal:
        reason = (
            "source combines a concrete company or system context with operating-model details, "
            "which makes it an engineering workflow or platform case"
        )
        route = "engineering"
    elif len(engineering_matches) >= 2 and engineering_score >= general_score and has_strong_engineering_signal:
        reason = (
            "source contains multiple engineering signals such as concrete systems, "
            "team/process details, or reusable engineering practices"
        )
        route = "engineering"
    else:
        reason = (
            "source is mostly broad expert content, career or market advice, "
            "or other general analysis without a concrete engineering operating model"
        )
        route = "general"

    details = {
        "engineering_score": engineering_score,
        "general_score": general_score,
        "general_priority_hits": general_priority_hits,
        "engineering_matches": engineering_matches,
        "general_matches": general_matches,
    }
    return route, reason, details


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect article-to-obsidian-kb routing path for a source.")
    parser.add_argument("--source-file", required=True, help="Path to a prepared transcript or source text file")
    parser.add_argument("--title", default="", help="Optional title to include in route detection")
    parser.add_argument("--json", action="store_true", help="Print JSON summary")
    args = parser.parse_args()

    source_path = Path(args.source_file).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"Source file does not exist: {source_path}")

    route, reason, details = detect_route(load_source_text(source_path), args.title)

    payload = {
        "route_used": route,
        "route_reason": reason,
        "source_file": str(source_path),
        "details": details,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
        return 0

    print(f"Route used: {route}")
    print(f"Route reason: {reason}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
