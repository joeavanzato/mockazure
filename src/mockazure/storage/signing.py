"""Microsoft Storage Shared Key strings, without decoding the encoded URI path."""

import base64
import hashlib
import hmac
import re
from collections import defaultdict

STANDARD = (
    "content-encoding",
    "content-language",
    "content-length",
    "content-md5",
    "content-type",
    "date",
    "if-modified-since",
    "if-match",
    "if-none-match",
    "if-unmodified-since",
    "range",
)


def header_values(headers):
    result = defaultdict(list)
    for name, value in headers:
        name = name.lower()
        # HTTP linear whitespace outside quoted values is folded only once.
        parts = re.split(r'("(?:[^"\\]|\\.)*")', value.strip())
        value = "".join(p if i % 2 else re.sub(r"[ \t\r\n]+", " ", p) for i, p in enumerate(parts))
        result[name].append(value)
    if any(
        len(result.get(n, [])) > 1
        for n in (*STANDARD, "authorization", "host", "x-ms-date", "x-ms-version")
    ):
        raise ValueError("Duplicate singleton header")
    return {k: ",".join(v) for k, v in result.items()}


def canonicalize_storage_request(
    method: str,
    raw_path: bytes,
    query: list[tuple[str, str]],
    headers: list[tuple[str, str]],
    account: str,
    service: str,
    version: str,
) -> bytes:
    return canonicalize(method, raw_path, query, headers, account, service, version, lite=False)


def canonicalize(method, raw_path, query, headers, account, service, version, *, lite):
    values = header_values(headers)
    resource = "/" + account + raw_path.decode("ascii")
    args = defaultdict(list)
    for key, value in query:
        args[key.lower()].append(value)
    if service == "table" or lite:
        if "comp" in args:
            resource += "?comp=" + ",".join(sorted(args["comp"]))
    else:
        resource += "".join("\n" + k + ":" + ",".join(sorted(v)) for k, v in sorted(args.items()))
    date = (
        values.get("x-ms-date", values.get("date", ""))
        if service == "table"
        else ("" if "x-ms-date" in values else values.get("date", ""))
    )
    if service == "table" and lite:
        return (date + "\n" + resource).encode()
    if service == "table" or lite:
        fields = [method, values.get("content-md5", ""), values.get("content-type", ""), date]
    else:
        fields = [method] + [values.get(n, "") for n in STANDARD]
        fields[6] = date
        if fields[3] == "0" and version >= "2015-02-21":
            fields[3] = ""
    custom = (
        ""
        if service == "table"
        else "".join(k + ":" + v + "\n" for k, v in sorted(values.items()) if k.startswith("x-ms-"))
    )
    return ("\n".join(fields) + "\n" + custom + resource).encode()


def signature(key: str, value: bytes) -> str:
    return base64.b64encode(
        hmac.new(base64.b64decode(key, validate=True), value, hashlib.sha256).digest()
    ).decode("ascii")


def matches(keys, supplied, value):
    if not isinstance(supplied, str) or not supplied.isascii():
        return False
    return any([hmac.compare_digest(signature(k, value), supplied) for k in keys])
