from __future__ import annotations

import requests

from telegram_to_matrix.retrying import is_retryable_http_error, retry_call


class DummyResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def test_retryable_http_statuses() -> None:
    err_500 = requests.HTTPError("x", response=DummyResponse(500))
    err_429 = requests.HTTPError("x", response=DummyResponse(429))
    err_400 = requests.HTTPError("x", response=DummyResponse(400))

    assert is_retryable_http_error(err_500)
    assert is_retryable_http_error(err_429)
    assert not is_retryable_http_error(err_400)


def test_retry_call_retries_until_success() -> None:
    state = {"count": 0}

    def fn() -> str:
        state["count"] += 1
        if state["count"] < 3:
            raise requests.ConnectionError("temporary")
        return "ok"

    result = retry_call(fn, max_attempts=3, base_delay=0.0)
    assert result == "ok"
    assert state["count"] == 3
