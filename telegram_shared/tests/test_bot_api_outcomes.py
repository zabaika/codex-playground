import io
import json
from urllib import error

import pytest

from telegram_shared import bot_api
from telegram_shared.errors import TelegramApiError


@pytest.mark.parametrize("http", [False, True])
def test_explicit_flood_refusal_preserves_structured_retry_metadata(http):
    body = json.dumps({"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 7}}).encode()
    def send(req, timeout):
        if http:
            raise error.HTTPError(req.full_url, 429, "Too Many Requests", hdrs=None, fp=io.BytesIO(body))
        return io.BytesIO(body)
    with pytest.raises(TelegramApiError) as caught:
        bot_api.api_call("fixture-token", "sendMessage", {"chat_id": 100, "text": "text"}, urlopen_func=send)
    assert getattr(caught.value, "request_outcome", None) == "refused"
    assert caught.value.error_code == 429 and caught.value.retry_after == 7


@pytest.mark.parametrize("http_code,body", [
    (502, b""), (502, b'{"ok":false,"error_code":429}'),
    (400, b"<html>proxy failure</html>"), (429, b'{"ok":false,"error_code":true}'),
])
def test_unverified_http_response_does_not_prove_refusal(http_code, body):
    def send(req, timeout):
        raise error.HTTPError(req.full_url, http_code, "fixture", hdrs=None, fp=io.BytesIO(body))
    with pytest.raises(TelegramApiError) as caught:
        bot_api.api_call("fixture-token", "sendMessage", urlopen_func=send)
    assert getattr(caught.value, "request_outcome", None) == "unknown"


def test_timeout_has_unknown_outcome_and_no_retry_metadata():
    def send(req, timeout):
        raise TimeoutError("lost response")
    with pytest.raises(TelegramApiError) as caught:
        bot_api.api_call("fixture-token", "sendMessage", urlopen_func=send)
    assert getattr(caught.value, "request_outcome", None) == "unknown"
    assert caught.value.error_code is None and caught.value.retry_after is None
