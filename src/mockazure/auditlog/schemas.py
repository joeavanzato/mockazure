"""Normalize operation aliases, then validate the pinned v1.0 CSDL."""

from datetime import datetime, timedelta

from mockazure.mail.contracts import validate_mail_payload
from mockazure.providers.graph.errors import GraphError
from mockazure.providers.graph.schema_reference import schema

READ_ONLY = {
    "id",
    "approximateReturnedRecordCount",
    "isRecordCountLimitExceeded",
    "recordCountLimit",
}


def query_input(body, evidence_time):
    try:
        if not isinstance(body, dict) or READ_ONLY & body.keys():
            raise ValueError("Read-only property")
        value = dict(body)
        # status is explicitly service-owned, including when the SDK submits its default.
        value.pop("status", None)
        if "serviceFilter" in value:
            singular = value.pop("serviceFilter")
            if not isinstance(singular, str):
                raise ValueError("Service filter requires text")
            if singular and "serviceFilters" in value and value["serviceFilters"] != [singular]:
                raise ValueError("Conflicting service filters")
            if singular:
                value["serviceFilters"] = [singular]
        if "recordTypeFilters" in value and isinstance(value["recordTypeFilters"], list):
            aliases = {
                name.casefold(): name
                for name in schema("v1.0")["types"]["security.auditLogRecordType"]["enum"]
            }
            value["recordTypeFilters"] = [
                aliases.get(item.casefold(), item) if isinstance(item, str) else item
                for item in value["recordTypeFilters"]
            ]
        value = validate_mail_payload("security.auditLogQuery", value, partial=True)
        value.pop("@odata.type", None)
        for key in list(value):
            if value[key] is None:
                del value[key]
        for key, item in value.items():
            if isinstance(item, list):
                if len(item) > 100 or any(
                    not isinstance(v, str) or not v or len(v) > 1024 for v in item
                ):
                    raise ValueError("Filter collection exceeds bounded profile")
                value[key] = list(dict.fromkeys(item))
            elif isinstance(item, str) and len(item) > 1024:
                raise ValueError("Query text exceeds bounded profile")
        value.setdefault("filterEndDateTime", evidence_time.isoformat())
        value.setdefault("filterStartDateTime", (evidence_time - timedelta(days=1)).isoformat())
        start, end = (
            datetime.fromisoformat(value[k].replace("Z", "+00:00"))
            for k in ("filterStartDateTime", "filterEndDateTime")
        )
        if start > end:
            raise ValueError("Start must precede end")
        value["filterStartDateTime"], value["filterEndDateTime"] = (
            start.isoformat(),
            end.isoformat(),
        )
        return value
    except (ValueError, TypeError, AttributeError, OverflowError):
        raise GraphError(400, "BadRequest", "Invalid or unsupported audit query input") from None
