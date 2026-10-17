from datetime import timedelta

from sqlalchemy import event, select

from mockazure.evidence.models import EvidenceObservation, EvidenceVersion
from tests.integration.test_evidence_directory import drain
from tests.integration.test_graph_evidence_reads import publish, record
from tests.integration.test_graph_risk_actions import PATH, observe, risk_client


async def test_dismissal_reads_linked_facts_without_per_record_queries(database, settings):
    async with risk_client(database, settings) as (client, tenant, app, user, headers, now):
        when = now - timedelta(seconds=5)
        identifiers = set()
        for _ in range(20):
            payload = record("signIn", user, when)
            identifiers.add(payload["id"])
            await publish(database, tenant, "signIn", payload, when)
        await observe(database, tenant, user, when)
        await drain(database)
        queries = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().startswith("SELECT") and (
                "evidence_observations" in statement or "evidence_sources" in statement
            ):
                queries.append(statement)

        event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
        try:
            response = await client.post(
                PATH + "/dismiss", headers=headers, json={"userIds": [str(user)]}
            )
        finally:
            event.remove(database.engine.sync_engine, "before_cursor_execute", capture)
        assert response.status_code == 204, response.text
        # Source insertion checks and one joined linked-fact read remain bounded.
        assert len(queries) <= 4, f"{len(queries)} fact reads for 20 linked records"
        async with database.sessions() as session:
            current = list(
                await session.scalars(
                    select(EvidenceVersion).where(
                        EvidenceVersion.tenant_id == tenant,
                        EvidenceVersion.family == "signIn",
                        EvidenceVersion.valid_to.is_(None),
                    )
                )
            )
            originals = list(
                await session.scalars(
                    select(EvidenceObservation).where(
                        EvidenceObservation.tenant_id == tenant,
                        EvidenceObservation.family == "signIn",
                    )
                )
            )
        assert {str(row.record_id) for row in current} == identifiers
        assert all(row.payload["riskState"] == "dismissed" for row in current)
        assert all(row.payload["riskState"] == "atRisk" for row in originals)
