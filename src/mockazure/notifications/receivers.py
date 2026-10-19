"""Exact registered HTTPS callbacks connect only to pinned local addresses."""

import asyncio
import hashlib
import ipaddress
import json
import secrets
import ssl
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from mockazure.providers.graph.errors import GraphError

NETWORKS = tuple(
    ipaddress.ip_network(v)
    for v in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "::1/128", "fc00::/7")
)


def resolve_receiver(url: str, configured_receivers: dict) -> dict:
    try:
        if not isinstance(url, str) or len(url) > 2048 or url not in configured_receivers:
            raise ValueError()
        parsed = urlsplit(url)
        config = configured_receivers[url]
        address = ipaddress.ip_address(config["address"])
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.fragment
            or not parsed.port
            or parsed.port < 1
        ):
            # Default HTTPS port443 is valid even when omitted.
            if not (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.fragment
                and parsed.port is None
            ):
                raise ValueError()
        if (
            not any(address in network for network in NETWORKS)
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError()
        if any(k == "validationToken" for k, _ in parse_qsl(parsed.query)):
            raise ValueError()
        if not isinstance(config.get("ca_file"), str) or not config["ca_file"]:
            raise ValueError()
        hostname = parsed.hostname.encode("idna").decode("ascii")
        if any(c.isspace() for c in hostname) or any(c in hostname for c in "/\\%\0"):
            raise ValueError()
        binding = {
            "url": url,
            "address": str(address),
            "hostname": hostname,
            "port": parsed.port or 443,
            "ca_file": config["ca_file"],
        }
        binding["fingerprint"] = hashlib.sha256(
            json.dumps(binding, sort_keys=True).encode()
        ).hexdigest()
        return binding
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise GraphError(
            400, "InvalidNotificationUrl", "Use an explicitly registered local HTTPS receiver"
        ) from None


async def post_receiver(binding, *, payload=None, validation_token=None, timeout=5.0):
    # httpx timeouts apply to individual reads. A slow streaming response must
    # also respect the whole operation's bound and the caller's remaining lease.
    async with asyncio.timeout(timeout):
        return await _post_receiver(
            binding, payload=payload, validation_token=validation_token, timeout=timeout
        )


async def _post_receiver(binding, *, payload=None, validation_token=None, timeout=5.0):
    parsed = urlsplit(binding["url"])
    address = binding["address"]
    netloc = ("[" + address + "]" if ":" in address else address) + ":" + str(binding["port"])
    query = parsed.query
    if validation_token is not None:
        query = urlencode(
            [*parse_qsl(query, keep_blank_values=True), ("validationToken", validation_token)]
        )
    connect_url = urlunsplit(("https", netloc, parsed.path or "/", query, ""))
    context = ssl.create_default_context(cafile=binding["ca_file"])
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    hostname = binding["hostname"]
    host = ("[" + hostname + "]" if ":" in hostname else hostname) + (
        ":" + str(binding["port"]) if binding["port"] != 443 else ""
    )
    async with httpx.AsyncClient(
        verify=context, trust_env=False, follow_redirects=False, timeout=timeout
    ) as client:
        async with client.stream(
            "POST",
            connect_url,
            headers={"Host": host},
            json=payload if validation_token is None else None,
            extensions={"sni_hostname": binding["hostname"]},
        ) as response:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 16384:
                    raise ValueError("Receiver response exceeds local limit")
                body.extend(chunk)
            return response.status_code, response.headers.get("content-type", ""), bytes(body)


async def validate_receiver(binding):
    token = secrets.token_urlsafe(32)
    try:
        status, content_type, body = await post_receiver(
            binding, validation_token=token, timeout=5.0
        )
    except (httpx.HTTPError, OSError, ValueError):
        raise GraphError(400, "ValidationError", "Local receiver validation failed") from None
    if (
        status != 200
        or content_type.split(";")[0].strip().lower() != "text/plain"
        or body != token.encode()
    ):
        raise GraphError(400, "ValidationError", "Receiver must echo validationToken as plain text")
