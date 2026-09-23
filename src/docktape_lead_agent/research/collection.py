from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ..domain.evidence import CertificateContext, EvidenceRecord, ResearchAttempt, utc_now
from ..domain.submissions import LeadSubmission, normalize_hostname
from .clients import (
    _TLD_EXTRACT,
    FOLLOW_UP_PAGE_LIMIT,
    INITIAL_PAGE_LIMIT,
    SEARCH_RESULT_LIMIT,
    SNIPPET_LIMIT,
    DiscoveryClient,
)
from .urls import DiscoveryError, assert_public_url, evidence_id, redact_error


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


def search_evidence(
    query: str, results: list[dict[str, Any]], *, synthetic: bool = False
) -> list[EvidenceRecord]:
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
    if path.rstrip("/") in {"/about", "/about-us", "/company"}:
        priority = 0
    elif "terms-of-service" in path or path.rstrip("/").endswith("/terms"):
        priority = 1
    elif any(word in path for word in ("infrastructure", "engineering", "cloud")):
        priority = 2
    elif any(
        word in path for word in ("legal", "terms", "imprint", "about", "company", "headquarters")
    ):
        priority = 3
    elif "careers" in path:
        priority = 4
    else:
        priority = 5
    return (0 if same_site else 1, priority, url)


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
    context = CertificateContext(
        apex=apex, hostnames=list(payload.get("certificate_hostnames", []))
    )
    return ResearchAttempt(evidence=records, certificate_context=context, synthetic=True)
