"""Client IP extraction.

`trusted_proxy_hops` is how many reverse proxies append to X-Forwarded-For.
Cloud Run's frontend appends one address, so production uses 1 and the
rightmost valid IP is the client. Zero hops ignores the header and uses the
socket peer, which prevents a caller from rotating a spoofed header.

Limits that key off this IP are enforced inside each instance only.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request


def _parse_ip(value: str) -> str | None:
    candidate = value.strip().strip('"').strip("'")
    if not candidate or candidate.lower() == "unknown":
        return None
    if candidate.startswith("[") and "]" in candidate:
        candidate = candidate[1 : candidate.index("]")]
    elif candidate.count(":") == 1 and "." in candidate:
        # IPv4 with a port. Bare IPv6 has more colons and is parsed below.
        candidate = candidate.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    peer = request.client.host if request.client else ""
    peer_ip = _parse_ip(peer) or "unknown"
    hops = max(0, int(trusted_proxy_hops))
    if hops == 0:
        return peer_ip

    header = request.headers.get("x-forwarded-for", "")
    chain = [ip for part in header.split(",") if (ip := _parse_ip(part))]
    if len(chain) < hops:
        return peer_ip
    return chain[-hops]
