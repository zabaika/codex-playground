from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_runtime_config
from .index_db import connect, get_status, init_db
from .indexer import build_or_update_index
from .search import search_index


def resolve_runtime(args: argparse.Namespace):
    config = load_runtime_config(Path(args.config_path))
    vault_root = Path(args.vault_root) if getattr(args, 'vault_root', None) else config.vault_root
    db_path = Path(args.db_path) if getattr(args, 'db_path', None) else config.db_path
    state_path = Path(args.state_path) if getattr(args, 'state_path', None) else config.state_path
    return vault_root, db_path, state_path, config.scope, config.ranking, config.retrieval


def add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument('--config-path', default=str(DEFAULT_CONFIG_PATH), help='Path to runtime.local.toml')
    parser.add_argument('--vault-root', help='Path to vault root')
    parser.add_argument('--db-path', help='Path to sqlite index')
    parser.add_argument('--state-path', help='Path to state json')


def cmd_build(args: argparse.Namespace) -> int:
    vault_root, db_path, state_path, scope, _, _ = resolve_runtime(args)
    payload = build_or_update_index(vault_root, db_path, state_path, scope)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    return cmd_build(args)


def cmd_search(args: argparse.Namespace) -> int:
    _, db_path, _, _, ranking, retrieval = resolve_runtime(args)
    limit = args.limit if args.limit is not None else retrieval.default_limit
    results = search_index(db_path, args.query, ranking, retrieval, limit=limit)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for item in results:
            print(f"{item['score']:.3f}\t{item['path']}\t{item['snippet']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    _, db_path, state_path, scope, ranking, retrieval = resolve_runtime(args)
    conn = connect(db_path)
    init_db(conn)
    payload = get_status(conn)
    payload['configured_scope'] = {
        'include_roots': scope.include_roots,
        'exclude_roots': scope.exclude_roots,
        'exclude_globs': scope.exclude_globs,
    }
    payload['configured_ranking'] = {
        'weights': ranking.weights,
        'note_type_weights': ranking.note_type_weights,
        'exact_title_bonus': ranking.exact_title_bonus,
    }
    payload['configured_retrieval'] = {
        'default_limit': retrieval.default_limit,
        'min_term_coverage_ratio': retrieval.min_term_coverage_ratio,
        'min_score_ratio_to_top': retrieval.min_score_ratio_to_top,
        'always_keep_top_n': retrieval.always_keep_top_n,
    }
    if state_path.exists():
        payload['state'] = json.loads(state_path.read_text(encoding='utf-8'))
    else:
        payload['state'] = None
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='kb-index')
    subparsers = parser.add_subparsers(dest='command', required=True)

    build_parser_cmd = subparsers.add_parser('build')
    add_common_paths(build_parser_cmd)
    build_parser_cmd.set_defaults(func=cmd_build)

    update_parser_cmd = subparsers.add_parser('update')
    add_common_paths(update_parser_cmd)
    update_parser_cmd.set_defaults(func=cmd_update)

    search_parser_cmd = subparsers.add_parser('search')
    add_common_paths(search_parser_cmd)
    search_parser_cmd.add_argument('query')
    search_parser_cmd.add_argument('--limit', type=int, help='Maximum number of notes to return')
    search_parser_cmd.add_argument('--json', action='store_true')
    search_parser_cmd.set_defaults(func=cmd_search)

    status_parser_cmd = subparsers.add_parser('status')
    add_common_paths(status_parser_cmd)
    status_parser_cmd.set_defaults(func=cmd_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == '__main__':
    raise SystemExit(main())
