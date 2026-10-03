"""HTTP transport: one shared client plus backoff retries for transport-level failures."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from .protocol import LLMError

TIMEOUT_SECONDS = 60.0
# Connect timeout is tighter: an unreachable endpoint fails fast while reads stay generous.
CONNECT_TIMEOUT_SECONDS = 10.0

#: Retryable statuses (rate limits, gateway failures); other 4xx answers would repeat identically.
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})

#: Total attempts per request (one initial send plus two retries).
DEFAULT_ATTEMPTS = 3
#: Backoff base: the nth retry waits base * 2**(n-1) seconds (0.5 / 1.0).
BASE_DELAY_SECONDS = 0.5
#: Random jitter added to each backoff wait so parallel clients do not retry in lockstep.
DEFAULT_JITTER_SECONDS = 0.25

# One process-wide client: httpx.Client is thread-safe and reuse avoids a handshake per call.
_CLIENT_LOCK = threading.Lock()
_CLIENT: httpx.Client | None = None

#: Module-level default sleeper; tests monkeypatch it to remove real waits.
_sleep = time.sleep


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)


def shared_client() -> httpx.Client:
    """Return the shared HTTP client, creating it on first use."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT.is_closed:
            _CLIENT = httpx.Client(timeout=_timeout())
        return _CLIENT


@dataclass(frozen=True)
class RetryPolicy:
    """Retry parameters for one request; sleeper defaults to the module-level sleep function."""

    attempts: int = DEFAULT_ATTEMPTS
    base_delay: float = BASE_DELAY_SECONDS
    jitter: float = DEFAULT_JITTER_SECONDS
    sleeper: Callable[[float], None] | None = None
    # (retry number, seconds waited, reason) for observation only; it does not affect decisions.
    on_retry: Callable[[int, float, str], None] | None = None

    def _wait(self, seconds: float) -> None:
        (self.sleeper or _sleep)(seconds)


def _retry_after(response: httpx.Response) -> float | None:
    """Return the server-suggested wait in seconds; the HTTP-date form is not parsed."""
    raw = response.headers.get("retry-after", "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _delay(policy: RetryPolicy, response: httpx.Response | None, attempt: int) -> float:
    """Compute the wait before a retry: Retry-After wins over the backoff sequence."""
    if response is not None:
        hinted = _retry_after(response)
        if hinted is not None:
            return hinted
    delay = policy.base_delay * (2 ** (attempt - 1))
    if policy.jitter > 0:
        delay += random.uniform(0, policy.jitter)
    return delay


def _retryable(policy: RetryPolicy, attempt: int) -> bool:
    return attempt < policy.attempts


def send(
    http: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: Any = None,
    policy: RetryPolicy | None = None,
) -> httpx.Response:
    """Send a non-streaming request, retrying transport failures, and return the last response."""
    plan = policy or RetryPolicy()
    attempt = 0
    while True:
        attempt += 1
        try:
            response = http.request(method, url, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            if not _retryable(plan, attempt):
                raise LLMError(f"请求 {url} 失败：{exc}") from exc
            delay = _delay(plan, None, attempt)
            plan.on_retry and plan.on_retry(attempt, delay, f"network: {exc}")
            plan._wait(delay)
            continue
        # Status codes stay the caller's business: overflow detection needs the response body.
        if response.status_code in RETRYABLE_STATUS and _retryable(plan, attempt):
            delay = _delay(plan, response, attempt)
            plan.on_retry and plan.on_retry(attempt, delay, f"HTTP {response.status_code}")
            plan._wait(delay)
            continue
        return response


def send_stream(
    http: httpx.Client,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: Any = None,
    policy: RetryPolicy | None = None,
) -> httpx.Response:
    """Send a streaming request and return the response once its status is known."""
    plan = policy or RetryPolicy()
    attempt = 0
    while True:
        attempt += 1
        request = http.build_request(method, url, json=json_body, headers=headers)
        try:
            response = http.send(request, stream=True)
        except httpx.HTTPError as exc:
            if not _retryable(plan, attempt):
                raise LLMError(f"请求 {url} 失败：{exc}") from exc
            delay = _delay(plan, None, attempt)
            plan.on_retry and plan.on_retry(attempt, delay, f"network: {exc}")
            plan._wait(delay)
            continue
        if response.status_code in RETRYABLE_STATUS and _retryable(plan, attempt):
            delay = _delay(plan, response, attempt)
            plan.on_retry and plan.on_retry(attempt, delay, f"HTTP {response.status_code}")
            response.close()
            plan._wait(delay)
            continue
        # The caller owns closing the returned body; errors while iterating it are not retried.
        return response
