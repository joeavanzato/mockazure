"""Original generated SDK over local HTTPS, including heterogeneous result models."""

import asyncio
import json
import ssl
from importlib.metadata import version
from pathlib import Path

import httpx
from graph_directory import graph_client
from graph_security import client_token
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.directory_objects.get_by_ids.get_by_ids_post_request_body import (
    GetByIdsPostRequestBody,
)
from msgraph.generated.groups.item.member_of.member_of_request_builder import (
    MemberOfRequestBuilder,
)


async def main():
    config = json.loads(Path("/state/client.json").read_text())
    token = client_token(
        config, config["client_id"], json.loads(Path(config["secret_file"]).read_text())["secret"]
    )
    async with httpx.AsyncClient(
        verify=ssl.create_default_context(cafile=config["ca_file"]), trust_env=False, timeout=30
    ) as http:
        graph = graph_client(config, token["access_token"], http)
        expected = {config["user_id"], *config["group_ids"]}
        objects = await graph.directory_objects.get_by_ids.post(
            GetByIdsPostRequestBody(ids=list(expected))
        )
        assert {x.id for x in objects.value} == expected
        assert {x.odata_type for x in objects.value} == {
            "#microsoft.graph.user",
            "#microsoft.graph.group",
        }
        item = await graph.directory_objects.by_directory_object_id(config["user_id"]).get()
        assert item.id == config["user_id"]
        builder = graph.groups.by_group_id(config["leaf_id"]).member_of
        query = MemberOfRequestBuilder.MemberOfRequestBuilderGetQueryParameters(top=1)
        page = await builder.get(RequestConfiguration(query_parameters=query))
        rows, pages = list(page.value), 1
        while page.odata_next_link:
            assert page.odata_next_link.startswith("https://localhost:8443/")
            page = await builder.with_url(page.odata_next_link).get()
            rows.extend(page.value)
            pages += 1
        assert {row.id for row in rows} == set(config["group_ids"]) and pages == 3
        org = await graph.organization.get()
        assert org.value[0].city == "Qualification City"
        role = await graph.role_management.directory.role_definitions.by_unified_role_definition_id(
            "fe930be7-5e62-47db-91af-98c3a49a38b1"
        ).get()
        assert role.description and role.role_permissions[0].allowed_resource_actions
    Path("/state/python-report.json").write_text(
        json.dumps(
            {
                "client": "msgraph-sdk",
                "version": version("msgraph-sdk"),
                "mixed_objects": len(expected),
                "membership_pages": pages,
                "typed_organization": True,
                "typed_role_permissions": True,
            },
            indent=2,
        )
        + "\n"
    )


asyncio.run(main())
