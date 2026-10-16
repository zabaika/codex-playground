from datetime import datetime, timezone

import pytest

from telegram_connector import telegram_bridge as bridge


NOW = datetime(2026, 10, 15, 12, 30, tzinfo=timezone.utc)


@pytest.fixture
def policy():
    # Explicit policy for the pure parser; S02 owns production resolution.
    return {
        "channels": {"default_list": ["@must_not_be_used"]},
        "digest": {"since": "yesterday", "until": "yesterday"},
        "analysis": {
            "max_channels": 32,
            "search": {"max_query_chars": 2000, "mode": "fast"},
            "output": {
                "default_detail": "normal", "max_items": 50,
                "sources_per_item": 3,
                "profiles": {
                    "short": {"default_limit": 5, "max_items": 10},
                    "normal": {"default_limit": 15, "max_items": 30},
                    "full": {"default_limit": 30, "max_items": 50},
                },
            },
        },
    }


@pytest.mark.parametrize("text", ["/topics", '/search "банки"', "/qa"])
def test_s01_request_contract_requires_channels(text, policy, monkeypatch):
    def unexpected_config_read():
        pytest.fail("Pure request parsing must use its injected policy")

    monkeypatch.setattr(bridge, "load_runtime_config", unexpected_config_read)
    parser = getattr(bridge, "parse_analysis_request", None)
    assert callable(parser), "New commands need a shared request parser"
    with pytest.raises(ValueError, match="[Cc]hannel"):
        parser(text, policy=policy, now=NOW)


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
def test_s01_request_contract_typed_defaults(command, policy):
    query = ' "Какие банки  открывают счета?"' if command == "search" else ""
    result = bridge.parse_analysis_request(
        f"/{command} @one, @two -100123{query}", policy=policy, now=NOW,
    )
    assert result.command == command
    assert result.channels == ("@one", "@two", "-100123")
    assert result.since == datetime(2026, 10, 14, tzinfo=timezone.utc)
    assert result.until == datetime(2026, 10, 14, 23, 59, 59, tzinfo=timezone.utc)
    assert result.accepted_at == result.cutoff_at == NOW
    assert result.detail == "normal"
    assert result.limit == 15
    assert result.sources == 3
    assert result.query == ("Какие банки  открывают счета?" if command == "search" else None)
    assert result.mode == ("fast" if command == "search" else None)


@pytest.mark.parametrize("alias,day", [("today", 15), ("yesterday", 14), ("week", 8), ("month", 15), ("-3d", 12)])
def test_s01_request_contract_existing_aliases(alias, day, policy):
    result = bridge.parse_analysis_request(f"/topics @one {alias}", policy=policy, now=NOW)
    month = 9 if alias == "month" else 10
    assert result.since == datetime(2026, month, day, tzinfo=timezone.utc)
    assert result.until == datetime(2026, month, day, 23, 59, 59, tzinfo=timezone.utc)


def test_s01_request_contract_default_dates_and_single_clock(policy):
    policy["digest"] = {"since": "week", "until": "today"}
    moment = datetime(2026, 10, 15, 0, 5, tzinfo=timezone.utc)
    result = bridge.parse_analysis_request("/qa @one", policy=policy, now=moment)
    assert result.since == datetime(2026, 10, 8, tzinfo=timezone.utc)
    assert result.until == datetime(2026, 10, 15, 23, 59, 59, tzinfo=timezone.utc)
    assert result.cutoff_at == moment


@pytest.mark.parametrize("query", ["week", "2026-09-01", "@looks_like_channel", "банки  Испании"])
def test_s01_request_contract_query_position_and_preserved_text(query, policy):
    result = bridge.parse_analysis_request(f'/search @one "{query}"', policy=policy, now=NOW)
    assert result.query == query
    assert result.since.day == 14
    shorthand = bridge.parse_analysis_request(f'/search @one week "{query}"', policy=policy, now=NOW)
    assert shorthand.query == query
    assert shorthand.since.day == 8


def test_s01_request_contract_shorthand_wins_and_explicit_zone(policy):
    short = bridge.parse_analysis_request(
        "/topics @one since=2026-10-01 yesterday until=today", policy=policy, now=NOW,
    )
    assert short.since.day == short.until.day == 14
    zoned = bridge.parse_analysis_request(
        "/qa @one since=2026-10-14T23:10:00-02:00 until=2026-10-15T02:30:00Z",
        policy=policy, now=NOW,
    )
    assert zoned.since == datetime(2026, 10, 15, 1, 10, tzinfo=timezone.utc)
    assert zoned.until == datetime(2026, 10, 15, 2, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("command", ["topics", "search", "qa"])
@pytest.mark.parametrize("detail,limit", [("short", 5), ("normal", 15), ("full", 30)])
def test_s01_request_contract_detail_and_cli_equivalence(command, detail, limit, policy):
    query = ["банки  и сроки"] if command == "search" else []
    args = [command, "@one", "--since", "week", "--until", "today", *query,
            "--detail", detail, "--sources", "2"]
    cli = bridge.parse_analysis_request(args, policy=policy, now=NOW)
    text_query = ' "банки  и сроки"' if query else ""
    bot = bridge.parse_analysis_request(
        f'/{command}@FixtureBot @one since=week until=today{text_query} --detail {detail} --sources 2',
        policy=policy, now=NOW,
    )
    assert cli == bot
    assert cli.detail == detail
    assert cli.limit == limit
    assert cli.sources == 2


def test_s01_request_contract_overrides_respect_config(policy):
    result = bridge.parse_analysis_request(
        '/search @one "банки" --mode=full --detail=short --limit=7 --sources=1',
        policy=policy, now=NOW,
    )
    assert (result.mode, result.detail, result.limit, result.sources) == ("full", "short", 7, 1)
    policy["analysis"]["output"]["default_detail"] = "short"
    policy["analysis"]["search"]["mode"] = "full"
    default = bridge.parse_analysis_request('/search @one "банки"', policy=policy, now=NOW)
    assert (default.mode, default.detail, default.limit) == ("full", "short", 5)


@pytest.mark.parametrize("text", [
    "", "/digest @one", "/topics @one unexpected", "/topics @one today yesterday",
    "/topics 123", '/search @one ""', '/search @one "   "',
    "/search @one banks in Spain", "/topics @one --include-comments", "/qa @one --model gpt",
    "/topics @one --mode full", "/topics @one --detail verbose", "/topics @one --detail",
    '/search @one "банки" --mode other',
    "/topics @one --limit 0", "/topics @one --limit -1", "/topics @one --limit 1.5",
    "/topics @one --detail short --limit 11", "/qa @one --limit 51",
    "/topics @one --sources 0", "/topics @one --sources 4",
    "/topics @one since=2026-10-15 until=2026-10-14", "/topics @one since=2026-10-16 until=2026-10-17",
    "/topics @one since=invalid", "/topics @one since=2026-02-30", "/topics @one since=",
    "/topics @one until=", "/topics @one since=week since=today",
    "/qa @one --detail short --detail full", "/topics @one 17", '/search @one "unterminated',
])
def test_s01_request_contract_rejects_invalid_input(text, policy):
    with pytest.raises(ValueError):
        bridge.parse_analysis_request(text, policy=policy, now=NOW)


def test_s01_request_contract_raw_channel_and_query_caps(policy):
    policy["analysis"]["max_channels"] = 1
    with pytest.raises(ValueError, match="[Cc]hannel"):
        bridge.parse_analysis_request("/topics @same,@same", policy=policy, now=NOW)
    policy["analysis"]["search"]["max_query_chars"] = 3
    with pytest.raises(ValueError, match="[Qq]uery"):
        bridge.parse_analysis_request('/search @one "long"', policy=policy, now=NOW)
    assert bridge.parse_analysis_request('/search @one "abc"', policy=policy, now=NOW).query == "abc"


def test_s01_request_contract_global_cap_can_be_lower_than_profile(policy):
    policy["analysis"]["output"]["max_items"] = 7
    with pytest.raises(ValueError):
        bridge.parse_analysis_request("/qa @one --limit 8", policy=policy, now=NOW)


def test_s01_request_contract_immutable_scope(policy):
    from dataclasses import FrozenInstanceError

    result = bridge.parse_analysis_request("/topics @one", policy=policy, now=NOW)
    with pytest.raises(FrozenInstanceError):
        result.channels = ("@other",)
