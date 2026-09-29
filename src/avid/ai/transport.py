"""HTTP 传输：共享客户端 + 退避重试。

重试只管「传输层可重试」的失败——429 / 5xx / 连接与发送阶段的网络错误；协议层
的错误分类（上下文超限、非 200 的其余状态）归各 provider 模块。分层依据：网络
不可靠是所有 provider 共有的约束（判据 §9 对每个远程调用建模丢失/延迟/重复），
而"哪些状态可重试"三家一致，"哪些错误可恢复"三家不同。

流式的重试窗口只到**响应头到手**为止：一旦开始迭代响应体，delta 已经回调出去，
重试会造成重复正文——那时失败就是失败。

重试的等待通过 ``RetryPolicy.sleeper`` 注入；缺省走模块级 ``_sleep``，测试可以
monkeypatch 它消除真实等待（本仓库的测试不真睡）。
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from .protocol import LLMError

TIMEOUT_SECONDS = 60.0
# 连接超时单独收紧：端点不可达时不该等满 60 秒（读超时仍给长回答留足）。
CONNECT_TIMEOUT_SECONDS = 10.0

#: 这些状态码值得换一次连接再试：限流与网关类故障。其余 4xx 是请求本身的问题，
#: 原样重发只会得到同一个答案（401 不会因为等了两秒就变对）。
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504, 529})

#: 一次请求的总尝试次数（1 次首发 + 2 次重试）。3 次后仍失败就是故障，不是抖动。
DEFAULT_ATTEMPTS = 3
#: 退避基数：第 n 次重试前等 base * 2**(n-1) 秒（0.5 / 1.0），加抖动防惊群。
BASE_DELAY_SECONDS = 0.5
DEFAULT_JITTER_SECONDS = 0.25

# 进程内复用一个 Client。以前每次调用都新建 `httpx.Client` 再关掉：每轮模型调用
# 都要重新 TCP/TLS 握手，多轮 agent 与 subagent 场景下线性叠加。`httpx.Client`
# 是线程安全的（连接池自己带锁），所以整个进程共用一个实例。
_CLIENT_LOCK = threading.Lock()
_CLIENT: httpx.Client | None = None

#: 模块级缺省等待函数。测试 monkeypatch 它来消除真实 sleep（见模块文档）。
_sleep = time.sleep


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS)


def shared_client() -> httpx.Client:
    """共享的 HTTP 客户端（首次调用时建，之后复用）。"""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None or _CLIENT.is_closed:
            _CLIENT = httpx.Client(timeout=_timeout())
        return _CLIENT


@dataclass(frozen=True)
class RetryPolicy:
    """一次请求的重试参数。``sleeper`` 缺省走模块级 ``_sleep``。"""

    attempts: int = DEFAULT_ATTEMPTS
    base_delay: float = BASE_DELAY_SECONDS
    jitter: float = DEFAULT_JITTER_SECONDS
    sleeper: Callable[[float], None] | None = None
    # (第几次重试, 等了多少秒, 原因)——观测与日志用，不参与决策。
    on_retry: Callable[[int, float, str], None] | None = None

    def _wait(self, seconds: float) -> None:
        (self.sleeper or _sleep)(seconds)


def _retry_after(response: httpx.Response) -> float | None:
    """服务端给的等待秒数。只认数字形式；HTTP 日期不解析（等不出那个精度）。"""
    raw = response.headers.get("retry-after", "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _delay(policy: RetryPolicy, response: httpx.Response | None, attempt: int) -> float:
    """这次重试等多久。Retry-After 优先于退避序列；抖动只加在退避上。"""
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
    """发一个非流式请求，重试传输层可重试的失败，返回**最后一个**响应。

    非重试的失败抛 :class:`LLMError`（网络错误耗尽重试后）；状态码由调用方检查——
    上下文超限等协议级判定需要响应正文，transport 不越权。
    """
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
    """发一个流式请求，返回**已建立**（状态码已判定）的流式响应。

    重试窗口只覆盖请求发送与响应头阶段；返回后调用方负责 close（``with response:``）。
    响应体阶段的网络错误不在重试范围内——那已经把 delta 回调出去了。
    """
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
        return response
