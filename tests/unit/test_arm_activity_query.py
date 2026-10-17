import pytest

from mockazure.providers.arm.activity_query import compile_activity_filter, selected_fields
from mockazure.providers.arm.errors import ArmError


def test_activity_exact_filter_and_documented_selection():
    compiled = compile_activity_filter(
        "eventTimestamp ge '2026-10-01T00:00:00.0000001Z' and "
        "eventTimestamp le '2026-10-01T00:00:00.0000002Z' and resourceGroupName eq 'IR'"
    ).compile()
    assert "arm_event_ticks" in str(compiled)
    numbers = [value for value in compiled.params.values() if isinstance(value, int)]
    assert max(numbers) - min(numbers) == 1
    assert selected_fields("eventName,id,eventTimestamp") == ["eventName", "id", "eventTimestamp"]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "eventTimestamp ge '0001-01-01T00:00:00+01:00'",
        "eventTimestamp gt '2026-10-01T00:00:00Z'",
        "resourceGroupName eq 'IR'",
        "eventTimestamp ge '2026-10-01T00:00:00Z' or resourceGroupName eq 'IR'",
        "eventTimestamp ge '2026-10-01T00:00:00Z' and resourceGroupName eq 'IR' "
        "and correlationId eq 'x'",
        "eventTimestamp ge '2026-10-02T00:00:00Z' and eventTimestamp le '2026-10-01T00:00:00Z'",
        "eventTimestamp ge '2026-10-01T00:00:00.00000001Z'",
    ],
)
def test_activity_rejects_unsupported_filter(value):
    with pytest.raises(ArmError):
        compile_activity_filter(value)


@pytest.mark.parametrize("value", ["", "id,id", "*", "password", "category"])
def test_activity_rejects_unsupported_selection(value):
    with pytest.raises(ArmError):
        selected_fields(value)


def test_original_mes_utc_culture_filter_matches_iso():
    culture = compile_activity_filter(
        "eventTimestamp ge '10/07/2026 00:00:00' and eventTimestamp le '10/07/2026 23:59:59'"
    ).compile()
    iso = compile_activity_filter(
        "eventTimestamp ge '2026-10-07T00:00:00Z' and eventTimestamp le '2026-10-07T23:59:59Z'"
    ).compile()
    assert culture.params == iso.params


@pytest.mark.parametrize(
    "date",
    [
        "07/10/26 00:00:00",
        "13/07/2026 00:00:00",
        "02/30/2026 00:00:00",
        "10/07/2026 00:00:00+01:00",
        "10/07/2026",
        "10/07/2026 25:00:00",
    ],
)
def test_mes_culture_filter_rejects_unobserved_and_invalid_forms(date):
    with pytest.raises(ArmError):
        compile_activity_filter("eventTimestamp ge '" + date + "'")
