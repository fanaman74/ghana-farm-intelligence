"""Provider plumbing: HTTP with backoff, SQLite-backed caching, provenance types.

Rules enforced here
-------------------
* Every provider call has a timeout, retry budget and a cache TTL.
* A failed refresh falls back to the last successful payload, marked ``stale``
  with its original retrieval date. Failure never produces an invented value.
* Every section carries explicit source, licence, resolution and limitations.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from ..config import settings
from ..db import cache_get, cache_put, record_provider_health
from ..utils import iso_now, json_safe, stable_hash

log = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_STALE = "stale"
STATUS_UNAVAILABLE = "unavailable"
STATUS_ERROR = "error"
STATUS_DEMO = "demo"

_CLIENT: httpx.Client | None = None


class ProviderError(Exception):
    """Provider failure with a machine-readable code."""

    def __init__(self, provider: str, code: str, message: str, *, retryable: bool = False,
                 status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "status_code": self.status_code,
        }


def client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None or _CLIENT.is_closed:
        _CLIENT = httpx.Client(
            timeout=httpx.Timeout(settings.provider_timeout_s, connect=15.0),
            headers={"User-Agent": settings.user_agent, "Accept-Encoding": "gzip, deflate"},
            follow_redirects=True,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _CLIENT


def close_client() -> None:
    global _CLIENT
    if _CLIENT is not None and not _CLIENT.is_closed:
        _CLIENT.close()
    _CLIENT = None


def request(
    provider: str,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    expect_json: bool = True,
    retries: int | None = None,
) -> Any:
    """HTTP request with exponential backoff, mapped failures, and timing."""
    attempts = (settings.provider_retries if retries is None else retries) + 1
    started = time.perf_counter()
    last_error: ProviderError | None = None
    for attempt in range(attempts):
        try:
            response = client().request(
                method, url, params=params, json=json_body, headers=headers
            )
            if response.status_code in (429, 500, 502, 503, 504):
                last_error = ProviderError(
                    provider,
                    "upstream_unavailable" if response.status_code in (502, 503, 504) else "upstream_error",
                    f"{provider} returned HTTP {response.status_code}",
                    retryable=True,
                    status_code=response.status_code,
                )
            elif response.status_code >= 400:
                raise ProviderError(
                    provider,
                    "http_error",
                    f"{provider} returned HTTP {response.status_code}: {response.text[:180]}",
                    retryable=False,
                    status_code=response.status_code,
                )
            else:
                latency = int((time.perf_counter() - started) * 1000)
                record_provider_health(provider, STATUS_OK, latency)
                payload = response.json() if expect_json else response.content
                return payload
        except httpx.TimeoutException as exc:
            last_error = ProviderError(provider, "timeout", f"{provider} timed out", retryable=True)
            last_error.__cause__ = exc
        except httpx.HTTPError as exc:
            last_error = ProviderError(provider, "network_error", f"{provider} network error: {exc}", retryable=True)
            last_error.__cause__ = exc
        except ValueError as exc:
            raise ProviderError(provider, "invalid_json", f"{provider} returned invalid JSON") from exc

        if attempt < attempts - 1:
            time.sleep(min(2**attempt, 6) * 0.5)

    assert last_error is not None
    latency = int((time.perf_counter() - started) * 1000)
    record_provider_health(provider, STATUS_UNAVAILABLE, latency, last_error.message)
    raise last_error


@dataclass
class Source:
    provider_id: str
    dataset: str
    url: str
    licence: str
    resolution: str | None = None
    period: str | None = None
    method: str | None = None
    limitations: str | None = None
    access_method: str | None = None
    key_required: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "dataset": self.dataset,
            "url": self.url,
            "licence": self.licence,
            "resolution": self.resolution,
            "period": self.period,
            "method": self.method,
            "limitations": self.limitations,
            "access_method": self.access_method,
            "key_required": self.key_required,
        }


@dataclass
class Section:
    key: str
    status: str
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    retrieved_at: str = field(default_factory=iso_now)
    message: str | None = None
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: int | None = None

    @property
    def ok(self) -> bool:
        return self.status in (STATUS_OK, STATUS_STALE, STATUS_DEMO)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "data": json_safe(self.data),
            "sources": [s.as_dict() for s in self.sources],
            "retrieved_at": self.retrieved_at,
            "message": self.message,
            "warnings": self.warnings,
            "elapsed_ms": self.elapsed_ms,
        }


def unavailable(key: str, sources: list[Source], message: str, *, code: str | None = None) -> Section:
    return Section(
        key=key,
        status=STATUS_UNAVAILABLE,
        data={"reason": code or "unavailable"},
        sources=sources,
        message=message,
    )


def failure(key: str, sources: list[Source], error: Exception) -> Section:
    if isinstance(error, ProviderError):
        return Section(
            key=key,
            status=STATUS_ERROR,
            data={"error": error.as_dict()},
            sources=sources,
            message=f"{error.message}. No value is shown rather than an estimate.",
        )
    return Section(
        key=key,
        status=STATUS_ERROR,
        data={"error": {"code": "unexpected", "message": str(error)[:300]}},
        sources=sources,
        message="This dataset could not be retrieved. No value is shown rather than an estimate.",
    )


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #
def cached(
    key_parts: list[Any],
    provider: str,
    ttl_seconds: int,
    fetcher: Callable[[], Any],
) -> tuple[Any, dict[str, Any]]:
    """Return (payload, meta) using the SQLite cache with stale fallback.

    meta keys: cache_key, cached, stale, retrieved_at, expired_at
    """
    cache_key = f"{provider}:{stable_hash(key_parts)}"
    entry = cache_get(cache_key)
    now = time.time()
    if entry and not entry["expired"]:
        return entry["payload"], {
            "cache_key": cache_key,
            "cached": True,
            "stale": False,
            "retrieved_at": entry["created_at"],
            "expires_at": entry["expires_at"],
        }
    try:
        payload = fetcher()
    except Exception:
        if entry is not None:
            log.warning("%s refresh failed; serving stale cache from %s", provider, entry["created_at"])
            return entry["payload"], {
                "cache_key": cache_key,
                "cached": True,
                "stale": True,
                "retrieved_at": entry["created_at"],
                "expires_at": entry["expires_at"],
                "error": "refresh_failed",
            }
        raise
    cache_put(cache_key, provider, payload, ttl_seconds)
    return payload, {
        "cache_key": cache_key,
        "cached": False,
        "stale": False,
        "retrieved_at": iso_now(),
        "expires_at": None,
    }


def probe(provider: str, url: str, *, params: dict[str, Any] | None = None,
          timeout: float = 20.0) -> dict[str, Any]:
    """Lightweight availability check used by /api/catalog/providers/status."""
    started = time.perf_counter()
    try:
        response = client().get(url, params=params, timeout=timeout)
        latency = int((time.perf_counter() - started) * 1000)
        ok = response.status_code < 400
        record_provider_health(
            provider,
            STATUS_OK if ok else STATUS_UNAVAILABLE,
            latency,
            None if ok else f"HTTP {response.status_code}",
        )
        return {"provider": provider, "reachable": ok, "status_code": response.status_code, "latency_ms": latency}
    except httpx.HTTPError as exc:
        latency = int((time.perf_counter() - started) * 1000)
        record_provider_health(provider, STATUS_UNAVAILABLE, latency, str(exc)[:200])
        return {"provider": provider, "reachable": False, "status_code": None, "latency_ms": latency, "error": str(exc)[:200]}
