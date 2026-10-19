"""Original generated Graph Python SDK against an explicit local endpoint."""

import argparse
import asyncio
import json
import ssl
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx
from kiota_serialization_json.json_parse_node import JsonParseNode
from msgraph.generated.models.event import Event
from msgraph.generated.models.message import Message
from msgraph.generated.models.subscription import Subscription
from msgraph.generated.users.item.events.item.accept.accept_post_request_body import (
    AcceptPostRequestBody,
)
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (
    SendMailPostRequestBody,
)

from integrations.python.graph_directory import graph_client


def typed(body, kind):
    return JsonParseNode(body).get_object_value(kind)


async def run(p, mode):
    origin = p["endpoint"]
    assert urlsplit(origin).hostname in {"localhost", "127.0.0.1"}

    async def local_only(request):
        assert str(request.url).startswith(origin + "/")

    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=p["ca_file"]),
        trust_env=False,
        follow_redirects=False,
        event_hooks={"request": [local_only]},
    ) as http:
        graph = graph_client({"endpoints": {"graph": origin}}, p["token"], http)
        user = graph.users.by_user_id(p["owner"])
        checks = {}
        objects = {}
        if mode == "write":
            sub = await graph.subscriptions.post(typed(p["subscription_body"], Subscription))
            draft = await user.messages.post(typed(p["message_body"], Message))
            assert draft.id and draft.is_draft
            await user.messages.by_message_id(draft.id).patch(
                Message(subject=p["label"] + " persistent")
            )
            got = await user.messages.by_message_id(draft.id).get()
            assert got.subject == p["label"] + " persistent"
            assert (
                await user.send_mail.post(
                    SendMailPostRequestBody(
                        message=typed(p["send_body"], Message), save_to_sent_items=True
                    )
                )
                is None
            )
            event = await user.events.post(typed(p["event_body"], Event))
            window = urlencode({"startDateTime": p["window_start"], "endDateTime": p["window_end"]})
            instances = (
                await user.events.by_event_id(event.id)
                .instances.with_url(
                    origin + f"/v1.0/users/{p['owner']}/events/{event.id}/instances?" + window
                )
                .get()
            )
            assert len(instances.value) == 3
            times = [row.start.date_time for row in instances.value]
            assert "T13:00:00" in times[0] and all("T14:00:00" in t for t in times[1:])
            delta = await user.calendar_view.delta.with_url(
                origin + f"/v1.0/users/{p['owner']}/calendarView/delta?" + window
            ).get()
            assert delta.odata_delta_link
            await user.events.by_event_id(event.id).patch(Event(subject=p["label"] + " updated"))
            changed = await user.calendar_view.delta.with_url(delta.odata_delta_link).get()
            assert changed.value
            target = graph.users.by_user_id(p["recipient"])
            for _ in range(80):
                message_query = urlencode(
                    {"$filter": "subject eq '" + p["send_body"]["subject"].replace("'", "''") + "'"}
                )
                messages = await target.messages.with_url(
                    origin + f"/v1.0/users/{p['recipient']}/messages?" + message_query
                ).get()
                meetings = await target.events.get()
                mail = next(
                    (m for m in messages.value if m.subject == p["send_body"]["subject"]), None
                )
                invite = next((e for e in meetings.value if e.i_cal_u_id == event.i_cal_u_id), None)
                if mail and invite:
                    break
                await asyncio.sleep(0.25)
            assert mail and invite and invite.id != event.id
            attachments = await target.messages.by_message_id(mail.id).attachments.get()
            assert any(
                a.name == "evidence.bin" and a.content_bytes == b"local\x00attachment"
                for a in attachments.value
            )
            await target.events.by_event_id(invite.id).accept.post(
                AcceptPostRequestBody(send_response=True)
            )
            await graph.subscriptions.by_subscription_id(sub.id).patch(
                Subscription(expiration_date_time=datetime.now(UTC) + timedelta(hours=3))
            )
            objects = {
                "message": draft.id,
                "event": event.id,
                "subscription": sub.id,
                "owner": p["owner"],
            }
            checks.update(
                mail_roundtrip=True,
                calendar_recurrence_delta=True,
                meeting_response=True,
                subscription_management=True,
            )
        else:
            fixtures = json.loads(Path(p["fixtures"]).read_text("utf-8"))
            for fixture in fixtures.values():
                other = graph.users.by_user_id(fixture["owner"])
                assert (await other.messages.by_message_id(fixture["message"]).get()).id == fixture[
                    "message"
                ]
                before = await other.events.by_event_id(fixture["event"]).get()
                assert before.attendees[0].status.response.value == "accepted"
                await other.events.by_event_id(fixture["event"]).patch(
                    Event(subject="Cross python")
                )
                assert (
                    await other.events.by_event_id(fixture["event"]).get()
                ).subject == "Cross python"
            own = fixtures["python"]
            assert (
                await graph.subscriptions.by_subscription_id(own["subscription"]).get()
            ).id == own["subscription"]
            await graph.subscriptions.by_subscription_id(own["subscription"]).reauthorize.post()
            await graph.subscriptions.by_subscription_id(own["subscription"]).delete()
            checks.update(
                cross_language_reads=len(fixtures) == 4,
                subscription_management=True,
                persistent_read=True,
            )
        return {
            "passed": True,
            "clientVersions": {"msgraph-sdk": version("msgraph-sdk")},
            "assertions": checks,
            "objects": objects,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mode", choices=["write", "read"], required=True)
    args = parser.parse_args()
    p = json.loads(args.profile.read_text("utf-8"))
    args.report.write_text(json.dumps(asyncio.run(run(p, args.mode)), indent=2) + "\n", "utf-8")


if __name__ == "__main__":
    main()
