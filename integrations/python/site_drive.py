"""Original generated Graph SDK: discovery, library walk, bytes and durable delta."""

import asyncio
import hashlib
import json
import ssl
import sys
from importlib.metadata import version
from pathlib import Path

import httpx
from graph_directory import delta_round, graph_client
from graph_security import client_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.drives.item.items.item.children.children_request_builder import (
    ChildrenRequestBuilder,
)


async def main():
    phase = sys.argv[1]
    config = json.loads(Path("/state/client.json").read_text(encoding="utf-8"))
    token = client_token(
        config,
        config["client_id"],
        json.loads(Path(config["secret_file"]).read_text(encoding="utf-8"))["secret"],
    )
    fixture = config["fixtures"]["python"]
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]),
        trust_env=False,
        timeout=30,
        follow_redirects=True,
    ) as http:
        graph = graph_client(config, token["access_token"], http)
        site = graph.sites.by_site_id(fixture["site"])
        drive = graph.drives.by_drive_id(fixture["drive"])
        root = drive.items.by_drive_item_id(fixture["root"])
        delta = root.delta
        report_path = Path("/state/python-report.json")
        if phase == "prepare":
            assert (await site.get()).id == fixture["site"]
            drives = await site.drives.get()
            assert [d.id for d in drives.value] == [fixture["drive"]]
            query = ChildrenRequestBuilder.ChildrenRequestBuilderGetQueryParameters(top=25)
            page = await root.children.get(RequestConfiguration(query_parameters=query))
            rows, pages = list(page.value), 1
            while page.odata_next_link:
                page = await root.children.with_url(page.odata_next_link).get()
                rows.extend(page.value)
                pages += 1
            assert len(rows) == 101 and pages == 5
            for item in rows:
                raw = await drive.items.by_drive_item_id(item.id).content.get()
                assert hashlib.sha256(raw).hexdigest() == config["content_sha256"]
            initial, link = await delta_round(delta)
            assert len(initial) == 102
            Path("/state/python-delta.json").write_text(
                json.dumps({"link": link}), encoding="utf-8"
            )
            report = {
                "client": "msgraph-sdk",
                "version": version("msgraph-sdk"),
                "sites": 1,
                "drives": 1,
                "files": len(rows),
                "pages": pages,
                "verified_downloads": len(rows),
                "content_sha256": config["content_sha256"],
            }
        else:
            link = json.loads(Path("/state/python-delta.json").read_text(encoding="utf-8"))["link"]
            changes, _ = await delta_round(delta, link)
            assert (
                next(row for row in changes if row.id == "python!file0").name
                == "Renamed evidence.bin"
            )
            assert next(row for row in changes if row.id == "python!file1").deleted is not None
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["delta_after_restart"] = True
        report_path.write_text(json.dumps(report), encoding="utf-8")


asyncio.run(main())
