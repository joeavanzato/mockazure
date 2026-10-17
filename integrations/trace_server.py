"""Qualification-only request tracing; capture only paths and allowlisted query metadata."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import uvicorn

from mockazure.admin.app import create_app
from mockazure.config import load_settings

settings = load_settings(Path("/instance/container.json"))
app = create_app(settings)


@app.middleware("http")
async def trace(request, call_next):
    response = await call_next(request)
    if request.url.path not in ("/_mock/health", "/_mock/ready"):
        with Path("/state/requests.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "time": datetime.now(UTC).isoformat(),
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "commands": request.headers.get("commandname")
                        if re.fullmatch(
                            r"[A-Za-z*, -]{0,2000}", request.headers.get("commandname", "")
                        )
                        else None,
                        "query_keys": [
                            k
                            for k in request.query_params
                            if re.fullmatch(r"[A-Za-z$][A-Za-z0-9_$]{0,60}", k)
                        ],
                        "select": request.query_params.get("$select")
                        if re.fullmatch(
                            r"[A-Za-z0-9_,]{1,4000}", request.query_params.get("$select", "")
                        )
                        else None,
                        "continuation": "$skiptoken" in request.query_params,
                        "versions": {
                            k: v
                            for k, v in request.query_params.items()
                            if k in ("api-version", "Version")
                            and re.fullmatch(r"[0-9.a-z-]{1,40}", v)
                        },
                    }
                )
                + "\n"
            )
    return response


uvicorn.run(
    app,
    host="0.0.0.0",
    port=8443,
    proxy_headers=False,
    ssl_certfile="/instance/server.pem",
    ssl_keyfile="/instance/server.key",
    access_log=False,
    log_level="warning",
)
