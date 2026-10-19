from types import SimpleNamespace

from app.client_ip import client_ip


def _request(host: str, forwarded: str | None = None):
    headers = {}
    if forwarded is not None:
        headers["x-forwarded-for"] = forwarded
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)


def test_zero_hops_ignores_forwarded_header():
    request = _request("127.0.0.1", "203.0.113.5, 198.51.100.9")
    assert client_ip(request, 0) == "127.0.0.1"


def test_one_hop_uses_rightmost_valid_ip():
    request = _request("35.0.0.1", "203.0.113.5, not-an-ip, 198.51.100.9")
    assert client_ip(request, 1) == "198.51.100.9"


def test_spoofed_leftmost_ip_is_not_the_client_when_proxy_appends():
    request = _request("35.0.0.1", "1.2.3.4, 203.0.113.8")
    assert client_ip(request, 1) == "203.0.113.8"


def test_missing_or_invalid_chain_falls_back_to_peer():
    assert client_ip(_request("10.0.0.8", "nope"), 1) == "10.0.0.8"
    assert client_ip(_request("10.0.0.8", None), 2) == "10.0.0.8"


def test_two_hops_skips_the_appended_proxy():
    request = _request("35.0.0.1", "203.0.113.5, 10.1.1.1, 35.191.0.2")
    assert client_ip(request, 2) == "10.1.1.1"
