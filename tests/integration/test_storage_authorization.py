"""Signed data-plane requests must enforce current account state before mutation."""

import base64
import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree

import pytest

from tests.integration.graph_support import graph_client
from tests.integration.test_storage_account_management import ARGS, CREATE, setup

VERSION = "2025-11-05"


def signed(method, url, key, *, now=None, query=None, version=VERSION):
    """Independent published Shared Key vector for these empty-body requests."""
    path = urlsplit(url).path
    account = path.split("/")[1]
    date = format_datetime(now or datetime.now(UTC), usegmt=True)
    canonical = method + "\n" * 12 + f"x-ms-date:{date}\nx-ms-version:{version}\n/{account}{path}"
    for name, value in sorted((query or {}).items()):
        canonical += "\n" + name.lower() + ":" + value
    signature = base64.b64encode(
        hmac.new(base64.b64decode(key), canonical.encode(), hashlib.sha256).digest()
    ).decode()
    return {
        "x-ms-date": date,
        "x-ms-version": version,
        "Authorization": f"SharedKey {account}:{signature}",
    }


async def provision(client, database, settings, tenant, app):
    sub, base, group, principal, headers = await setup(client, database, settings, tenant, app)
    path = group + "/providers/Microsoft.Storage/storageAccounts/signedaccount"
    result = await client.put(path, params=ARGS, headers=headers, json=CREATE)
    assert result.status_code == 200, result.text
    keys = (await client.post(path + "/listKeys", params=ARGS, headers=headers)).json()["keys"]
    return path, headers, result.json()["properties"]["primaryEndpoints"], keys[0]["value"]


@pytest.mark.parametrize("version", [VERSION, "2025-01-05", "2019-07-07"])
async def test_signed_container_share_rest_roundtrip(database, settings, version):
    # Original four-ecosystem SDK qualification is an additional required C1 gate.
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        for service, kind in (("blob", "container"), ("file", "share")):
            url = endpoints[service] + "evidence"
            query = {"restype": kind}
            result = await client.put(
                url, params=query, headers=signed("PUT", url, key, query=query, version=version)
            )
            assert result.status_code == 201, result.text
            assert result.headers["ETag"] and result.headers["x-ms-request-id"]
            read = await client.head(
                url, params=query, headers=signed("HEAD", url, key, query=query, version=version)
            )
            assert read.status_code == 200 and read.content == b"", dict(read.headers)
            base = endpoints[service]
            listing = await client.get(
                base,
                params={"comp": "list"},
                headers=signed("GET", base, key, query={"comp": "list"}, version=version),
            )
            assert listing.status_code == 200, listing.text
            assert "evidence" in [
                node.text for node in ElementTree.fromstring(listing.content).findall(".//Name")
            ]


async def test_storage_auth_boundaries(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["blob"] + "evidence"
        query = {"restype": "container"}
        tampered = signed("PUT", url, key, query=query)
        tampered["Authorization"] = tampered["Authorization"][:-2] + "AA"
        assert (await client.put(url, params=query, headers=tampered)).status_code == 403
        expired = signed(
            "PUT", url, key, query=query, now=datetime.now(UTC) - timedelta(minutes=16)
        )
        assert (await client.put(url, params=query, headers=expired)).status_code == 403
        assert (
            await client.put(url, params=query, headers=arm | {"x-ms-version": VERSION})
        ).status_code == 403
        valid = signed("PUT", url, key, query=query)
        assert (await client.put(url, params=query, headers=valid)).status_code == 201
        assert (
            await client.post(
                path + "/regenerateKey", params=ARGS, headers=arm, json={"keyName": "key1"}
            )
        ).status_code == 200
        assert (
            await client.get(url, params=query, headers=signed("GET", url, key, query=query))
        ).status_code == 403
        current = (await client.post(path + "/listKeys", params=ARGS, headers=arm)).json()["keys"][
            0
        ]["value"]
        assert (
            await client.patch(
                path, params=ARGS, headers=arm, json={"properties": {"allowSharedKeyAccess": False}}
            )
        ).status_code == 200
        assert (
            await client.get(url, params=query, headers=signed("GET", url, current, query=query))
        ).status_code == 403


def account_sas(key, **changes):
    now = datetime.now(UTC)
    args = {
        "sv": VERSION,
        "ss": "bf",
        "srt": "sc",
        "sp": "rcwdl",
        "st": (now - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "se": (now + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "spr": "https",
    } | changes
    value = (
        "signedaccount\n"
        + "\n".join(
            args.get(k, "") for k in ("sp", "ss", "srt", "st", "se", "sip", "spr", "sv", "ses")
        )
        + "\n"
    )
    args["sig"] = base64.b64encode(
        hmac.new(base64.b64decode(key), value.encode(), hashlib.sha256).digest()
    ).decode()
    return args


async def test_account_sas_restrictions_and_network_gate(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        client.headers.pop("Authorization", None)
        url = endpoints["blob"] + "sascontainer"
        query = {"restype": "container"}
        for changes in (
            {"sp": "r"},
            {"ss": "f"},
            {"srt": "o"},
            {"st": (datetime.now(UTC) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            {"se": (datetime.now(UTC) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")},
            {"sip": "192.0.2.1"},
        ):
            denied = await client.put(url, params=query | account_sas(key, **changes))
            assert denied.status_code == 403, denied.text
        ok = await client.put(url, params=query | account_sas(key))
        assert ok.status_code == 201, ok.text
        assert (await client.head(url, params=query | account_sas(key, sp="r"))).status_code == 200
        assert (
            await client.patch(
                path,
                params=ARGS,
                headers=arm,
                json={
                    "properties": {
                        "networkAcls": {"defaultAction": "Deny", "bypass": "None", "ipRules": []}
                    }
                },
            )
        ).status_code == 200
        denied = await client.get(
            url, params=query | account_sas(key), headers={"X-Forwarded-For": "127.0.0.1"}
        )
        assert denied.status_code == 403


async def test_storage_names_paging_etags_and_duplicate_create(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        client.headers.pop("Authorization", None)
        for service, kind in (("blob", "container"), ("file", "share")):
            query = {"restype": kind}
            for name in ("first", "second", "third"):
                url = endpoints[service] + name
                result = await client.put(url, params=query | account_sas(key))
                assert result.status_code == 201, result.text
            duplicate = await client.put(url, params=query | account_sas(key))
            assert duplicate.status_code == 409
            bad = await client.put(endpoints[service] + "Bad_Name", params=query | account_sas(key))
            assert bad.status_code == 400
            listing = await client.get(
                endpoints[service], params={"comp": "list", "maxresults": "2"} | account_sas(key)
            )
            assert listing.status_code == 200, listing.text
            xml = ElementTree.fromstring(listing.content)
            assert [x.text for x in xml.findall(".//Name")] == ["first", "second"]
            marker = xml.findtext("NextMarker")
            assert marker
            tail = await client.get(
                endpoints[service],
                params={"comp": "list", "marker": marker, "maxresults": "2"} | account_sas(key),
            )
            assert tail.status_code == 200, tail.text
            assert [x.text for x in ElementTree.fromstring(tail.content).findall(".//Name")] == [
                "third"
            ]
            deleted = await client.delete(url, params=query | account_sas(key))
            assert deleted.status_code == 202
            assert (await client.head(url, params=query | account_sas(key))).status_code == 404


async def test_data_origins_do_not_expose_core_routes(database, settings):
    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        for service in ("blob", "file"):
            origin = endpoints[service].rsplit("/signedaccount/", 1)[0]
            result = await client.get(origin + "/_mock/health")
            assert result.status_code != 200, "Data listeners exposed the admin API"
            wrong = await client.put(
                "https://localhost:8443/signedaccount/evidence",
                params={"restype": "container"} | account_sas(key),
            )
            assert wrong.status_code != 201


async def test_namespace_migration_refuses_lossy_downgrade(database, settings):
    from alembic import command
    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import DBAPIError

    from mockazure.db.migrations import configuration

    async with graph_client(database, settings) as (client, tenant, app, user):
        path, arm, endpoints, key = await provision(client, database, settings, tenant, app)
        url = endpoints["blob"] + "preserved"
        query = {"restype": "container"}
        assert (
            await client.put(url, params=query, headers=signed("PUT", url, key, query=query))
        ).status_code == 201
    engine = create_engine(settings.database_url.get_secret_value(), hide_parameters=True)
    try:
        with pytest.raises(DBAPIError, match="Cannot discard Storage namespace state"):
            with engine.begin() as connection:
                config = configuration()
                config.attributes["connection"] = connection
                command.downgrade(config, "060")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "073"
            assert connection.scalar(text("SELECT name FROM storage_namespaces")) == "preserved"
    finally:
        engine.dispose()
