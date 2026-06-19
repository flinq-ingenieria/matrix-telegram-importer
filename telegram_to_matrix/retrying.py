from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

import requests

T = TypeVar("T")


class RetryableError(RuntimeError):
    pass


def is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, requests.Timeout | requests.ConnectionError):
        return True
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        if response is None:
            return True
        status = response.status_code
        return status == 429 or 500 <= status < 600
    return False


def retry_call(
    fn: Callable[[], T],
    *,
    max_attempts: int,
    base_delay: float,
    should_retry: Callable[[Exception], bool] = is_retryable_http_error,
) -> T:
    attempt = 1
    while True:
        try:
            return fn()
        except Exception as exc:
            if attempt >= max_attempts or not should_retry(exc):
                raise
            sleep_for = base_delay * (2 ** (attempt - 1))
            jitter = random.uniform(0.0, max(0.001, sleep_for * 0.15))
            time.sleep(sleep_for + jitter)
            attempt += 1
