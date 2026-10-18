"""Unchanged generated Microsoft Graph SDK against the local mail wire contract."""

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
from msgraph.generated.models.message import Message
from msgraph.generated.users.item.messages.messages_request_builder import MessagesRequestBuilder


async def main():
    phase = sys.argv[1]
    config = json.loads(Path("/state/client.json").read_text())
    token = client_token(
        config, config["client_id"], json.loads(Path(config["secret_file"]).read_text())["secret"]
    )
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False, timeout=30
    ) as http:
        graph = graph_client(config, token["access_token"], http)
        user = graph.users.by_user_id(config["python_user"])
        delta_builder = user.mail_folders.by_mail_folder_id("inbox").messages.delta
        if phase == "prepare":
            folders = await user.mail_folders.get()
            assert {v.id for v in folders.value} == {"inbox", "archive"}
            query = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
                top=25, select=["id", "subject"]
            )
            page = await user.messages.get(RequestConfiguration(query_parameters=query))
            rows, pages = list(page.value), 1
            while page.odata_next_link:
                page = await user.messages.with_url(page.odata_next_link).get()
                rows.extend(page.value)
                pages += 1
            assert len(rows) == 101 and pages == 5
            raw = await user.messages.by_message_id("m0").content.get()
            assert hashlib.sha256(raw).hexdigest() == config["mime_sha256"]
            attachment = (
                await user.messages.by_message_id("m0").attachments.by_attachment_id("a1").get()
            )
            assert (
                hashlib.sha256(attachment.content_bytes).hexdigest() == config["attachment_sha256"]
            )
            initial, link = await delta_round(delta_builder)
            assert len(initial) == 101
            Path("/state/python-delta.json").write_text(json.dumps({"link": link}))
            await user.messages.by_message_id("m0").patch(Message(is_read=True))
            Path("/state/python-report.json").write_text(
                json.dumps(
                    {
                        "client": "msgraph-sdk",
                        "version": version("msgraph-sdk"),
                        "folders": 2,
                        "messages": len(rows),
                        "pages": pages,
                        "mime_sha256": hashlib.sha256(raw).hexdigest(),
                        "attachment_sha256": config["attachment_sha256"],
                    }
                )
            )
        else:
            link = json.loads(Path("/state/python-delta.json").read_text())["link"]
            changes, _ = await delta_round(delta_builder, link)
            assert {row.id for row in changes} == {"m0", "m1"}
            assert next(row for row in changes if row.id == "m0").is_read is True
            assert (
                next(row for row in changes if row.id == "m1").additional_data["@removed"]["reason"]
                == "deleted"
            )
            report = json.loads(Path("/state/python-report.json").read_text())
            report["delta_after_restart"] = True
            Path("/state/python-report.json").write_text(json.dumps(report))


asyncio.run(main())
