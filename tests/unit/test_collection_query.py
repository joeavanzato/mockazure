from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from mockazure.collections.contracts import validate_records
from mockazure.collections.query import compiled, contract, parse
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.query.parser import FilterParser


def test_numeric_filter_uses_bound_decimal_and_datetime_exact_ticks():
    for query in ["currentScore ge 12.5", "currentScore eq null"]:
        sql = compiled(FilterParser(query).parse(), contract("secureScores")).compile(
            dialect=postgresql.dialect()
        )
        assert "CAST" in str(sql)
    result = validate_records(
        "alerts_v2", [{"id": "a", "createdDateTime": "2026-10-01T00:00:00.0000001Z"}], uuid4()
    )
    assert result[0][1]["createdDateTime"] % 10 == 1


@pytest.mark.parametrize(
    "query",
    [
        {"$filter": "currentScore eq true"},
        {"$filter": "currentScore eq '12.5'"},
        {"$filter": "createdDateTime eq 'not-a-date'"},
        {"$select": "id"},
    ],
)
def test_scores_reject_unsupported_or_untyped_query(query):
    with pytest.raises(GraphError):
        parse(list(query.items()), "secureScores")


def test_legacy_skip_limit_and_selection():
    parse(
        [("$skip", "500"), ("$select", "id,severity"), ("$orderby", "createdDateTime desc")],
        "alerts",
    )
    with pytest.raises(GraphError):
        parse([("$skip", "501")], "alerts")


@pytest.mark.parametrize(
    "family,query",
    [
        ("alerts", "status ne 'resolved'"),
        ("alerts", "confidence ne 10"),
        ("alerts", "createdDateTime ne 2026-10-01T00:00:00.0000001Z"),
        ("secureScores", "currentScore ne 12.5"),
        ("secureScoreControlProfiles", "deprecated ne true"),
    ],
)
def test_non_null_inequality_is_null_safe(family, query):
    sql = compiled(FilterParser(query).parse(), contract(family)).compile(
        dialect=postgresql.dialect()
    )
    assert "IS DISTINCT FROM" in str(sql)
