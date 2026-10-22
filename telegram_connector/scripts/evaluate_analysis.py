"""Local quality evaluation over explicit source annotations and reviewed outputs."""

import argparse
import copy
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telegram_connector import telegram_analysis as analysis
from telegram_connector import analysis_pipeline
from telegram_connector import analysis_evidence
from telegram_connector import telegram_history_client as history
from telegram_connector.analysis_store import AnalysisStore
from common import json_io, sqlite as common_sqlite, ttl_runner


TRIAL_LIMITS = {"max_llm_calls": 24, "max_provider_requests": 96, "max_total_tokens": 200000}
TRIAL_RESULTS_DIR = "scratch/telegram-connector-analytics/quality-eval/results"


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _source_key(value):
    _require(isinstance(value, dict) and type(value.get("channel_id")) is int and value["channel_id"] != 0
        and type(value.get("message_id")) is int and value["message_id"] > 0, "invalid_source_identity")
    return value["channel_id"], value["message_id"]


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", value) is not None


def _validate_task(task, known):
    for field in ("expected_fact_ids", "critical_fact_ids", "allowed_fact_ids"):
        ids = task[field]
        _require(isinstance(ids, list) and all(isinstance(key, str) for key in ids)
            and len(set(ids)) == len(ids) and set(ids) <= known, "invalid_task_fact_scope")
    expected = set(task["expected_fact_ids"])
    _require(set(task["critical_fact_ids"]) <= expected <= set(task["allowed_fact_ids"]), "invalid_critical_scope")
    for field in ("same_item", "different_items"):
        for group in task[field]:
            _require(isinstance(group, list) and len(group) >= 2 and len(set(group)) == len(group)
                and set(group) <= expected and (field != "different_items" or len(group) == 2), "invalid_fact_group")
    _require(isinstance(task["review_checks"], list) and len(set(task["review_checks"])) == len(task["review_checks"])
        and all(_identifier(key) for key in task["review_checks"]), "invalid_review_checks")


def validate_gold(document):
    _require(isinstance(document, dict) and document.get("version") == 1, "invalid_gold_version")
    gold = copy.deepcopy(document)
    since = datetime.combine(date.fromisoformat(gold["period"]["since"]), time.min, tzinfo=timezone.utc)
    until = datetime.combine(date.fromisoformat(gold["period"]["until"]), time.max, tzinfo=timezone.utc)
    accepted = datetime.fromisoformat(gold["period"]["accepted_at"])
    _require(accepted.tzinfo is not None and since <= until <= accepted, "invalid_gold_period")
    messages = {}
    for message in gold["messages"]:
        key = _source_key(message)
        stamp = datetime.fromisoformat(message["date"])
        _require(key not in messages and isinstance(message["text"], str) and bool(message["text"])
            and stamp.tzinfo is not None and since <= stamp <= until, "invalid_gold_message")
        messages[key] = message["text"]
    facts = set()
    for fact in gold["facts"]:
        _require(_identifier(fact["id"]) and fact["id"] not in facts and isinstance(fact["claim"], str)
            and bool(fact["claim"].strip()) and bool(fact["sources"]), "invalid_gold_fact")
        facts.add(fact["id"])
        for ref in fact["sources"]:
            text = messages.get(_source_key(ref))
            quote = ref["quote"]
            _require(text is not None and isinstance(quote, str) and bool(quote) and text.count(quote) == 1, "invalid_gold_span")
            start, end = text.index(quote), text.index(quote) + len(quote)
            _require(ref.get("start", start) == start and ref.get("end", end) == end, "invalid_gold_offset")
            ref.update(start=start, end=end)
    tasks = set()
    for task in gold["tasks"]:
        _require(_identifier(task["id"]) and task["id"] not in tasks and task["command"] in {"topics", "search", "qa"}, "invalid_gold_task")
        tasks.add(task["id"])
        _validate_task(task, facts)
        _require(bool(task["details"]) and len(set(task["details"])) == len(task["details"])
            and set(task["details"]) <= {"short", "normal", "full"}, "invalid_gold_details")
        if task["command"] == "search":
            _require(isinstance(task["query"], str) and bool(task["query"].strip()) and bool(task["modes"])
                and len(set(task["modes"])) == len(task["modes"]) and set(task["modes"]) <= {"fast", "full"}, "invalid_gold_search")
        else:
            _require(task["query"] is None and task["modes"] == [None], "invalid_gold_command_mode")
    _require(bool(messages) and bool(facts) and bool(tasks), "empty_gold")
    return gold


def expand_cases(document):
    return [{**copy.deepcopy(task), "id": f"{task['id']}-{mode or 'all'}-{detail}", "task_id": task["id"],
             "mode": mode, "detail": detail}
        for task in document["tasks"] for mode in task["modes"] for detail in task["details"]]


def result_surfaces(result):
    surfaces = {"summary": (result.get("summary", ""), None)}
    for number, item in enumerate(result.get("items", [])):
        for field in ("title", "text"):
            surfaces[f"items.{number}.{field}"] = (item[field], number)
        for field in ("conditions", "exceptions", "contradictions"):
            for index, text in enumerate(item.get(field, [])):
                surfaces[f"items.{number}.{field}.{index}"] = (text, number)
    for index, text in enumerate(result.get("limitations", [])):
        surfaces[f"limitations.{index}"] = (text, None)
    _require(all(isinstance(text, str) for text, _ in surfaces.values()), "invalid_result_text")
    return surfaces


def retrieval_scores(gold, case, run):
    """Unique annotated answer sources; primary retrieval and context stay separate."""
    facts = {fact["id"]: fact for fact in gold["facts"]}
    relevant = {_source_key(ref) for key in case["expected_fact_ids"] for ref in facts[key]["sources"]}
    corpus = {_source_key(message) for message in gold["messages"]}
    pools = {"selected_answer_recall": {_source_key(ref) for ref in run["selected_source_refs"]}}
    for label, key in (("original_candidate_answer_recall", "original_only_candidates"),
                       ("expanded_candidate_answer_recall", "expanded_candidates")):
        pools[label] = set()
        for pair in run["retrieval"][key]:
            _require(isinstance(pair, list) and len(pair) == 2, "invalid_retrieval_reference")
            pools[label].add(_source_key({"channel_id": pair[0], "message_id": pair[1]}))
    _require(all(pool <= corpus for pool in pools.values()), "retrieval_outside_corpus")
    return {"answer_source_count": len(relevant), **{label: len(pool & relevant) / len(relevant) if relevant else 1.0
        for label, pool in pools.items()}}


def score_review(gold, case, run, review):
    """Compute metrics from complete, bound review spans; no prose matching/judge."""
    facts = {fact["id"]: fact for fact in gold["facts"]}
    _validate_task(case, set(facts))
    digest = hashlib.sha256(analysis.canonical_json(run["result"]).encode()).hexdigest()
    _require(digest == run["result_sha256"] == review["result_sha256"], "review_result_changed")
    _require(type(review["complete"]) is bool and review["reviewer"]["kind"] in {"human", "assistant"}
        and isinstance(review["reviewer"]["id"], str) and bool(review["reviewer"]["id"].strip()), "invalid_reviewer")
    _require(set(review["checks"]) == set(case["review_checks"])
        and all(type(value) is bool for value in review["checks"].values()), "invalid_review_checks")
    surfaces = result_surfaces(run["result"])
    marks = {key: bytearray(len(text)) for key, (text, _) in surfaces.items()}
    item_sources = [{_source_key(ref) for ref in item.get("sources", [])} for item in run["result"].get("items", [])]
    all_sources = set().union(*item_sources) if item_sources else set()
    allowed = set(case["allowed_fact_ids"])
    covered, groups = set(), {}
    factual = supported = valid_sources = unsupported = uncertain = 0
    for atom in review["claims"]:
        _require(atom["path"] in surfaces and atom["verdict"] in {"supported", "unsupported", "uncertain", "irrelevant", "nonfactual"}, "invalid_review_atom")
        text, item_number = surfaces[atom["path"]]
        start, end = atom["start"], atom["end"]
        _require(type(start) is int and type(end) is int and 0 <= start < end <= len(text)
            and bool(text[start:end].strip()), "invalid_review_span")
        marked = marks[atom["path"]]
        _require(not any(marked[start:end]), "overlapping_review_spans")
        marked[start:end] = b"\1" * (end - start)
        ids = atom["fact_ids"]
        _require(isinstance(ids, list) and len(set(ids)) == len(ids) and set(ids) <= set(facts), "invalid_review_fact")
        refs = {_source_key(ref) for ref in atom["source_refs"]}
        if atom["verdict"] == "nonfactual":
            _require(not ids and not refs, "nonfactual_cannot_cover_facts")
            continue
        factual += 1
        unsupported += atom["verdict"] == "unsupported"
        uncertain += atom["verdict"] == "uncertain"
        attached = all_sources if item_number is None else item_sources[item_number]
        source_sets = [{_source_key(ref) for ref in facts[key]["sources"]} for key in ids]
        source_ok = bool(ids) and bool(refs) and refs <= attached and refs <= set().union(*source_sets)
        source_ok = source_ok and all(refs.intersection(options) for options in source_sets)
        valid_sources += bool(source_ok)
        if atom["verdict"] == "supported" and source_ok and set(ids) <= allowed:
            supported += 1
            covered.update(ids)
            if item_number is not None:
                for key in ids:
                    groups.setdefault(key, set()).add(item_number)
    unreviewed = sum(not marks[key][index] and not char.isspace()
        for key, (text, _) in surfaces.items() for index, char in enumerate(text))
    complete = review["complete"] and unreviewed == 0
    expected, critical = set(case["expected_fact_ids"]), set(case["critical_fact_ids"])
    same = all(set.intersection(*(groups.get(key, set()) for key in group)) for group in case["same_item"])
    separate = all(groups.get(left) and groups.get(right) and groups[left].isdisjoint(groups[right]) for left, right in case["different_items"])
    return {"status": "unreviewed" if not complete else "inconclusive" if uncertain else "scored",
        "fact_precision": supported / factual if factual else 0.0,
        "fact_recall": len(covered & expected) / len(expected) if expected else 1.0,
        "critical_recall": len(covered & critical) / len(critical) if critical else 1.0,
        "source_support": valid_sources / factual if factual else 0.0,
        "unsupported_rate": unsupported / factual if factual else 0.0,
        "covered_fact_ids": sorted(covered), "missing_fact_ids": sorted(expected - covered),
        "unreviewed_characters": unreviewed, "factual_atoms": factual, "grouping_pass": bool(same and separate),
        "structural_pass": bool(complete and not uncertain and unsupported == 0 and supported == factual
            and all(review["checks"].values()) and same and separate),
        **({"retrieval": retrieval_scores(gold, case, run)} if "retrieval" in run else {})}


def _digest(value):
    return hashlib.sha256(analysis.canonical_json(value).encode()).hexdigest()


def trial_config(gold, config, *, config_dir, output_root):
    """Narrow only an isolated evaluation; operator API caps remain upper bounds."""
    effective = analysis.resolve_analysis_settings(config, config_dir=config_dir)
    policy = effective["analysis"]
    _require(len(gold["messages"]) <= policy["max_messages"]
        and len({message["channel_id"] for message in gold["messages"]}) <= policy["max_channels"], "trial_exceeds_operator_scope")
    values = copy.deepcopy(config)
    values.setdefault("paths", {})["history_db"] = str(Path(output_root).resolve() / "history.sqlite3")
    values.setdefault("bridge", {}).update(allowed_chat_ids="100", allowed_user_ids="7", allowed_usernames="")
    values.setdefault("digest_ai", {})["messages_per_ai_pass"] = min(4, effective["digest_ai"]["messages_per_ai_pass"])
    narrowed = values.setdefault("analysis", {})
    narrowed.update({key: min(limit, policy[key]) for key, limit in TRIAL_LIMITS.items()})
    narrowed.update(max_messages=len(gold["messages"]))
    narrowed.setdefault("output", {})["results_dir"] = TRIAL_RESULTS_DIR
    return values


def build_scope(gold, config, *, config_dir, output_root):
    values = trial_config(gold, config, config_dir=config_dir, output_root=output_root)
    settings = analysis.resolve_analysis_settings(values, config_dir=config_dir)
    cases = expand_cases(gold)
    limits = {key: settings["analysis"][key] for key in TRIAL_LIMITS}
    scope = {"version": 1, "gold_sha256": _digest(gold), "settings_sha256": _digest(settings),
        "output_root": str(Path(output_root).resolve()), "model": settings["processing"]["model"],
        "reasoning_effort": settings["digest_ai"]["reasoning_effort"], "prompt_sha256": settings["analysis_prompts"]["hash"],
        "cases": cases, "limits": limits, "total_limits": {key: limit * len(cases) for key, limit in limits.items()},
        "delivery_transport": "local capture; no Telegram requests", "data_scope": "synthetic gold messages only"}
    return {**scope, "scope_sha256": _digest(scope)}


def review_template(case, run):
    return {"result_sha256": run["result_sha256"], "reviewer": {"kind": "assistant", "id": "unassigned"},
        "complete": False, "checks": {key: False for key in case["review_checks"]},
        "claims": [{"path": path, "start": 0, "end": len(text), "verdict": "uncertain", "fact_ids": [], "source_refs": []}
            for path, (text, _) in result_surfaces(run["result"]).items() if text.strip()]}


@contextmanager
def capture_model_responses(path):
    """Keep synthetic model outputs, including self-reported limitations, without requests/secrets."""
    original = analysis_pipeline.request_counted_analysis_payload
    responses = []
    def request(store, job_id, batch_id, **kwargs):
        result = original(store, job_id, batch_id, **kwargs)
        responses.append({"job_id": job_id, "batch_id": batch_id,
            "attempt_id": result["attempt_id"], "response": copy.deepcopy(result["response"])})
        json_io.write_json_atomic(path, responses)
        return result
    analysis_pipeline.request_counted_analysis_payload = request
    try:
        yield
    finally:
        analysis_pipeline.request_counted_analysis_payload = original


@contextmanager
def capture_extraction_rejections(path):
    """Retain rejected synthetic evidence for diagnosis, preserving engine validation."""
    original = analysis_evidence.validate_evidence_records
    rejected = []
    def validate(store, job_id, batch_id, **kwargs):
        try:
            return original(store, job_id, batch_id, **kwargs)
        except ValueError as exc:
            rejected.append({"job_id": job_id, "batch_id": batch_id,
                "error": str(exc), "document": copy.deepcopy(kwargs["document"])})
            json_io.write_json_atomic(path, rejected)
            raise
    analysis_evidence.validate_evidence_records = validate
    try:
        yield
    finally:
        analysis_evidence.validate_evidence_records = original


def execute_case(gold, config, scope, case_id, *, output_root, config_dir=None, live=False, api_key=None, urlopen_func=None):
    _require(live or urlopen_func is not None, "live_scope_not_approved")
    config_dir = Path(config_dir or ROOT / "telegram_connector/config")
    output_root = Path(output_root).resolve()
    _require(build_scope(gold, config, config_dir=config_dir, output_root=output_root) == scope, "approved_scope_changed")
    case = next((item for item in scope["cases"] if item["id"] == case_id), None)
    _require(case is not None, "unknown_case")
    values = trial_config(gold, config, config_dir=config_dir, output_root=output_root)
    db_path = output_root / "history.sqlite3"
    _require(db_path.resolve() != analysis.bridge.resolve_history_db_path(config).resolve(), "real_history_db_forbidden")
    owner_path, captured_path = output_root / "run.json", output_root / f"{case_id}.json"
    with analysis.bridge.analysis_ownership_lock(db_path, owner="supervisor") as owned:
        _require(owned is not None, "evaluation_already_running")
        first = not owner_path.exists()
        if first:
            _require(not db_path.exists() and not captured_path.exists(), "unowned_evaluation_data")
            owner = {"scope_sha256": scope["scope_sha256"], "cases": {}}
            json_io.write_json_atomic(owner_path, owner)
        else:
            owner = json.loads(owner_path.read_text())
            _require(owner["scope_sha256"] == scope["scope_sha256"] and db_path.exists(), "evaluation_ownership_changed")
        if captured_path.exists():
            captured = json.loads(captured_path.read_text())
            _require(captured["scope_sha256"] == scope["scope_sha256"] and captured["case_id"] == case_id
                and captured["job_id"] == owner["cases"].get(case_id)
                and captured["result_sha256"] == _digest(captured["result"]), "captured_result_changed")
            return captured
        _require(case_id not in owner["cases"], "uncaptured_case_requires_inspection")
        conn = common_sqlite.connect_sqlite(db_path)
        try:
            history.init_db(conn)
            store = AnalysisStore(conn)
            peers = list(dict.fromkeys(message["channel_id"] for message in gold["messages"]))
            refs = {f"@quality{peer}": peer for peer in peers}
            client = CorpusClient(refs)
            if first:
                for peer in peers:
                    history.upsert_channel(conn, client.entity(peer))
                for message in gold["messages"]:
                    history.upsert_message(conn, client.entity(message["channel_id"]), SimpleNamespace(
                        id=message["message_id"], date=datetime.fromisoformat(message["date"]),
                        message=message["text"], media=None), None, None, None, None)
                now = datetime.now(timezone.utc)
                queued = analysis.enqueue_fts_backfill(store, values, rebuild=False, now=now)
                index = store.claim_job(queued["job_id"], max_running_jobs=1, now=now)
                while history.backfill_fts_page(store, index["job_id"], generation=index["generation"])["state"] != "ready":
                    pass
            access = {"chats": frozenset({"100"}), "users": frozenset({"7"}), "usernames": frozenset()}
            text = f"/{case['command']} {','.join(refs)} since={gold['period']['since']} until={gold['period']['until']}"
            if case["command"] == "search":
                text += f" {shlex.quote(case['query'])} --mode {case['mode']}"
            text += f" --detail {case['detail']}"
            update = {"update_id": scope["cases"].index(case) + 1,
                "message": {"chat": {"id": 100}, "from": {"id": 7}, "text": text}}
            now = datetime.now(timezone.utc)
            queued, _ = analysis.admit_analysis_job(store, values, update, access=access,
                bot_namespace="quality:" + scope["scope_sha256"], now=now, config_dir=config_dir)
            owner["cases"][case_id] = queued["job_id"]
            json_io.write_json_atomic(owner_path, owner)
            job = store.claim_job(queued["job_id"], max_running_jobs=1, now=now)
            _require(job is not None, "evaluation_claim_failed")
            store.save_checkpoint(job["job_id"], {"history": {"refs": refs, "sources": {
                str(peer): {"mode": "reuse", "state": "complete"} for peer in peers}}},
                generation=job["generation"], max_bytes=job["request"]["settings"]["analysis"]["max_intermediate_bytes"])
            runtime = history.RuntimeConfig(db_path=db_path, media_root=output_root / "media",
                user_session_name="quality-user", bot_session_name="quality-bot", api_id="1", api_hash="unused",
                phone="unused", bot_token="unused", user_password="", tesseract_binary="tesseract", vision_prompt="",
                sync_batch_size=int(values["sync"]["batch_size"]), default_auth_mode="user", public_auth_mode="bot",
                private_auth_mode="user", default_channels=[])
            local_messages, presentation = [], {}
            def capture(token, chat, text, parse_mode, **kwargs):
                local_messages.append(text)
                return {"message_id": len(local_messages), "chat": {"id": 100}}
            def execute(store, current):
                with capture_extraction_rejections(output_root / f"{case_id}.validation.json"), \
                        capture_model_responses(output_root / f"{case_id}.stages.json"):
                    status = analysis.execute_analysis_job(store, current, runtime=runtime, client=client,
                        api_key=api_key, token="local-capture", access_provider=lambda: access,
                        urlopen_func=urlopen_func, send_func=capture)
                saved = store.get_job(current["job_id"])
                presentation.update(analysis.prepare_analysis_presentation(store, current["job_id"], saved["result"], generation=current["generation"]))
                return status
            exit_code = analysis.run_analysis(store, db_path, job_id=job["job_id"], generation=job["generation"], executor=execute)
            saved = store.get_job(job["job_id"])
            selected = []
            for peer in peers:
                selected.extend({"channel_id": row["channel_id"], "message_id": row["message_id"]}
                    for row in store.manifest_inputs_page(job["job_id"], channel_id=peer, limit=len(gold["messages"])))
            result = presentation.get("result")
            captured = {"version": 1, "case_id": case_id, "job_id": job["job_id"], "scope_sha256": scope["scope_sha256"],
                "gold_sha256": scope["gold_sha256"], "model": scope["model"], "reasoning_effort": scope["reasoning_effort"],
                "prompt_sha256": scope["prompt_sha256"], "status": saved["status"], "exit_code": exit_code,
                "delivery_transport": "local_capture", "local_messages": local_messages, "full_path": saved["full_path"],
                "selected_source_refs": selected, "result": result, "result_sha256": _digest(result),
                "usage": {key: saved[key] for key in ("llm_calls", "provider_requests", "used_tokens", "reserved_tokens")}}
            if case["mode"] == "fast" and saved["checkpoint"].get("manifest"):
                request, settings = saved["request"]["request"], saved["request"]["settings"]
                manifest = saved["checkpoint"]["manifest"]
                prepared = analysis.normalize_search_query(case["query"], settings["analysis"]["search"])
                baseline = store.search_candidates(analysis.compile_search_fts(prepared["original_terms"]),
                    since=request["since"], until=min(request["until"], request["cutoff_at"]),
                    ceilings=manifest["message_ceilings"], limit=min(settings["analysis"]["search"]["max_candidates"], settings["analysis"]["max_messages"]))
                captured["retrieval"] = {"comparison": "primary candidates before context recovery",
                    "original_only_candidates": [[row["channel_id"], row["message_id"]] for row in baseline],
                    "expanded_candidates": manifest["candidate_refs"]}
            json_io.write_json_atomic(captured_path, captured)
            return captured
        finally:
            conn.close()


class CorpusClient:
    """Only synthetic identities; fetching Telegram history is forbidden here."""
    def __init__(self, refs):
        self.refs = refs

    @staticmethod
    def entity(peer):
        # A synthetic Chat has no generated public/private Telegram message URL.
        return Chat(id=peer, title=f"Quality fixture {peer}")

    async def get_entity(self, ref):
        return self.entity(self.refs[ref])

    async def get_messages(self, *args, **kwargs):
        raise RuntimeError("Quality evaluation cannot fetch Telegram messages")


class Chat(SimpleNamespace):
    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=ROOT / "telegram_connector/tests/fixtures/analysis_quality_gold.json")
    parser.add_argument("--output", type=Path, default=ROOT / "scratch/telegram-connector-analytics/quality-eval")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "telegram_connector/config")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    commands.add_parser("prepare")
    run = commands.add_parser("run")
    run.add_argument("--case", required=True, help="Case ID or all; stop after the first incomplete case")
    run.add_argument("--approved-scope", required=True, help="Explicitly approved scope SHA-256")
    run.add_argument("--worker-inner", action="store_true", help=argparse.SUPPRESS)
    for command in ("review-template", "score"):
        child = commands.add_parser(command)
        child.add_argument("--case", required=True)
    arguments = parser.parse_args(argv)
    try:
        gold = validate_gold(json.loads(arguments.gold.read_text()))
        if arguments.command == "validate":
            print(f"Validated {len(gold['messages'])} messages, {len(gold['facts'])} facts, {len(expand_cases(gold))} cases")
            return 0
        scope_path = arguments.output / "scope.json"
        if arguments.command == "prepare":
            scope = build_scope(gold, analysis.bridge.load_runtime_config(), config_dir=arguments.config_dir, output_root=arguments.output)
            if scope_path.exists():
                _require(json.loads(scope_path.read_text()) == scope, "existing_scope_changed")
            else:
                json_io.write_json_atomic(scope_path, scope)
            print(scope_path)
            return 0
        scope = json.loads(scope_path.read_text())
        _require(scope["gold_sha256"] == _digest(gold), "gold_changed")
        if arguments.command == "run":
            _require(arguments.approved_scope == scope["scope_sha256"], "approved_scope_changed")
            config = analysis.bridge.load_runtime_config()
            _require(build_scope(gold, config, config_dir=arguments.config_dir, output_root=arguments.output) == scope, "approved_scope_changed")
            cases = scope["cases"] if arguments.case == "all" else [case for case in scope["cases"] if case["id"] == arguments.case]
            _require(bool(cases), "unknown_case")
            if not arguments.worker_inner:
                settings = analysis.resolve_analysis_settings(config, config_dir=arguments.config_dir)
                process = settings["process"]
                return ttl_runner.run_with_ttl(
                    [sys.executable, str(Path(__file__).resolve()), "--gold", str(arguments.gold.resolve()),
                     "--output", str(arguments.output.resolve()), "--config-dir", str(arguments.config_dir.resolve()),
                     "run", "--case", arguments.case, "--approved-scope", arguments.approved_scope, "--worker-inner"],
                    timeout_seconds=settings["bridge"]["worker_process_timeout_seconds"],
                    grace_seconds=process["default_termination_grace_seconds"],
                    poll_interval_seconds=process["poll_interval_seconds"], timeout_exit_code=process["timeout_exit_code"],
                    term_signal=process["term_signal"], kill_signal=process["kill_signal"],
                    audit_file=arguments.output / "evaluation.last_attempt.json", timeout_reason="process_ttl_expired",
                    use_caffeinate=False, sleep_policy="interrupt",
                    env=analysis.bridge.build_history_client_subprocess_env({
                        key: os.environ.get(key, "") for key in analysis.bridge.HISTORY_CLIENT_SECRET_ENV_MAP
                    }))
            for case in cases:
                captured = execute_case(gold, config, scope, case["id"], output_root=arguments.output,
                    config_dir=arguments.config_dir, live=True)
                print(f"{case['id']}: {captured['status']} ({captured['usage']['llm_calls']} inference attempts)")
                if captured["status"] != "completed" or captured["exit_code"] != 0:
                    return 1
            return 0
        case = next((case for case in scope["cases"] if case["id"] == arguments.case), None)
        _require(case is not None, "unknown_case")
        captured = json.loads((arguments.output / f"{case['id']}.json").read_text())
        _require(captured["scope_sha256"] == scope["scope_sha256"] and captured["result"] is not None, "result_unavailable")
        review_path = arguments.output / f"{case['id']}.review.json"
        if arguments.command == "review-template":
            _require(not review_path.exists(), "review_already_exists")
            json_io.write_json_atomic(review_path, review_template(case, captured))
            print(review_path)
            return 0
        score = score_review(gold, case, captured, json.loads(review_path.read_text()))
        json_io.write_json_atomic(arguments.output / f"{case['id']}.score.json", score)
        print(analysis.canonical_json(score))
        return 0 if score["status"] == "scored" else 1
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"Evaluation error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
