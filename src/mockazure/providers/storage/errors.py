"""Storage's XML error envelope; never echo signatures, keys, or supplied tokens."""

from dataclasses import dataclass
from xml.etree.ElementTree import Element, SubElement, tostring

from starlette.responses import JSONResponse, Response


@dataclass
class StorageError(Exception):
    status: int
    code: str
    message: str


def headers(request):
    result = {
        "x-ms-request-id": request.state.request_id,
        "x-ms-version": request.headers.get("x-ms-version", "2025-11-05")
        if request.headers.get("x-ms-version") in {"2019-07-07", "2025-01-05", "2025-11-05"}
        else "2025-11-05",
    }

    correlation = request.headers.get("x-ms-client-request-id", "")
    if correlation and len(correlation) <= 1024 and all(32 <= ord(c) < 127 for c in correlation):
        result["x-ms-client-request-id"] = correlation
    return result


def response(request, error, service):
    head = headers(request) | {"x-ms-error-code": error.code}
    if service == "table":
        return JSONResponse(
            {
                "odata.error": {
                    "code": error.code,
                    "message": {"lang": "en-US", "value": error.message},
                }
            },
            status_code=error.status,
            headers=head,
        )
    root = Element("Error")
    for key, value in [
        ("Code", error.code),
        ("Message", error.message),
        ("RequestId", request.state.request_id),
    ]:
        SubElement(root, key).text = value
    return Response(
        b"" if request.method == "HEAD" else tostring(root, encoding="utf-8", xml_declaration=True),
        status_code=error.status,
        headers=head,
        media_type="application/xml",
    )
