"""Packaged, bounded example queries shared by the console and qualification."""

import json
from importlib.resources import files


def cases():
    return json.loads(files("mockazure.hunting").joinpath("data/cookbook.json").read_text("utf-8"))


def validate_case(case, result):
    try:
        assert set(result) >= {"schema", "results"}
        assert "tables" not in result and "@odata.nextLink" not in result
        assert 1 <= len(result["results"]) <= 20
        assert {c["name"] for c in result["schema"]} == set(result["results"][0])
        for row in result["results"]:
            for key in case["required_values"]:
                assert row[key] not in (None, "")
            for key in case.get("positive_counts", []):
                assert type(row[key]) is int and row[key] > 0
    except (KeyError, TypeError, AssertionError):
        raise ValueError("Hunting cookbook assertion failed: " + case["id"]) from None
