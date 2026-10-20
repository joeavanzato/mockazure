from mockazure.population.catalog import get_adapter
from mockazure.population.types import ResourceQuery
from tests.integration.test_population_adapters import context


async def test_remaining_service_families_have_bounded_safe_browsers(database, settings):
    ctx = await context(database, settings)
    families = [
        "directory.devices",
        "directory.applications",
        "directory.servicePrincipals",
        "identity.grants",
        "identity.directoryRoles",
        "identity.guestInvitations",
        "security.conditionalAccessPolicies",
        "security.namedLocations",
        "security.authenticationMethods",
        "security.authenticationPolicy",
        "mail.rules",
        "mail.delegations",
        "exchange.configuration",
        "exchange.configurationRecords",
        "exchange.permissions",
        "exchange.security",
        "exchange.auditSettings",
        "activity.subscriptions",
    ]
    for family in families:
        adapter = get_adapter(family)
        page = await adapter.browse(ctx, ResourceQuery(limit=1))
        assert len(page.items) <= 1
        assert adapter.describe().limitations
        operations = adapter.describe().operations
        if family == "directory.devices":
            assert [(op.key, op.mode) for op in operations] == [("import-owned", "fixture")]
        else:
            assert not operations


async def test_authentication_browser_never_exposes_secret_properties(database, settings):
    from uuid import uuid4

    from mockazure.security.models import SecurityObject

    ctx = await context(database, settings)
    identifier = uuid4()
    async with database.sessions.begin() as session:
        session.add(
            SecurityObject(
                tenant_id=ctx.tenant_id,
                id=identifier,
                kind="authenticationMethod",
                properties={
                    "id": str(identifier),
                    "displayName": "Test",
                    "secret": "DO-NOT-EXPOSE",
                    "verifier": "DO-NOT-EXPOSE",
                    "@odata.type": "#microsoft.graph.passwordAuthenticationMethod",
                },
            )
        )
    adapter = get_adapter("security.authenticationMethods")
    for query in (ResourceQuery(), ResourceQuery(identifier=str(identifier))):
        assert "DO-NOT-EXPOSE" not in (await adapter.browse(ctx, query)).model_dump_json()
