from __future__ import annotations

import json
import re
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, RankingConfig, RetrievalConfig, load_runtime_config
from .index_db import connect, init_db

TERM_RE = re.compile(r"[\w-]+", re.UNICODE)
STOP_TERMS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "в",
    "во",
    "для",
    "и",
    "к",
    "на",
    "о",
    "об",
    "по",
    "с",
}


def normalize_for_match(text: str) -> str:
    return " ".join(term.lower() for term in TERM_RE.findall(text or ""))


def tokenize(text: str) -> list[str]:
    return [term.lower() for term in TERM_RE.findall(text or "")]


def normalize_query_terms(query: str) -> list[str]:
    terms = [term for term in tokenize(query) if term not in STOP_TERMS]
    if terms:
        return list(dict.fromkeys(terms))
    return list(dict.fromkeys(tokenize(query)))


def token_match(left: str, right: str) -> bool:
    if left == right:
        return True
    if min(len(left), len(right)) < 6:
        return False
    return left.startswith(right) or right.startswith(left)


def count_matching_terms(query_terms: list[str], text: str) -> int:
    candidate_terms = list(dict.fromkeys(tokenize(text)))
    return sum(1 for query_term in query_terms if any(token_match(query_term, candidate_term) for candidate_term in candidate_terms))


def build_match_query(query: str) -> str:
    terms = normalize_query_terms(query)
    if not terms:
        return '""'
    return " OR ".join(f'"{term}"' for term in terms)


def overlap_score(query_terms: list[str], text: str) -> float:
    if not query_terms or not text:
        return 0.0
    matched = count_matching_terms(query_terms, text)
    return matched / len(query_terms)


def has_token_subsequence(query_terms: list[str], title_terms: list[str]) -> bool:
    if not query_terms or len(query_terms) > len(title_terms):
        return False
    for start in range(len(title_terms) - len(query_terms) + 1):
        window = title_terms[start : start + len(query_terms)]
        if all(token_match(query_term, title_term) for query_term, title_term in zip(query_terms, window)):
            return True
    return False


def exactish_title_score(query: str, title: str, ranking: RankingConfig) -> float:
    normalized_query = normalize_for_match(query)
    normalized_title = normalize_for_match(title)
    if not normalized_query or not normalized_title:
        return 0.0
    if normalized_query == normalized_title:
        return ranking.exact_title_bonus['exact_match']
    query_terms = normalize_query_terms(query)
    title_terms = tokenize(title)
    if has_token_subsequence(query_terms, title_terms) or has_token_subsequence(title_terms, query_terms):
        return ranking.exact_title_bonus['substring_match']
    if query_terms and count_matching_terms(query_terms, title) == len(query_terms):
        return ranking.exact_title_bonus['all_terms_match']
    if len(query_terms) >= 2 and count_matching_terms(query_terms, title) >= max(2, len(query_terms) - 1):
        return ranking.exact_title_bonus['near_match']
    return 0.0


def best_phrase_match_score(query: str, query_terms: list[str], phrases: list[str], ranking: RankingConfig) -> float:
    best_score = 0.0
    for phrase in phrases:
        if not phrase:
            continue
        best_score = max(
            best_score,
            overlap_score(query_terms, phrase),
            exactish_title_score(query, phrase, ranking),
        )
    return best_score


def row_to_record(row, fts_rank: int | None, candidate_sources: set[str]) -> dict[str, object]:
    return {
        'path': row['path'],
        'title': row['title'],
        'note_type': row['note_type'],
        'lead_summary': row['lead_summary'],
        'headings_json': row['headings_json'],
        'tags_json': row['tags_json'],
        'links_out_json': row['links_out_json'],
        'fts_rank': fts_rank,
        'candidate_sources': candidate_sources,
    }


def merge_candidate_row(candidates: dict[str, dict[str, object]], row, source: str, fts_rank: int | None = None) -> None:
    path = row['path']
    record = candidates.get(path)
    if record is None:
        candidates[path] = row_to_record(row, fts_rank, {source})
        return
    record['candidate_sources'].add(source)
    if fts_rank is not None:
        previous_rank = record['fts_rank']
        if previous_rank is None or fts_rank < previous_rank:
            record['fts_rank'] = fts_rank


def fetch_links_out_rows(conn, query_terms: list[str], limit: int) -> list:
    if not query_terms:
        return []
    clauses = " OR ".join("LOWER(links_out_json) LIKE ?" for _ in query_terms)
    params = [f"%{term}%" for term in query_terms]
    params.append(limit)
    return conn.execute(
        f"""
        SELECT
          path,
          title,
          note_type,
          lead_summary,
          headings_json,
          tags_json,
          aliases_json,
          links_out_json
        FROM notes
        WHERE {clauses}
        LIMIT ?
        """,
        params,
    ).fetchall()


def fetch_title_rows(conn, query_terms: list[str], limit: int, note_type: str | None = None) -> list:
    if not query_terms:
        return []
    clauses = " OR ".join("LOWER(title) LIKE ?" for _ in query_terms)
    params = [f"%{term}%" for term in query_terms]
    note_type_sql = ""
    if note_type:
        note_type_sql = " AND note_type = ?"
        params.append(note_type)
    params.append(limit)
    return conn.execute(
        f"""
        SELECT
          path,
          title,
          note_type,
          lead_summary,
          headings_json,
          tags_json,
          aliases_json,
          links_out_json
        FROM notes
        WHERE ({clauses}){note_type_sql}
        LIMIT ?
        """,
        params,
    ).fetchall()


def should_keep_result(
    result: dict[str, object],
    position: int,
    top_score: float,
    retrieval: RetrievalConfig,
) -> bool:
    if position < retrieval.always_keep_top_n:
        return True
    if top_score <= 0:
        return False
    score_ratio = float(result['score']) / top_score
    return (
        float(result['term_coverage_ratio']) >= retrieval.min_term_coverage_ratio
        and score_ratio >= retrieval.min_score_ratio_to_top
    )


def should_keep_title_lookup_result(
    result: dict[str, object],
    query: str,
    query_terms: list[str],
    ranking: RankingConfig,
) -> bool:
    exact_score = exactish_title_score(query, str(result['title']), ranking)
    if exact_score > 0:
        return True
    title_overlap = overlap_score(query_terms, str(result['title']))
    return title_overlap > 0


def search_index(
    db_path: Path,
    query: str,
    ranking: RankingConfig | None = None,
    retrieval: RetrievalConfig | None = None,
    limit: int | None = None,
    mode: str = 'default',
    note_type: str | None = None,
) -> list[dict[str, object]]:
    runtime_config = None
    if ranking is None or retrieval is None:
        runtime_config = load_runtime_config(DEFAULT_CONFIG_PATH)
    ranking = ranking or runtime_config.ranking
    retrieval = retrieval or runtime_config.retrieval
    if limit is None:
        raise ValueError("search_index requires an explicit limit")
    conn = connect(db_path)
    init_db(conn)
    query_terms = normalize_query_terms(query)
    match_query = build_match_query(query)
    note_type_join_filter = ""
    fts_params: list[object] = [match_query]
    if note_type:
        note_type_join_filter = " AND notes.note_type = ?"
        fts_params.append(note_type)
    fts_params.append(retrieval.fts_candidate_limit)
    fts_rows = conn.execute(
        """
        SELECT
          notes.path AS path,
          notes.title AS title,
          notes.note_type AS note_type,
          notes.lead_summary AS lead_summary,
          notes.headings_json AS headings_json,
          notes.tags_json AS tags_json,
          notes.links_out_json AS links_out_json,
          bm25(note_fts) AS bm25_score
        FROM note_fts
        JOIN notes ON notes.path = note_fts.path
        WHERE note_fts MATCH ?
        """ + note_type_join_filter + """
        ORDER BY bm25_score ASC
        LIMIT ?
        """,
        fts_params,
    ).fetchall()
    title_rows = (
        fetch_title_rows(conn, query_terms, retrieval.title_candidate_limit, note_type=note_type)
        if mode == 'title-first'
        else []
    )
    links_rows = [] if mode == 'title-first' else fetch_links_out_rows(
        conn,
        query_terms,
        retrieval.links_out_candidate_limit,
    )
    candidates: dict[str, dict[str, object]] = {}
    for row in title_rows:
        merge_candidate_row(candidates, row, 'title')
    for rank, row in enumerate(fts_rows, start=1):
        merge_candidate_row(candidates, row, 'fts', rank)
    for row in links_rows:
        merge_candidate_row(candidates, row, 'links_out')
    results: list[dict[str, object]] = []
    for row in candidates.values():
        tags = json.loads(row['tags_json'] or '[]')
        headings = json.loads(row['headings_json'] or '[]')
        links_out = json.loads(row['links_out_json'] or '[]')
        fts_score = 1.0 / row['fts_rank'] if row['fts_rank'] else 0.0
        title_score = overlap_score(query_terms, row['title'])
        title_exact_score = exactish_title_score(query, row['title'], ranking)
        lead_score = overlap_score(query_terms, row['lead_summary'])
        heading_score = overlap_score(query_terms, ' '.join(headings))
        tag_score = overlap_score(query_terms, ' '.join(tags))
        links_out_score = best_phrase_match_score(query, query_terms, links_out, ranking)
        note_type_score = ranking.note_type_weights.get(row['note_type'], ranking.note_type_weights.get('other', 0.5))
        matched_terms_count = max(
            count_matching_terms(query_terms, row['title']),
            count_matching_terms(query_terms, row['lead_summary']),
            count_matching_terms(query_terms, ' '.join(headings)),
            count_matching_terms(query_terms, ' '.join(tags)),
            max((count_matching_terms(query_terms, link_target) for link_target in links_out), default=0),
        )
        term_coverage_ratio = (matched_terms_count / len(query_terms)) if query_terms else 0.0
        weights = ranking.weights
        final_score = (
            weights['fts'] * fts_score
            + weights['title'] * title_score
            + weights['title_exact'] * title_exact_score
            + weights['lead_summary'] * lead_score
            + weights['heading'] * heading_score
            + weights['tags'] * tag_score
            + weights['links_out'] * links_out_score
            + weights['note_type'] * note_type_score
        )
        results.append(
            {
                'path': row['path'],
                'title': row['title'],
                'score': round(final_score, 6),
                'tags': tags,
                'lead_summary': row['lead_summary'],
                'headings': headings,
                'snippet': row['lead_summary'][:300].replace('\n', ' '),
                'links_out': links_out,
                'matched_terms_count': matched_terms_count,
                'term_coverage_ratio': round(term_coverage_ratio, 6),
                'candidate_sources': sorted(row['candidate_sources']),
            }
        )
    results.sort(key=lambda item: item['score'], reverse=True)
    if not results:
        return []
    top_score = float(results[0]['score'])
    filtered: list[dict[str, object]] = []
    for position, result in enumerate(results):
        if mode == 'title-first':
            keep = should_keep_title_lookup_result(result, query, query_terms, ranking)
        else:
            keep = should_keep_result(result, position, top_score, retrieval)
        if keep:
            filtered.append(result)
        if len(filtered) >= limit:
            break
    return filtered
