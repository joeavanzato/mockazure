from datetime import UTC, datetime, timedelta

import pytest

from mockazure.query.compiler import evaluate_query
from mockazure.query.parser import parse_query
from mockazure.query.types import QueryError, QueryResult

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)


def run(text, columns, rows, *, profile="loganalytics", other=None):
    tables = {"Resources": QueryResult(columns, rows, False)} | (other or {})
    return evaluate_query(parse_query(text, profile), tables, NOW)


def test_query_typed_semantics():
    result = run(
        "Resources | project bigger=n > 3, either=n > 3 or isnull(n), "
        "equal=n == int(null), unequal=n != int(null), missing=tostring(d.missing), "
        "empty=isempty(s), fallback=coalesce(s, 'fallback')",
        [("n", "long"), ("s", "string"), ("d", "dynamic")],
        [[5, "", {}], [None, "b", {"missing": None}]],
    )
    assert result.rows == [
        [True, True, False, True, "", True, "fallback"],
        [None, True, None, None, "", False, "b"],
    ]
    assert result.columns == [
        ("bigger", "bool"),
        ("either", "bool"),
        ("equal", "bool"),
        ("unequal", "bool"),
        ("missing", "string"),
        ("empty", "bool"),
        ("fallback", "string"),
    ]


def test_string_terms_and_case_sensitive_operators():
    result = run(
        "Resources | project term=s has 'admin', part=s contains 'admin', "
        "exact=s == 'ADMIN', folded=s =~ 'ADMIN', member=s in~ ('ADMIN', 'other'), "
        "prefix=s startswith 'AD', suffix=s endswith_cs 'min'",
        [("s", "string")],
        [["admin"], ["administrator"], ["group ADMIN"]],
    )
    assert result.rows == [
        [True, True, False, True, True, True, True],
        [False, True, False, False, False, True, False],
        [True, True, False, False, False, False, False],
    ]


def test_aggregation_nulls_groups_and_arg_max():
    result = run(
        "Resources | summarize total=count(), present=countif(isnotnull(n)), "
        "sum=sum(n), mean=avg(n), low=min(n), high=max(n), distinct=dcount(n) by group "
        "| order by group asc",
        [("group", "string"), ("n", "long")],
        [["a", 2], ["a", 4], ["a", None], ["b", None]],
    )
    assert result.rows == [["a", 3, 2, 6, 3.0, 2, 4, 2], ["b", 1, 0, 0, None, None, None, 0]]
    latest = run(
        "Resources | summarize arg_max(n, label) by group",
        [("group", "string"), ("n", "long"), ("label", "string")],
        [["a", 1, "old"], ["a", 2, "new"]],
    )
    assert latest.columns == [("group", "string"), ("n", "long"), ("label", "string")]
    assert latest.rows == [["a", 2, "new"]]


@pytest.mark.parametrize(
    ("flavor", "expected"),
    [
        ("inner", [[1, "a", 1, "R"], [1, "b", 1, "R"]]),
        ("innerunique", [[1, "a", 1, "R"]]),
        ("leftouter", [[1, "a", 1, "R"], [1, "b", 1, "R"], [2, "c", None, ""]]),
        ("fullouter", [[1, "a", 1, "R"], [1, "b", 1, "R"], [2, "c", None, ""], [None, "", 3, "S"]]),
        ("leftanti", [[2, "c"]]),
    ],
)
def test_join_flavors_preserve_duplicates_and_typed_missing(flavor, expected):
    result = run(
        f"Resources | join kind={flavor} (ResourceContainers) on id",
        [("id", "long"), ("name", "string")],
        [[1, "a"], [1, "b"], [2, "c"]],
        other={
            "ResourceContainers": QueryResult(
                [("id", "long"), ("name", "string")], [[1, "R"], [3, "S"]], False
            )
        },
    )
    assert result.rows == expected


def test_expansion_projection_union_and_table_let():
    result = run(
        "let selected = Resources | where n > 1; "
        "selected | mv-expand item=items | extend value=tolong(item) "
        "| project-rename key=n | project-away items, item "
        "| union (selected | project key=n, value=n) | distinct key,value | top 3 by value desc",
        [("n", "long"), ("items", "dynamic")],
        [[1, [9]], [2, [3, 4]], [3, []]],
    )
    assert result.rows == [[2, 4], [2, 3], [3, 3]]


def test_scalar_let_dates_and_bins():
    result = run(
        "let cutoff=ago(2h); Resources | where stamp >= cutoff "
        "| project bucket=bin(stamp,1h), difference=now()-stamp, "
        "label=strcat(toupper(substring(name,0,1)),tolower(substring(name,1)))",
        [("stamp", "datetime"), ("name", "string")],
        [
            [datetime(2026, 10, 19, 11, 15, tzinfo=UTC), "aBC"],
            [datetime(2026, 10, 18, tzinfo=UTC), "old"],
        ],
    )
    assert result.rows[0][0] == datetime(2026, 10, 19, 11, tzinfo=UTC)
    assert result.rows[0][1].total_seconds() == 2700
    assert result.rows[0][2] == "Abc"


@pytest.mark.parametrize(
    "text",
    [
        "Resources | evaluate python('bad')",
        "Resources; DROP TABLE arm_resources",
        "Resources | where __import__('os')",
        "Resources | where unknown(name)",
        "Resources | project unknownColumn",
        "Secrets | take 1",
    ],
)
def test_query_rejects_unknown(text):
    with pytest.raises(QueryError):
        run(text, [("name", "string")], [["safe"]])


@pytest.mark.parametrize(
    "text",
    [
        "let x=1; Resources | take x",
        "Resources | join kind=leftanti (ResourceContainers) on id",
        "Resources | mv-expand d limit 2001",
        "Resources | union Resources | union Resources | union Resources | union Resources",
        "Resources | mv-expand d | mv-expand d | mv-expand d | mv-expand d",
    ],
)
def test_resourcegraph_sourced_language_limits(text):
    with pytest.raises(QueryError):
        run(text, [("id", "long"), ("d", "dynamic")], [[1, [1]]], profile="resourcegraph")


@pytest.mark.parametrize(
    "text",
    [
        "Resources | where missing == 1",
        "Resources | extend bad=unknown(name)",
        "Resources | project missing",
        "Resources | summarize sum(name)",
        "Resources | where name + 1 > 0",
    ],
)
def test_semantic_binding_rejects_invalid_queries_even_on_empty_tables(text):
    with pytest.raises(QueryError):
        run(text, [("name", "string")], [])


def test_empty_aggregate_and_nullable_boolean_truth_tables():
    result = run("Resources | summarize n=count(), total=sum(n), mean=avg(n)", [("n", "long")], [])
    assert result.rows == [[0, 0, None]]
    boolean = run(
        "Resources | project yes=b or true, no=b and false, maybe=b and true, inverse=not(b)",
        [("b", "bool")],
        [[True], [False], [None]],
    )
    assert boolean.rows == [
        [True, False, True, False],
        [True, False, False, True],
        [True, False, None, None],
    ]


def test_dynamic_access_casts_and_invalid_values():
    result = run(
        "Resources | project parsed=parse_json(raw), bad=toint('oops'), "
        "value=tostring(d['odd key']), item=tolong(d.items[0]), "
        "decision=case(n>2,'large',n>0,'small','none'), length=strlen(raw)",
        [("raw", "string"), ("d", "dynamic"), ("n", "long")],
        [['{"x":1}', {"odd key": "value", "items": [5]}, 2]],
    )
    assert result.rows == [[{"x": 1}, None, "value", 5, "small", 7]]


def test_union_preserves_distinct_column_types():
    result = run(
        "Resources | union Other",
        [("value", "long")],
        [[1]],
        other={"Other": QueryResult([("value", "string")], [["one"]], False)},
    )
    assert result.columns == [("value_long", "long"), ("value_string", "string")]
    assert result.rows == [[1, ""], [None, "one"]]


@pytest.mark.parametrize(
    ("query", "limits"),
    [
        ("Resources", {"rows": 1}),
        ("Resources", {"bytes": 1}),
        ("Resources", {"seconds": 0}),
        ("Resources | mv-expand d", {"expanded": 1}),
        ("Resources | join kind=inner (Resources) on n", {"expanded": 1}),
    ],
)
def test_query_budgets_fail_explicitly(query, limits):
    from mockazure.query.types import QueryLimits

    with pytest.raises(QueryError) as caught:
        evaluate_query(
            parse_query(query, "loganalytics"),
            {
                "Resources": QueryResult(
                    [("n", "long"), ("d", "dynamic")], [[1, [1, 2]], [1, [3]]], False
                )
            },
            NOW,
            limits=QueryLimits(**limits),
        )
    assert caught.value.code == "QueryLimitExceeded"


def test_scalar_literal_cast_precision_and_integer_division():
    result = run(
        "Resources | project stamp=datetime(2026-10-19T12:00:00Z), "
        "duration=timespan(01:30:00), maximum=tolong('9223372036854775807'), "
        "negative=-5/2, invalid=parse_json('NaN')",
        [("id", "long")],
        [[1]],
    )
    assert result.rows[0] == [NOW, timedelta(minutes=90), 9223372036854775807, -2, "NaN"]


def test_numeric_result_types_and_nonnegative_modulo():
    result = run(
        "Resources | project sum=int(1)+int(2), a=-14%12, b=14%-12, c=-14%-12",
        [("id", "long")],
        [[1]],
    )
    assert result.columns == [("sum", "long"), ("a", "long"), ("b", "long"), ("c", "long")]
    assert result.rows == [[3, 10, 2, 10]]


def test_resourcegraph_default_table_and_repeated_remote_join_restriction():
    result = run(
        "where id == 1 | project id", [("id", "long")], [[1], [2]], profile="resourcegraph"
    )
    assert result.rows == [[1]]
    with pytest.raises(QueryError):
        parse_query(
            "Resources | join (ResourceContainers) on id | join (ResourceContainers) on id",
            "resourcegraph",
        )


def test_union_type_suffix_collision_preserves_every_column():
    result = run(
        "Resources | union Other",
        [("x", "long"), ("x_long", "long")],
        [[1, 9]],
        other={"Other": QueryResult([("x", "string")], [["one"]], False)},
    )
    assert result.columns == [("x_long1", "long"), ("x_long", "long"), ("x_string", "string")]
    assert result.rows == [[1, 9, ""], [None, None, "one"]]


def test_review_nullable_membership():
    result = run(
        "Resources | project yes=n in (int(null)), no=n !in (int(null))",
        [("n", "long")],
        [[None], [1]],
    )
    assert result.rows == [[None, None], [False, True]]
    assert run(
        "Resources | where n !in (int(null)) | count", [("n", "long")], [[None], [1]]
    ).rows == [[1]]


def test_review_integer_bin_preserves_long_precision():
    result = run(
        "Resources | project v=bin(n, 1), negative=bin(-n, 2)",
        [("n", "long")],
        [[9223372036854775807]],
    )
    assert result.rows == [[9223372036854775807, -9223372036854775808]]


def test_review_bare_logs_table_binding():
    from mockazure.monitor.logs import sources
    from mockazure.monitor.tables import catalog

    for name in catalog():
        plan = parse_query(f"let t={name}; let u=t; u | count", "loganalytics")
        assert sources(plan) == {name}
        assert evaluate_query(plan, {name: QueryResult([("n", "long")], [[1]])}, NOW).rows == [[1]]
    assert run("let n=1; let m=n; Resources | project v=m", [("n", "long")], [[2]]).rows == [[1]]


def test_review_logs_expansion_default_is_not_local_truncation():
    from mockazure.query.types import QueryLimits

    plan = parse_query("Resources | mv-expand d | summarize n=count()", "loganalytics")
    assert plan.nodes[0].operations[0].options[0] == 2147483647
    with pytest.raises(QueryError, match="limit"):
        evaluate_query(
            plan,
            {"Resources": QueryResult([("d", "dynamic")], [[[1, 2, 3, 4]]])},
            NOW,
            limits=QueryLimits(expanded=3),
        )
    limited = parse_query("Resources | mv-expand d limit 2 | summarize n=count()", "loganalytics")
    assert evaluate_query(
        limited,
        {"Resources": QueryResult([("d", "dynamic")], [[[1, 2, 3, 4]]])},
        NOW,
        limits=QueryLimits(expanded=3),
    ).rows == [[2]]


def test_review_empty_string_coalesce_preserves_type():
    assert run(
        "Resources | project s=coalesce('', ''), missing=isnull(coalesce('', ''))",
        [("n", "long")],
        [[1]],
    ).rows == [["", False]]
