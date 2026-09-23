from __future__ import annotations

import hashlib
import ipaddress
import socket
from urllib.parse import urlsplit


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
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise DiscoveryError(f"hostname did not resolve: {host}") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise DiscoveryError(f"hostname resolves to a non-public address: {host}")


def evidence_id(
    source_type: str, source_url: str | None, excerpt: str, discriminator: str = ""
) -> str:
    value = "\n".join((source_type, source_url or "", excerpt, discriminator))
    return "evidence_" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]
