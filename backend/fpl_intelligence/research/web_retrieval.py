"""Safe, deterministic web-page retrieval for research links."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit

from lxml import html


@dataclass(frozen=True)
class RetrievedPage:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str | None
    title: str | None
    published_at_raw: str | None
    text: str
    content_hash: str
    retrieved_at: datetime
    fetch_mode: str


class PageRetrievalError(ValueError):
    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


class StaticPageClient(Protocol):
    async def get(self, url: str, **kwargs: Any) -> Any: ...


class DynamicPageClient(Protocol):
    def fetch(self, url: str, **kwargs: Any) -> Any: ...


class ScraplingStaticClient:
    async def get(self, url: str, **kwargs: Any) -> Any:
        from scrapling.fetchers import AsyncFetcher
        return await AsyncFetcher.get(url, **kwargs)


class ScraplingDynamicClient:
    def fetch(self, url: str, **kwargs: Any) -> Any:
        from scrapling.fetchers import DynamicFetcher
        return DynamicFetcher.fetch(url, **kwargs)


class ScraplingPageRetriever:
    def __init__(self, *, static_client: StaticPageClient | None = None, dynamic_client: DynamicPageClient | None = None,
                 timeout_seconds: float = 20.0, dynamic_fallback: bool = False, minimum_text_length: int = 120):
        self.static_client = static_client or ScraplingStaticClient()
        self.dynamic_client = dynamic_client
        self.timeout_seconds = timeout_seconds
        self.dynamic_fallback = dynamic_fallback
        self.minimum_text_length = minimum_text_length

    def retrieve(self, url: str) -> str:
        return self.retrieve_page(url).text

    def retrieve_page(self, url: str) -> RetrievedPage:
        _validate_url(url)
        try:
            response = asyncio.run(self.static_client.get(url, follow_redirects="safe", timeout=self.timeout_seconds))
        except PageRetrievalError:
            raise
        except TimeoutError as exc:
            raise PageRetrievalError("timeout") from exc
        except Exception as exc:
            raise PageRetrievalError("retrieval_failed") from exc
        status = int(getattr(response, "status", getattr(response, "status_code", 0)) or 0)
        if status in {401, 403, 429}:
            raise PageRetrievalError("blocked")
        if status >= 400:
            raise PageRetrievalError("http_error")
        try:
            page = _to_page(url, response, "static", self.minimum_text_length)
        except PageRetrievalError as exc:
            if not (self.dynamic_fallback and exc.reason == "empty_content" and self.dynamic_client is not None):
                raise
            try:
                dynamic = self.dynamic_client.fetch(url, timeout=self.timeout_seconds)
                return _to_page(url, dynamic, "dynamic", self.minimum_text_length)
            except PageRetrievalError:
                raise
            except Exception as dynamic_exc:
                raise PageRetrievalError("dynamic_unavailable") from dynamic_exc
        return page


def _validate_url(value: str) -> None:
    parsed = urlsplit(value)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host:
        raise PageRetrievalError("invalid_url")
    lowered = host.lower().rstrip(".")
    if lowered == "localhost" or lowered.endswith(".localhost") or lowered in {"localhost.localdomain", "ip6-localhost"}:
        raise PageRetrievalError("invalid_url")
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return
    if not address.is_global:
        raise PageRetrievalError("invalid_url")


def _response_text(response: Any) -> str:
    value = getattr(response, "text", "")
    return value() if callable(value) else str(value or "")


def _response_headers(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", {}) or {}
    return {str(key).lower(): str(value) for key, value in dict(headers).items()}


def _to_page(requested_url: str, response: Any, mode: str, minimum: int) -> RetrievedPage:
    headers = _response_headers(response)
    content_type = headers.get("content-type")
    if content_type and not any(value in content_type.lower() for value in ("text/html", "application/xhtml+xml")):
        raise PageRetrievalError("unsupported_content")
    raw = _response_text(response)
    if not raw.strip():
        raise PageRetrievalError("empty_content")
    try:
        document = html.fromstring(raw)
    except (ValueError, TypeError) as exc:
        raise PageRetrievalError("unsupported_content") from exc
    for node in document.xpath("//script|//style|//nav|//footer|//header|//form|//aside|//noscript|//svg"):
        node.drop_tree()
    title = _first(document, "//meta[@property='og:title']/@content") or _first(document, "//title/text()") or _first(document, "//h1//text()")
    published = _first(document, "//meta[@property='article:published_time']/@content") or _first(document, "//time[@datetime]/@datetime")
    canonical = _first(document, "//link[contains(concat(' ', normalize-space(@rel), ' '), ' canonical ')]/@href")
    roots = document.xpath("//article|//main|//*[@role='main']") or [document.find("body") or document]
    text = _normalise("\n".join(" ".join(root.xpath(".//text()")) for root in roots))
    if len(text) < minimum:
        raise PageRetrievalError("empty_content")
    final_url = str(getattr(response, "url", "") or requested_url)
    return RetrievedPage(requested_url, canonical or final_url, int(getattr(response, "status", getattr(response, "status_code", 200)) or 200), content_type, _normalise(title or "") or None, published, text, hashlib.sha256(text.encode()).hexdigest(), datetime.now(timezone.utc), mode)


def _first(document, xpath: str) -> str | None:
    values = document.xpath(xpath)
    return str(values[0]).strip() if values else None


def _normalise(value: str) -> str:
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if line.split())
