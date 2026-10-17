import pytest

from mockazure.admin.routes import capabilities


@pytest.mark.asyncio
async def test_evidence_capabilities_name_implemented_surfaces():
    value = await capabilities()
    assert value["delivery"] == "arm"
    assert "managed_identity" in value["authentication_flows"]
    assert {
        "graph.auditLogs.signIns",
        "graph.auditLogs.directoryAudits",
        "graph.identityProtection.riskDetections",
        "graph.identityProtection.riskyUsers",
        "graph.identityProtection.riskyUsers.actions",
        "graph.licensing",
    } <= set(value["provider_operations"])
    assert {
        "evidence.import",
        "evidence.export",
        "evidence.reset",
        "evidence.jobs",
        "managed_identity.hosts",
    } <= set(value["admin_operations"])
    assert not any("no evidence collection" in text for text in value["limitations"])
