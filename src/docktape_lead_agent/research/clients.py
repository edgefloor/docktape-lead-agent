from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Self
from urllib.parse import urlsplit

import httpx
import tldextract

from ..domain.evidence import CertificateContext, EvidenceRecord
from ..domain.submissions import normalize_hostname
from .http import request_with_retry
from .urls import DiscoveryError, assert_public_url, evidence_id, redact_error

PAGE_TEXT_LIMIT = 12_000
SNIPPET_LIMIT = 1_000
CERT_BODY_LIMIT = 250_000
INITIAL_PAGE_LIMIT = 4
FOLLOW_UP_PAGE_LIMIT = 1
SEARCH_RESULT_LIMIT = 5
_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


class DiscoveryClient:
    def __init__(
        self,
        *,
        searxng_base_url: str,
        firecrawl_base_url: str,
        firecrawl_api_key: str | None,
        client: httpx.Client | None = None,
        on_attempt: Callable[[str, int, str, float], None] | None = None,
    ) -> None:
        timeout = httpx.Timeout(connect=10, read=30, write=10, pool=10)
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None
        self.searxng_base_url = searxng_base_url.rstrip("/")
        self.firecrawl_base_url = firecrawl_base_url.rstrip("/")
        self.firecrawl_api_key = firecrawl_api_key
        self.on_attempt = on_attempt

    def _observer(self, provider: str) -> Callable[[int, str, float], None] | None:
        if self.on_attempt is None:
            return None
        return lambda attempt, outcome, elapsed: self.on_attempt(
            provider, attempt, outcome, elapsed
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search(self, query: str) -> list[dict[str, Any]]:
        endpoint = f"{self.searxng_base_url}/search"
        response, body, _ = request_with_retry(
            self.client,
            "GET",
            endpoint,
            params={"q": query, "format": "json"},
            on_attempt=self._observer("searxng"),
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DiscoveryError(
                f"SearXNG returned invalid JSON with HTTP {response.status_code}"
            ) from exc
        results = payload.get("results")
        if not isinstance(results, list):
            raise DiscoveryError("SearXNG response has no results list")
        return [result for result in results[:SEARCH_RESULT_LIMIT] if isinstance(result, dict)]

    def scrape(
        self,
        url: str,
        *,
        submitted_host: str | None = None,
        synthetic: bool = False,
    ) -> tuple[EvidenceRecord, list[str]]:
        assert_public_url(url)
        endpoint = f"{self.firecrawl_base_url}/v1/scrape"
        headers = (
            {"Authorization": f"Bearer {self.firecrawl_api_key}"} if self.firecrawl_api_key else {}
        )
        response, body, response_truncated = request_with_retry(
            self.client,
            "POST",
            endpoint,
            headers=headers,
            json={"url": url, "formats": ["markdown", "links"]},
            on_attempt=self._observer("firecrawl"),
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DiscoveryError(
                f"Firecrawl returned invalid JSON with HTTP {response.status_code}"
            ) from exc
        data = payload.get("data", payload)
        if not isinstance(data, dict):
            raise DiscoveryError("Firecrawl response has no data object")
        markdown = data.get("markdown", "")
        if not isinstance(markdown, str):
            raise DiscoveryError("Firecrawl response has no Markdown text")
        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        redirect_urls = [
            candidate
            for candidate in (
                metadata.get("sourceURL"),
                metadata.get("url"),
                metadata.get("canonicalUrl"),
                data.get("url"),
            )
            if isinstance(candidate, str)
        ]
        for redirect_url in redirect_urls:
            assert_public_url(redirect_url)
        canonical_url = redirect_urls[0] if redirect_urls else url
        if not isinstance(canonical_url, str):
            canonical_url = url
        assert_public_url(canonical_url)
        excerpt = markdown[:PAGE_TEXT_LIMIT]
        truncated = response_truncated or len(markdown) > PAGE_TEXT_LIMIT
        links = data.get("links", [])
        safe_links = (
            [link for link in links if isinstance(link, str)] if isinstance(links, list) else []
        )
        canonical_host = normalize_hostname(urlsplit(canonical_url).hostname or "")
        submitted_domain = bool(
            submitted_host
            and (canonical_host == submitted_host or canonical_host.endswith(f".{submitted_host}"))
        )
        return (
            EvidenceRecord(
                id=evidence_id("page", canonical_url, excerpt),
                source_type="page",
                source_url=canonical_url,
                excerpt=excerpt,
                company_association="submitted_domain" if submitted_domain else "unverified",
                truncated=truncated,
                synthetic=synthetic,
            ),
            safe_links,
        )

    def certificate_context(self, hostname: str) -> CertificateContext:
        parsed = _TLD_EXTRACT(hostname)
        apex = parsed.top_domain_under_public_suffix or hostname
        context = CertificateContext(apex=apex)
        try:
            _, body, truncated = request_with_retry(
                self.client,
                "GET",
                "https://crt.name/v1/search",
                params={"apex": apex},
                headers={"Accept": "text/plain"},
                max_bytes=CERT_BODY_LIMIT,
                on_attempt=self._observer("certificate"),
            )
        except (httpx.HTTPError, DiscoveryError) as exc:
            context.unavailable = True
            context.warning = redact_error(exc)
            return context
        context.response_truncated = truncated
        seen: set[str] = set()
        for raw in body.decode("utf-8", errors="replace").splitlines():
            name = raw.strip().casefold().rstrip(".")
            if not name or " " in name or "." not in name:
                context.malformed_count += 1
                continue
            if name.startswith("*."):
                context.wildcard_count += 1
                name = name[2:]
            if name != apex and not name.endswith(f".{apex}"):
                context.out_of_scope_count += 1
                continue
            if name in seen:
                context.duplicate_count += 1
                continue
            seen.add(name)
            if len(context.hostnames) >= 100:
                context.omitted_count += 1
                continue
            context.hostnames.append(name)
        return context
