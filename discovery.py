from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Self
from urllib.parse import urljoin, urlsplit

import httpx
import tldextract

from models import (
    CertificateContext,
    EvidenceRecord,
    LeadSubmission,
    ResearchAttempt,
    normalize_hostname,
    utc_now,
)

PAGE_TEXT_LIMIT = 12_000
SNIPPET_LIMIT = 1_000
HTTP_BODY_LIMIT = 2_000_000
CERT_BODY_LIMIT = 250_000
INITIAL_PAGE_LIMIT = 4
FOLLOW_UP_PAGE_LIMIT = 1
SEARCH_RESULT_LIMIT = 5
RETRY_DELAY_LIMIT = 3.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}
PREFERRED_PATH_WORDS = ("about", "careers", "engineering", "product", "infrastructure")
_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())


class DiscoveryError(RuntimeError):
    pass


def redact_error(exc: BaseException) -> str:
    text = str(exc)
    for marker in ("Authorization", "Bearer ", "api_key", "apiKey"):
        if marker.casefold() in text.casefold():
            return f"{type(exc).__name__}: request failed; credentials redacted"
    return f"{type(exc).__name__}: {text[:500]}"


def assert_public_url(url: str, *, resolve: bool = True) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DiscoveryError(f"URL is not public HTTP or HTTPS: {url!r}")
    if parsed.username is not None or parsed.password is not None:
        raise DiscoveryError("URL credentials are not allowed")
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith(".localhost"):
        raise DiscoveryError("local URLs are not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise DiscoveryError("private or local URLs are not allowed")
    if not resolve or literal is not None:
        return
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise DiscoveryError(f"hostname did not resolve: {host}") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise DiscoveryError(f"hostname resolves to a non-public address: {host}")


def evidence_id(source_type: str, source_url: str | None, excerpt: str, discriminator: str = "") -> str:
    value = "\n".join((source_type, source_url or "", excerpt, discriminator))
    return "evidence_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


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
    **kwargs: Any,
) -> tuple[httpx.Response, bytes, bool]:
    last_error: BaseException | None = None
    for attempt in range(2):
        try:
            with client.stream(method, url, **kwargs) as response:
                if response.status_code in RETRYABLE_STATUS and attempt == 0:
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
                return response, b"".join(chunks), truncated
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise
    assert last_error is not None
    raise last_error


class DiscoveryClient:
    def __init__(
        self,
        *,
        searxng_base_url: str,
        firecrawl_base_url: str,
        firecrawl_api_key: str | None,
        client: httpx.Client | None = None,
    ) -> None:
        timeout = httpx.Timeout(connect=10, read=30, write=10, pool=10)
        self.client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None
        self.searxng_base_url = searxng_base_url.rstrip("/")
        self.firecrawl_base_url = firecrawl_base_url.rstrip("/")
        self.firecrawl_api_key = firecrawl_api_key

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
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DiscoveryError(f"SearXNG returned invalid JSON with HTTP {response.status_code}") from exc
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
        headers = {"Authorization": f"Bearer {self.firecrawl_api_key}"} if self.firecrawl_api_key else {}
        response, body, response_truncated = request_with_retry(
            self.client,
            "POST",
            endpoint,
            headers=headers,
            json={"url": url, "formats": ["markdown", "links"]},
        )
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise DiscoveryError(f"Firecrawl returned invalid JSON with HTTP {response.status_code}") from exc
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
        safe_links = [link for link in links if isinstance(link, str)] if isinstance(links, list) else []
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


def submitted_evidence(lead: LeadSubmission, *, synthetic: bool = False) -> list[EvidenceRecord]:
    values = {
        "name": lead.name,
        "email": lead.email,
        "company_name": lead.company_name,
        "website": lead.website,
        "employee_count_band": lead.employee_count_band.value,
    }
    now = utc_now()
    return [
        EvidenceRecord(
            id=evidence_id("submitted_field", None, value, field),
            source_type="submitted_field",
            excerpt=f"{field}: {value}",
            retrieved_at=now,
            company_association="submitted",
            synthetic=synthetic,
        )
        for field, value in values.items()
    ]


def search_evidence(query: str, results: list[dict[str, Any]], *, synthetic: bool = False) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for position, result in enumerate(results[:SEARCH_RESULT_LIMIT], start=1):
        url = result.get("url")
        title = result.get("title", "")
        snippet = result.get("content", result.get("snippet", ""))
        if not isinstance(url, str) or not isinstance(snippet, str) or not isinstance(title, str):
            continue
        try:
            assert_public_url(url)
        except DiscoveryError:
            continue
        excerpt = snippet[:SNIPPET_LIMIT]
        records.append(
            EvidenceRecord(
                id=evidence_id("search_snippet", url, excerpt, f"{query}:{position}"),
                source_type="search_snippet",
                source_url=url,
                excerpt=excerpt,
                company_association="unverified",
                truncated=len(snippet) > SNIPPET_LIMIT,
                synthetic=synthetic,
                search_query=query,
                result_title=title,
                result_position=position,
            )
        )
    return records


def _rank_candidate(url: str, submitted_host: str) -> tuple[int, int, str]:
    parsed = urlsplit(url)
    host = normalize_hostname(parsed.hostname or "")
    same_site = host == submitted_host or host.endswith(f".{submitted_host}")
    path = parsed.path.casefold()
    preferred = any(word in path for word in PREFERRED_PATH_WORDS)
    return (0 if same_site else 1, 0 if preferred else 1, url)


def collect_initial(lead: LeadSubmission, client: DiscoveryClient) -> ResearchAttempt:
    evidence = submitted_evidence(lead)
    queries = [
        f"{lead.company_name} {lead.hostname} official company",
        f"{lead.company_name} {lead.hostname} employee count legal entity jurisdiction headquarters",
        f"{lead.company_name} {lead.hostname} cloud infrastructure engineering",
    ]
    warnings: list[str] = []
    search_records: list[EvidenceRecord] = []
    for query in queries:
        try:
            search_records.extend(search_evidence(query, client.search(query)))
        except (httpx.HTTPError, DiscoveryError) as exc:
            warnings.append(f"Search failed for {query!r}: {redact_error(exc)}")
    evidence.extend(search_records)

    requested: list[str] = []
    candidate_urls: list[str] = [lead.website]
    homepage_links: list[str] = []
    try:
        record, homepage_links = client.scrape(lead.website, submitted_host=lead.hostname)
        requested.append(lead.website)
        evidence.append(record)
    except (httpx.HTTPError, DiscoveryError) as exc:
        requested.append(lead.website)
        warnings.append(f"Homepage fetch failed: {redact_error(exc)}")

    candidate_urls.extend(urljoin(lead.website, link) for link in homepage_links)
    candidate_urls.extend(record.source_url for record in search_records if record.source_url)
    distinct: list[str] = []
    seen = {lead.website.rstrip("/")}
    for url in sorted(candidate_urls, key=lambda item: _rank_candidate(item, lead.hostname)):
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        try:
            assert_public_url(url)
        except DiscoveryError:
            continue
        distinct.append(url)
    for url in distinct[: max(0, INITIAL_PAGE_LIMIT - 1)]:
        requested.append(url)
        try:
            record, _ = client.scrape(url, submitted_host=lead.hostname)
            evidence.append(record)
        except (httpx.HTTPError, DiscoveryError) as exc:
            warnings.append(f"Page fetch failed for {url}: {redact_error(exc)}")

    context = client.certificate_context(lead.hostname)
    if context.warning:
        warnings.append(f"Certificate context unavailable: {context.warning}")
    return ResearchAttempt(
        evidence=evidence,
        certificate_context=context,
        queries=queries,
        page_requests=requested[:INITIAL_PAGE_LIMIT],
        warnings=warnings,
    )


def collect_follow_up(
    lead: LeadSubmission,
    client: DiscoveryClient,
    target: str,
    existing: ResearchAttempt,
) -> ResearchAttempt:
    if target == "jurisdiction":
        query = (
            f"{lead.company_name} {lead.hostname} legal entity incorporation registered office "
            "contracting entity official"
        )
    else:
        query = f'"{lead.company_name}" "{target}" {lead.hostname} company identity'
    warnings: list[str] = []
    new_records: list[EvidenceRecord] = []
    requested: list[str] = []
    try:
        results = client.search(query)
        snippets = search_evidence(query, results)
        new_records.extend(snippets)
        requested_urls = {url.rstrip("/") for url in existing.page_requests}
        requested_urls.update(
            item.source_url.rstrip("/")
            for item in existing.evidence
            if item.source_type == "page" and item.source_url
        )
        candidate = next(
            (
                item.source_url
                for item in snippets
                if item.source_url and item.source_url.rstrip("/") not in requested_urls
            ),
            None,
        )
        if candidate:
            requested.append(candidate)
            record, _ = client.scrape(candidate, submitted_host=lead.hostname)
            new_records.append(record)
    except (httpx.HTTPError, DiscoveryError) as exc:
        warnings.append(f"Compliance follow-up failed: {redact_error(exc)}")
    return ResearchAttempt(
        evidence=[*existing.evidence, *new_records],
        certificate_context=existing.certificate_context,
        queries=[*existing.queries, query],
        page_requests=[*existing.page_requests, *requested[:FOLLOW_UP_PAGE_LIMIT]],
        warnings=[*existing.warnings, *warnings],
        synthetic=existing.synthetic,
        follow_up_failed=bool(warnings),
    )


def load_evidence_fixture(path: Path, lead: LeadSubmission) -> ResearchAttempt:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("synthetic") is not True:
        raise DiscoveryError("evidence fixture must be an object with synthetic set to true")
    raw_records = payload.get("evidence", [])
    if not isinstance(raw_records, list):
        raise DiscoveryError("evidence fixture must contain an evidence list")
    records = submitted_evidence(lead, synthetic=True)
    for index, item in enumerate(raw_records):
        if not isinstance(item, dict):
            raise DiscoveryError(f"evidence fixture item {index} is not an object")
        item = {**item, "synthetic": True}
        if not item.get("id"):
            item["id"] = evidence_id(
                str(item.get("source_type", "page")),
                item.get("source_url"),
                str(item.get("excerpt", "")),
                str(index),
            )
        records.append(EvidenceRecord.model_validate(item))
    parsed = _TLD_EXTRACT(lead.hostname)
    apex = parsed.top_domain_under_public_suffix or lead.hostname
    context = CertificateContext(apex=apex, hostnames=list(payload.get("certificate_hostnames", [])))
    return ResearchAttempt(evidence=records, certificate_context=context, synthetic=True)
