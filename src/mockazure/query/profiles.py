"""Service-specific published language restrictions."""

from .types import QueryError

PROFILES = {"resourcegraph", "loganalytics", "hunting"}
OPERATORS = {
    "where",
    "project",
    "project-away",
    "project-rename",
    "extend",
    "distinct",
    "order",
    "sort",
    "take",
    "limit",
    "top",
    "summarize",
    "union",
    "join",
    "mv-expand",
    "mvexpand",
    "count",
}
JOINS = {"innerunique", "inner", "leftouter", "fullouter", "leftanti"}


def validate_profile(profile, counts, flavor=None, expansion=None):
    if profile not in PROFILES:
        raise QueryError("Unknown query language profile")
    if flavor is not None and (
        flavor not in JOINS or profile == "resourcegraph" and flavor == "leftanti"
    ):
        raise QueryError("Unsupported join flavor for this service")
    if profile == "resourcegraph":
        if counts.get("join", 0) + counts.get("union", 0) > 3 or counts.get("mv-expand", 0) > 3:
            raise QueryError("Resource Graph operator limit exceeded")
        if expansion is not None and expansion > 2000:
            raise QueryError("Resource Graph expansion limit exceeds 2000")
