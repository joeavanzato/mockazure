"""Pinned Activity Logs filter grammar, compiled against indexed exact-time columns."""

import re
from datetime import UTC, datetime

from sqlalchemy import and_

from mockazure.evidence.models import EvidenceVersion as V
from mockazure.providers.arm.activity import instant
from mockazure.providers.arm.query import invalid, parse_filter
from mockazure.providers.arm.scopes import parse_scope
from mockazure.providers.graph.query.ast import Boolean, Compare

# id is explicitly selected by the pinned GetActivityLogsFilteredAndSelected example.
SELECTABLE = frozenset(
    (
        "authorization claims correlationId description eventDataId eventName eventTimestamp "
        "httpRequest level operationId operationName properties resourceGroupName "
        "resourceProviderName "
        "resourceId status submissionTimestamp subStatus subscriptionId id"
    ).split()
)


def selected_fields(value):
    if value is None:
        return None
    fields = [field.strip() for field in value.split(",")]
    if not fields or len(fields) != len(set(fields)) or set(fields) - SELECTABLE:
        invalid("Unsupported Activity Logs selection")
    return fields


def compile_activity_filter(value):
    if not value:
        invalid("Activity Logs requires an eventTimestamp lower bound")
    node = parse_filter(value)
    terms = []

    def flatten(item):
        if isinstance(item, Boolean) and item.op == "and":
            for child in item.children:
                flatten(child)
        elif isinstance(item, Compare) and isinstance(item.right.value, str):
            terms.append(item)
        else:
            invalid("Unsupported Activity Logs filter grammar")

    flatten(node)
    times, extra, predicates = {}, [], []
    columns = {
        "resourceGroupName": V.arm_group_name,
        "resourceUri": V.arm_scope_key,
        "resourceProvider": V.arm_provider,
        "correlationId": V.arm_correlation_id,
    }
    for term in terms:
        name, op, raw = term.left.name, term.op, term.right.value
        if name == "eventTimestamp" and op in {"ge", "le"} and op not in times:
            try:
                # Pinned original MES interpolates en-US DateTime in its UTC process.
                # Keep this compatibility grammar at the query boundary only.
                if re.fullmatch(r"\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}", raw):
                    raw = (
                        datetime.strptime(raw, "%m/%d/%Y %H:%M:%S").replace(tzinfo=UTC).isoformat()
                    )
                ticks = instant(raw)[1]
            except ValueError:
                invalid("Invalid Activity Logs timestamp")
            times[op] = ticks
            predicates.append(
                V.arm_event_ticks >= ticks if op == "ge" else V.arm_event_ticks <= ticks
            )
        elif name in columns and op == "eq" and not extra:
            if not raw or len(raw.encode("utf-8")) > (2048 if name == "resourceUri" else 256):
                invalid("Invalid Activity Logs dimension")
            if name == "resourceUri":
                try:
                    raw = parse_scope(raw, allow_subscription_resource=True).key
                except ValueError:
                    invalid("Invalid Activity Logs resource URI")
            extra.append(name)
            predicates.append(columns[name] == raw.lower())
        else:
            invalid("Unsupported or repeated Activity Logs filter term")
    if "ge" not in times or times.get("le", times["ge"]) < times["ge"]:
        invalid("Activity Logs requires an ordered time range")
    return and_(*predicates)
