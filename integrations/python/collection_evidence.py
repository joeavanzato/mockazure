"""Exercise generated Graph models and paging against the isolated local lab."""

import asyncio
import json
import ssl
from importlib.metadata import version
from pathlib import Path

import httpx
from graph_directory import graph_client
from graph_security import client_token
from kiota_abstractions.api_error import APIError


async def main():
    config = json.loads(Path("/state/client.json").read_text())
    raw = client_token(
        config, config["client_id"], json.loads(Path(config["secret_file"]).read_text())["secret"]
    )["access_token"]
    report = {"client": "msgraph-sdk", "version": version("msgraph-sdk"), "checks": {}}
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False, timeout=30
    ) as http:
        graph = graph_client(config, raw, http)
        for attr, expected in [
            ("alerts", 301),
            ("alerts_v2", 301),
            ("incidents", 5),
            ("secure_scores", 3),
            ("secure_score_control_profiles", 101),
        ]:
            builder = getattr(graph.security, attr)
            page = await builder.get()
            rows = list(page.value or [])
            pages = 1
            while page.odata_next_link:
                assert page.odata_next_link.startswith("https://localhost:8443/")
                page = await builder.with_url(page.odata_next_link).get()
                rows += page.value or []
                pages += 1
            assert len(rows) == expected and len({r.id for r in rows}) == expected
            report["checks"][attr] = {"rows": len(rows), "pages": pages}
        alert = await graph.security.alerts_v2.by_alert_id("alert-000").get()
        assert alert.evidence[0].user_account.user_principal_name == "analyst@contoso.test"
        report["checks"]["typed_user_evidence"] = True
        negative = client_token(
            config,
            config["negative_client_id"],
            json.loads(Path(config["negative_secret_file"]).read_text())["secret"],
        )["access_token"]
        denied = graph_client(config, negative, http)
        try:
            await denied.security.alerts_v2.get()
        except APIError as error:
            assert error.response_status_code == 403
        else:
            raise AssertionError("Legacy permission incorrectly allowed modern alerts")
        report["checks"]["distinct_alert_permission"] = True
    Path("/state/python-report.json").write_text(json.dumps(report, indent=2) + "\n")


asyncio.run(main())
