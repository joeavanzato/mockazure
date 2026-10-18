"""Original generated Graph SDK over local Teams evidence, including saved pages."""

import asyncio
import hashlib
import json
import ssl
import sys
from importlib.metadata import version
from pathlib import Path

import httpx
from graph_directory import graph_client
from graph_security import client_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.chats.item.messages.messages_request_builder import MessagesRequestBuilder


async def collect(builder, page=None):
    page = page or await builder.get()
    rows, pages = list(page.value), 1
    while page.odata_next_link:
        page = await builder.with_url(page.odata_next_link).get()
        rows.extend(page.value)
        pages += 1
    return rows, pages


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
        verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False, timeout=30
    ) as http:
        graph = graph_client(config, token["access_token"], http)
        team = graph.teams.by_team_id(fixture["team"])
        channel = team.channels.by_channel_id(fixture["channel"])
        chat = graph.chats.by_chat_id(fixture["chat"])
        report_path = Path("/state/python-report.json")
        if phase == "prepare":
            assert (await team.get()).id == fixture["team"]
            teams, _ = await collect(graph.users.by_user_id(config["user_id"]).joined_teams)
            assert fixture["team"] in {row.id for row in teams}
            channels, _ = await collect(team.channels)
            assert [row.id for row in channels] == [fixture["channel"]]
            chats, _ = await collect(graph.users.by_user_id(config["user_id"]).chats)
            assert fixture["chat"] in {row.id for row in chats}
            messages, channel_pages = await collect(channel.messages)
            assert len(messages) == 101 and channel_pages == 6
            replies, reply_pages = await collect(channel.messages.by_chat_message_id("0").replies)
            assert len(replies) == 201 and reply_pages == 5
            hosted = channel.messages.by_chat_message_id("0").hosted_contents
            assert [row.id for row in (await hosted.get()).value] == ["hosted-one"]
            raw = await hosted.by_chat_message_hosted_content_id("hosted-one").content.get()
            assert hashlib.sha256(raw).hexdigest() == config["content_sha256"]
            query = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(top=25)
            first = await chat.messages.get(RequestConfiguration(query_parameters=query))
            all_messages, chat_pages = await collect(chat.messages, first)
            assert len(all_messages) == 101 and chat_pages == 5
            Path("/state/python-page.json").write_text(
                json.dumps({"link": first.odata_next_link}), encoding="utf-8"
            )
            report = {
                "client": "msgraph-sdk",
                "version": version("msgraph-sdk"),
                "channel_messages": 101,
                "chat_messages": 101,
                "replies": 201,
                "channel_pages": channel_pages,
                "chat_pages": chat_pages,
                "reply_pages": reply_pages,
                "hosted_sha256": config["content_sha256"],
            }
        else:
            link = json.loads(Path("/state/python-page.json").read_text(encoding="utf-8"))["link"]
            rows, _ = await collect(chat.messages, await chat.messages.with_url(link).get())
            assert len(rows) == 76 and all(row.body.content == "Local evidence" for row in rows)
            assert (
                await chat.messages.by_chat_message_id("1").get()
            ).body.content == "Edited after snapshot"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["snapshot_after_restart"] = True
        report_path.write_text(json.dumps(report), encoding="utf-8")


asyncio.run(main())
