"""Qualification-only request tracing; never capture headers, query strings or bodies."""
import json
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
            stream.write(json.dumps({"time":datetime.now(UTC).isoformat(),
                "method":request.method,"path":request.url.path,"status":response.status_code})+"\n")
    return response


uvicorn.run(app, host="0.0.0.0", port=8443, proxy_headers=False,
            ssl_certfile="/instance/server.pem", ssl_keyfile="/instance/server.key",
            access_log=False, log_level="warning")
