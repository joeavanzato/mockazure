import pytest

from mockazure.providers.graph.errors import GraphError


def parser():
    from mockazure.providers.graph.query.contracts import contract_for
    from mockazure.providers.graph.query.parser import parse_query

    return parse_query, contract_for


@pytest.mark.parametrize(
    "query",
    [
        "displayName eq 'O''Brien'",
        "accountEnabled eq false",
        "mail eq null",
        "createdDateTime ge 2026-10-01T00:00:00Z",
        "id in ('00000000-0000-0000-0000-000000000001')",
        "(displayName eq '李' or startsWith(userPrincipalName,'analyst')) "
        "and accountEnabled eq true",
    ],
)
def test_typed_filters_parse_and_compile_to_bound_sql(query):
    from mockazure.providers.graph.query.compiler import compile_filter

    parse, contract_for = parser()
    contract = contract_for("v1.0", "users")
    spec = parse([("$filter", query)], contract, None)
    compiled = compile_filter(spec.filter, contract).compile()
    assert compiled.params or query == "accountEnabled eq false"
    assert "O'Brien" not in str(compiled)
    assert "李" not in str(compiled)


@pytest.mark.parametrize(
    "items",
    [
        [("$filter", "(" * 17 + "displayName eq 'x'" + ")" * 17)],
        [("$filter", " or ".join(["displayName eq 'x'"] * 100))],
        [("$filter", "id in (" + ",".join(["'x'"] * 101) + ")")],
        [("$filter", "displayName/evil eq 'x'")],
        [("$filter", "displayName eq 'x'"), ("$filter", "displayName eq 'y'")],
        [("$filter", "displayName eq 'x'; DROP TABLE identity_users")],
        [("$filter", "accountEnabled eq 'true'")],
        [("$select", "id,passwordProfile")],
        [("$expand", "members($expand=owners)")],
        [("$top", "0")],
        [("$skip", "1")],
    ],
)
def test_unsupported_and_excessive_queries_are_400(items):
    parse, contract_for = parser()
    with pytest.raises(GraphError) as error:
        parse(items, contract_for("v1.0", "users"), "eventual")
    assert error.value.status == 400


def test_collection_any_and_advanced_query_requirements():
    from mockazure.providers.graph.query.compiler import compile_filter

    parse, contract_for = parser()
    contract = contract_for("beta", "groups")
    spec = parse(
        [("$filter", "groupTypes/any(t: t eq 'Unified' or t eq 'DynamicMembership')")],
        contract,
        None,
    )
    compiled = compile_filter(spec.filter, contract).compile()
    assert "EXISTS" in str(compiled)
    assert "Unified" not in str(compiled)
    with pytest.raises(GraphError):
        parse([("$filter", "displayName eq 'x'"), ("$orderby", "displayName")], contract, None)
    spec = parse(
        [("$filter", "displayName eq 'x'"), ("$orderby", "displayName"), ("$count", "true")],
        contract,
        "eventual",
    )
    assert spec.count is True
    assert parse([("$count", "true")], contract, None).count is False


def test_not_equal_requires_advanced_headers_and_count():
    parse, contract_for = parser()
    contract = contract_for("v1.0", "users")
    for consistency, items in [
        (None, [("$filter", "displayName ne 'x'")]),
        ("eventual", [("$filter", "displayName ne 'x'")]),
    ]:
        with pytest.raises(GraphError) as error:
            parse(items, contract, consistency)
        assert error.value.status == 400
    assert parse(
        [("$filter", "displayName ne 'x'"), ("$count", "true")], contract, "eventual"
    ).count
