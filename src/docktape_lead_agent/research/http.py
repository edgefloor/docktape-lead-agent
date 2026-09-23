from __future__ import annotations

import time
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from ..domain.evidence import utc_now

HTTP_BODY_LIMIT = 2_000_000
RETRY_DELAY_LIMIT = 3.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}


def _retry_after(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    if not raw:
        return 0.5
    try:
        return min(max(float(raw), 0), RETRY_DELAY_LIMIT)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(raw) - utc_now()).total_seconds()
            return min(max(delay, 0), RETRY_DELAY_LIMIT)
        except (TypeError, ValueError):
            return 0.5


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_bytes: int = HTTP_BODY_LIMIT,
    on_attempt: Callable[[int, str, float], None] | None = None,
    **kwargs: Any,
) -> tuple[httpx.Response, bytes, bool]:
    last_error: BaseException | None = None
    for attempt in range(2):
        started = time.monotonic()
        try:
            with client.stream(method, url, **kwargs) as response:
                if response.status_code in RETRYABLE_STATUS and attempt == 0:
                    if on_attempt:
                        on_attempt(
                            attempt,
                            f"http_{response.status_code}_retry",
                            time.monotonic() - started,
                        )
                    delay = _retry_after(response)
                    time.sleep(delay)
                    continue
                response.raise_for_status()
                chunks: list[bytes] = []
                size = 0
                truncated = False
                for chunk in response.iter_bytes():
                    remaining = max_bytes - size
                    if remaining <= 0:
                        truncated = True
                        break
                    chunks.append(chunk[:remaining])
                    size += min(len(chunk), remaining)
                    if len(chunk) > remaining:
                        truncated = True
                        break
                if on_attempt:
                    on_attempt(attempt, f"http_{response.status_code}", time.monotonic() - started)
                return response, b"".join(chunks), truncated
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if on_attempt:
                on_attempt(attempt, type(exc).__name__, time.monotonic() - started)
            last_error = exc
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise
        except httpx.HTTPStatusError as exc:
            if on_attempt:
                on_attempt(attempt, f"http_{exc.response.status_code}", time.monotonic() - started)
            raise
    assert last_error is not None
    raise last_error
