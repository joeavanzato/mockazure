import socket

import pytest


def test_native_guard_allows_byte_encoded_localhost():
    addresses = socket.getaddrinfo(b"localhost", 443)
    assert addresses
    assert all(item[4][0] in ("127.0.0.1", "::1") for item in addresses)


def test_native_guard_blocks_external_dns_before_resolution():
    with pytest.raises(PermissionError, match="nonlocal"):
        socket.getaddrinfo("example.invalid", 443)
