from datetime import UTC, datetime

import pytest

from mockazure.query.compiler import evaluate_query
from mockazure.query.parser import parse_query
from mockazure.query.types import QueryError, QueryLimits, QueryResult

NOW = datetime(2026, 10, 7, tzinfo=UTC)


def test_hunting_language_validates_empty_inputs_and_types():
    plan = parse_query("DeviceInfo | project NotAColumn", "hunting")
    with pytest.raises(QueryError):
        evaluate_query(plan, {"DeviceInfo": QueryResult([("DeviceId", "string")], [])}, NOW)
    plan = parse_query("DeviceInfo | where DeviceId > 3", "hunting")
    with pytest.raises(QueryError):
        evaluate_query(plan, {"DeviceInfo": QueryResult([("DeviceId", "string")], [])}, NOW)


def test_hunting_join_and_graph_wire_are_distinct_from_logs():
    from mockazure.hunting.wire import hunting_response

    plan = parse_query(
        "DeviceInfo | join kind=inner (DeviceEvents) on DeviceId | project DeviceId, ActionType",
        "hunting",
    )
    result = evaluate_query(
        plan,
        {
            "DeviceInfo": QueryResult([("DeviceId", "string")], [["d"]]),
            "DeviceEvents": QueryResult(
                [("DeviceId", "string"), ("ActionType", "string")], [["d", "test"]]
            ),
        },
        NOW,
    )
    wire = hunting_response(result)
    from mockazure.providers.graph.schema_reference import validate_payload

    validate_payload("v1.0", "security.huntingQueryResults", wire)
    assert wire == {
        "schema": [
            {"name": "DeviceId", "type": "String"},
            {"name": "ActionType", "type": "String"},
        ],
        "results": [{"DeviceId": "d", "ActionType": "test"}],
    }


def test_hunting_budget_never_returns_a_silent_partial_result():
    with pytest.raises(QueryError, match="limit"):
        evaluate_query(
            parse_query("DeviceInfo", "hunting"),
            {"DeviceInfo": QueryResult([("DeviceId", "string")], [["a"], ["b"]])},
            NOW,
            limits=QueryLimits(rows=1),
        )


def test_hunting_null_columns_have_a_valid_graph_type():
    from mockazure.hunting.wire import hunting_response

    result = evaluate_query(
        parse_query("DeviceInfo | project Missing=null", "hunting"),
        {"DeviceInfo": QueryResult([("DeviceId", "string")], [["a"]])},
        NOW,
    )
    assert hunting_response(result) == {
        "schema": [{"name": "Missing", "type": "Object"}],
        "results": [{"Missing": None}],
    }


async def test_hunting_execution_limits_are_clamped_and_do_not_leak(monkeypatch):
    from types import SimpleNamespace
    from uuid import uuid4

    from mockazure.hunting import service

    previous = {"rows": 7}
    session = SimpleNamespace(info={"query_limits": previous})
    actor = SimpleNamespace(tenant_id=uuid4())

    async def clock(*args):
        return NOW

    async def execute(current, *args):
        assert current.info["query_limits"] == {
            "seconds": 30,
            "rows": 100000,
            "bytes": 1,
            "expanded": 1000000,
        }
        raise QueryError("test bounded failure")

    monkeypatch.setattr(service, "evidence_now", clock)
    monkeypatch.setattr(service, "execute_query", execute)
    with pytest.raises(QueryError, match="test bounded failure"):
        await service.run_hunting(
            session,
            actor,
            {"Query": "DeviceInfo"},
            wall_now=NOW,
            limits=QueryLimits(seconds=1000, rows=200000, bytes=1),
        )
    assert session.info["query_limits"] is previous
