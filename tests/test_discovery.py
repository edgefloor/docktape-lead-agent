from __future__ import annotations

import httpx
import pytest

from discovery import (
    DiscoveryClient,
    DiscoveryError,
    assert_public_url,
    collect_follow_up,
    request_with_retry,
    search_evidence,
)
from models import CertificateContext, EvidenceRecord, ResearchAttempt


def test_request_retries_once_and_bounds_body() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(200, content=b"abcdefghij", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        _, body, truncated = request_with_retry(client, "GET", "https://example.com", max_bytes=5)
    assert calls == 2
    assert body == b"abcde"
    assert truncated is True


def test_search_records_are_limited_and_truncated() -> None:
    results = [
        {"url": f"https://example.com/{index}", "title": str(index), "content": "x" * 1200}
        for index in range(8)
    ]
    records = search_evidence("query", results)
    assert len(records) == 5
    assert all(len(record.excerpt) == 1000 and record.truncated for record in records)


def test_public_url_check_resolves_exact_hostname(monkeypatch) -> None:
    resolved = []

    def fake_getaddrinfo(host, port, type):
        resolved.append(host)
        return [(None, None, None, None, ("127.0.0.1", port))]

    monkeypatch.setattr("discovery.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(DiscoveryError, match="non-public"):
        assert_public_url("https://www.example.com")
    assert resolved == ["www.example.com"]


def test_follow_up_can_fetch_url_previously_seen_only_as_snippet(lead) -> None:
    snippet_url = "https://93.184.216.34/about"
    other_url = "https://93.184.216.34/contact"
    existing = ResearchAttempt(
        evidence=[
            EvidenceRecord(
                id="snippet",
                source_type="search_snippet",
                source_url=snippet_url,
                excerpt="About Example Cloud",
                company_association="unverified",
            )
        ],
        certificate_context=CertificateContext(apex="example.com"),
        page_requests=[lead.website],
    )

    class Client:
        scraped: list[str] = []

        def search(self, query: str) -> list[dict[str, str]]:
            return [
                {"url": snippet_url, "title": "About", "content": "About Example Cloud"},
                {"url": other_url, "title": "Contact", "content": "Contact Example Cloud"},
            ]

        def scrape(self, url: str, *, submitted_host: str):
            self.scraped.append(url)
            return (
                EvidenceRecord(
                    id="page",
                    source_type="page",
                    source_url=url,
                    excerpt="Fetched about page",
                    company_association="unverified",
                ),
                [],
            )

    client = Client()
    result = collect_follow_up(lead, client, "jurisdiction", existing)

    assert client.scraped == [snippet_url]
    assert result.page_requests[-1] == snippet_url


def test_firecrawl_request_omits_authorization_without_api_key() -> None:
    target = "https://93.184.216.34/about"

    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(
            200,
            json={"data": {"markdown": "About Example", "links": [], "metadata": {"sourceURL": target}}},
            request=request,
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = DiscoveryClient(
            searxng_base_url="https://search.example.test",
            firecrawl_base_url="https://firecrawl.example.test",
            firecrawl_api_key=None,
            client=http,
        )
        record, _ = client.scrape(target)

    assert record.excerpt == "About Example"
