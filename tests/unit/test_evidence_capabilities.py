from types import SimpleNamespace

import pytest

from mockazure.admin.routes import capabilities
from mockazure.config import Settings


@pytest.mark.asyncio
async def test_evidence_capabilities_name_implemented_surfaces(tmp_path):
    value = await capabilities(
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    settings=Settings(data_dir=tmp_path, database_url="postgresql://localhost/unit")
                )
            )
        )
    )
    assert value["delivery"] == "collection-evidence"
    assert "managed_identity" in value["authentication_flows"]
    assert {
        "graph.security.alerts_v2.read",
        "graph.security.incidents.read",
        "graph.security.secureScores.read",
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
