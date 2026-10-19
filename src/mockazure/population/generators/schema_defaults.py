"""Synthetic required-field defaults for tests and local client qualification only."""

from mockazure.providers.graph.schema_reference import schema


def payload(kind, identifier, *, reference_time="2026-10-07T00:00:00Z", **values):
    """Explicit synthetic fixture defaults; never used by the runtime importer."""
    types = schema("v1.0")["types"]

    def default(kind):
        if kind.startswith("Collection("):
            return []
        if kind == "Edm.String":
            return "fixture"
        if kind == "Edm.Boolean":
            return False
        if kind in {"Edm.Int16", "Edm.Int32", "Edm.Int64", "Edm.Double"}:
            return 0
        if kind == "Edm.DateTimeOffset":
            return reference_time
        if kind == "Edm.Guid":
            return "00000000-0000-0000-0000-000000000000"
        if kind == "Edm.TimeOfDay":
            return "00:00:00"
        contract = types[kind.removeprefix("graph.")]
        if "enum" in contract:
            return contract["enum"][0]
        return {k: default(contract["properties"][k]["type"]) for k in contract["required"]}

    result = default(kind)
    return {**result, "id": identifier, "@odata.type": "#microsoft.graph." + kind, **values}
