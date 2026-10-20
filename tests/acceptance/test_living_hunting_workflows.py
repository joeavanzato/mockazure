from datetime import UTC, datetime

import pytest

from mockazure.hunting.contracts import catalog
from mockazure.hunting.cookbook import cases, validate_case
from mockazure.hunting.wire import hunting_response
from mockazure.monitor.tables import instant
from mockazure.population.generators import generate
from mockazure.query.compiler import evaluate_query
from mockazure.query.parser import parse_query
from mockazure.query.types import QueryResult
from tests.unit.test_hunting_generation import PARAMS, REFS


def test_cookbook_covers_every_table_and_four_relationship_joins():
    examples = cases()
    assert set(catalog()) <= {row["id"] for row in examples}
    assert len([row for row in examples if row["id"].startswith("join-")]) == 4
    tables = {}
    for name, contract in catalog().items():
        values = generate("hunting." + name, PARAMS, REFS, offset=0, limit=20)
        columns = list(contract.columns.items())
        rows = [
            [
                instant(m.payload["record"][key])
                if kind == "datetime" and m.payload["record"].get(key) is not None
                else m.payload["record"].get(key)
                for key, kind in columns
            ]
            for m in values
        ]
        tables[name] = QueryResult(columns, rows)
    now = datetime(2026, 10, 7, 12, tzinfo=UTC)
    for example in examples:
        result = evaluate_query(parse_query(example["query"], "hunting"), tables, now)
        validate_case(example, hunting_response(result))
    with pytest.raises(ValueError):
        validate_case(examples[0], {"schema": [], "results": []})


def test_live_hunting_report_requires_every_client_and_scale_gate():
    import json
    from pathlib import Path

    from tools.living_hunting import validate

    report = json.loads(Path("work/living-hunting.json").read_text("utf-8"))
    validate(report)
    assert report["cases"]["powershell"]["cmdlet"] == "Start-MgSecurityHuntingQuery"
