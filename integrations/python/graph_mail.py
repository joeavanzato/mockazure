"""Real MSAL and generated Graph SDK mailbox and audit query workflows."""

import argparse
import asyncio
import json
import ssl
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from graph_directory import acquire, graph_client
from kiota_abstractions.api_error import APIError
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.message_rule import MessageRule
from msgraph.generated.models.message_rule_actions import MessageRuleActions
from msgraph.generated.models.recipient import Recipient
from msgraph.generated.models.security.audit_log_query import AuditLogQuery
from msgraph.generated.models.security.audit_log_query_status import AuditLogQueryStatus


async def denied_call(awaitable):
    try:
        await awaitable
    except APIError as error:
        assert error.response_status_code == 403
    else:
        raise AssertionError("Expected denied Graph call")


async def collect(builder, first=None):
    page = first or await builder.get()
    values = list(page.value or [])
    pages = 1
    while page.odata_next_link:
        page = await builder.with_url(page.odata_next_link).get()
        values.extend(page.value or [])
        pages += 1
        assert pages <= 100
    return values, pages


async def wait_query(builder):
    for _ in range(120):
        row = await builder.get()
        if row.status == AuditLogQueryStatus.Succeeded:
            return row
        assert row.status in {AuditLogQueryStatus.NotStarted, AuditLogQueryStatus.Running}
        await asyncio.sleep(0.5)
    raise AssertionError("Audit query did not finish")


async def workflow(config, raw, denied, mode):
    checks = {}
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False, timeout=60
    ) as transport:
        graph = graph_client(config, raw, transport)
        user = graph.users.by_user_id(config["user_id"])
        rules = user.mail_folders.by_mail_folder_id("inbox").message_rules
        before, _ = await collect(rules)
        started = datetime.now(UTC) - timedelta(seconds=1)
        created = await rules.post(
            MessageRule(
                display_name="Python mail qualification " + mode,
                sequence=0,
                is_enabled=True,
                actions=MessageRuleActions(
                    forward_to=[
                        Recipient(email_address=EmailAddress(address="sdk-target@example.test"))
                    ],
                    stop_processing_rules=True,
                ),
            )
        )
        item = rules.by_message_rule_id(created.id)
        loaded = await item.get()
        assert loaded.actions.forward_to[0].email_address.address == "sdk-target@example.test"
        assert loaded.actions.stop_processing_rules is True
        checks["rule_actions"] = True
        await item.patch(MessageRule(display_name="Python updated " + mode, is_enabled=False))
        assert (await item.get()).is_enabled is False
        await item.delete()
        after, _ = await collect(rules)
        assert {r.id for r in after} == {r.id for r in before}
        checks["rule_crud"] = True
        first = await rules.with_url(
            config["endpoints"]["graph"]
            + f"/v1.0/users/{config['user_id']}/mailFolders/inbox/messageRules?$top=1"
        ).get()
        all_rules, pages = await collect(rules, first)
        assert pages >= 3
        assert {r.id for r in all_rules} == set(config["mail_fixture"]["rule_ids"])
        checks["rule_paging"] = True
        queries = graph.security.audit_log.queries
        fixture = config["mail_fixture"]
        search = await queries.post(
            AuditLogQuery(
                display_name="Python audit " + mode,
                service_filters=["Exchange"],
                operation_filters=["MailItemsAccessed"],
                filter_start_date_time=datetime.fromisoformat(fixture["audit_start"]),
                filter_end_date_time=datetime.fromisoformat(fixture["audit_end"]),
            )
        )
        assert search.id and search.status == AuditLogQueryStatus.NotStarted
        query = queries.by_audit_log_query_id(search.id)
        await wait_query(query)
        checks["audit_query"] = True
        records, pages = await collect(query.records)
        assert len(records) == len({r.id for r in records}) == len(fixture["audit_ids"])
        assert {r.id for r in records} == set(fixture["audit_ids"]) and pages > 1
        assert all(r.operation == "MailItemsAccessed" and r.service == "Exchange" for r in records)
        checks["audit_paging"] = True
        filtered = await queries.post(
            AuditLogQuery(
                display_name="Python exact audit",
                service_filters=["Exchange"],
                operation_filters=["MailItemsAccessed"],
                object_id_filters=[fixture["audit_object"]],
                filter_start_date_time=datetime.fromisoformat(fixture["audit_start"]),
                filter_end_date_time=datetime.fromisoformat(fixture["audit_end"]),
            )
        )
        selected = queries.by_audit_log_query_id(filtered.id)
        await wait_query(selected)
        exact, _ = await collect(selected.records)
        assert len(exact) == 1 and exact[0].id == fixture["audit_ids"][0]
        checks["audit_filters"] = True
        # Projection is asynchronous; each retry creates a new captured publication snapshot.
        for _ in range(30):
            native = await queries.post(
                AuditLogQuery(
                    display_name="Python native rule facts",
                    operation_filters=["New-InboxRule", "Set-InboxRule", "Remove-InboxRule"],
                    object_id_filters=[config["mailbox_upn"] + "\\" + created.id],
                    filter_start_date_time=started,
                    filter_end_date_time=datetime.now(UTC),
                )
            )
            snapshot = queries.by_audit_log_query_id(native.id)
            await wait_query(snapshot)
            facts, _ = await collect(snapshot.records)
            if len(facts) == 3:
                break
            await asyncio.sleep(0.5)
        assert {r.operation for r in facts} == {
            "New-InboxRule",
            "Set-InboxRule",
            "Remove-InboxRule",
        }
        checks["native_evidence"] = True
        restricted = graph_client(config, denied, transport)
        await denied_call(
            restricted.users.by_user_id(config["user_id"])
            .mail_folders.by_mail_folder_id("inbox")
            .message_rules.get()
        )
        await denied_call(restricted.security.audit_log.queries.get())
        checks["permission_denied"] = True
    return checks


async def run(config):
    versions = {name: version(name) for name in ("msal", "msgraph-sdk", "azure-identity")}
    assert versions == {"msal": "1.39.0", "msgraph-sdk": "1.64.0", "azure-identity": "1.26.0"}
    for endpoint in config["endpoints"].values():
        url = urlsplit(endpoint)
        assert url.scheme == "https" and (
            url.hostname in {"localhost", "127.0.0.1"} or url.hostname.endswith(".mockazure.test")
        )
    tokens = await asyncio.to_thread(acquire, config)
    result = {"passed": False, "modes": {}, "versions": versions}
    for mode in ("application", "delegated"):
        result["modes"][mode] = await workflow(config, tokens[mode], tokens["denied_" + mode], mode)
    result["passed"] = True
    Path(config["output_file"]).write_text(json.dumps(result, indent=2))
    print(json.dumps({"passed": True, "client": "Graph Python 1.64.0"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/state/client.json"))
    asyncio.run(run(json.loads(parser.parse_args().config.read_text())))
