from datetime import UTC, datetime

from mockazure.auditlog.schemas import query_input


def test_original_mes_empty_optional_service_filter():
    # Original Get-UALGraph submits empty strings for unselected optional filters.
    value = query_input(
        {
            "@odata.type": "#microsoft.graph.security.auditLogQuery",
            "displayName": "Original MES",
            "serviceFilter": "",
            "status": "",
            "keywordFilter": "",
            "recordTypeFilters": [],
            "operationFilters": [],
            "ipAddressFilters": [],
            "userPrincipalNameFilters": [],
            "objectIdFilters": [],
            "administrativeUnitIdFilters": [],
        },
        datetime.now(UTC),
    )
    assert not value.get("serviceFilters")
    assert "status" not in value
