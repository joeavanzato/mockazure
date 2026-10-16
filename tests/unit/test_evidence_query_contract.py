import pytest
from sqlalchemy.dialects import postgresql

from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.evidence.contracts import evidence_contract
from mockazure.providers.graph.evidence.query import compile_evidence_filter, parse_evidence_query


def test_sql_filters_match_original_mes_predicates():
    queries = [
        (
            "directoryAudit",
            "(activityDateTime ge 2026-10-01T00:00:00Z and "
            "startsWith(initiatedBy/user/userPrincipalName,'O''Brien%_λ')) or "
            "targetResources/any(t:t/id eq 'target' or t/userPrincipalName eq 'victim@test')",
        ),
        ("signIn", "signInEventTypes/any(t:t eq 'servicePrincipal' or t eq 'managedIdentity')"),
    ]
    for family, expression in queries:
        contract = evidence_contract("beta", family)
        query = parse_evidence_query([("$filter", expression)], contract)
        sql = compile_evidence_filter(query.filter, contract).compile(dialect=postgresql.dialect())
        assert "EXISTS" in str(sql)
        assert "victim@test" not in str(sql) and "managedIdentity" not in str(sql)
        if family == "directoryAudit":
            assert any("λ" in str(v) for v in sql.params.values())


@pytest.mark.parametrize(
    "expression",
    [
        "userId eq 'a\0b'",
        "(" * 17 + "id eq 'a'" + ")" * 17,
        " or ".join(["userId eq 'a'"] * 260),
        "status/errorCode eq " + "9" * 30,
        "status/errorCode eq 2147483648",
        "targetResources/any(t:t/password eq 'secret')",
        "targetResources/any(t:initiatedBy/user/id eq 'escape')",
        "userId in ('a')",
    ],
)
def test_invalid_filters_fail_closed(expression):
    family = "directoryAudit" if "targetResources" in expression else "signIn"
    with pytest.raises(GraphError) as exc:
        query = parse_evidence_query([("$filter", expression)], evidence_contract("v1.0", family))
        compile_evidence_filter(query.filter, evidence_contract("v1.0", family))
    assert exc.value.status == 400


@pytest.mark.parametrize(
    "family,option",
    [
        ("signIn", "$select"),
        ("directoryAudit", "$select"),
        ("riskyUser", "$orderby"),
        ("riskDetection", "$expand"),
        ("signIn", "$count"),
        ("signIn", "$skip"),
    ],
)
def test_route_specific_options(family, option):
    with pytest.raises(GraphError):
        parse_evidence_query([(option, "id")], evidence_contract("v1.0", family))
