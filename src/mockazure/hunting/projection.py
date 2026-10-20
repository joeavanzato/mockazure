"""Only sourced cloud audit outcomes become native hunting observations."""

from pydantic import BaseModel, ConfigDict, model_validator

from mockazure.evidence.projectors import project_source
from mockazure.hunting.schemas import validate_row

VERSION = "hunting-native-v1"


class HuntingProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    table: str
    source_id: str
    row: dict

    @model_validator(mode="after")
    def typed(self):
        validate_row(self.table, self.row)
        return self


def project_hunting(source):
    # These sources describe actual local cloud operations, not sensor telemetry.
    # Historical imports, authentication and directory queries have no native map.
    if source.ref.kind not in {"mail", "workload"}:
        return []
    result = []
    for item in project_source(source).items:
        if item.family != "auditLogRecord":
            continue
        value = item.payload
        raw = value["auditData"]["dynamicProperties"]
        row = {
            "Timestamp": source.occurred_at.isoformat(),
            "ActionType": value["operation"],
            "Application": value["service"],
            "ReportId": str(item.id),
            "AccountObjectId": value["userId"],
            "AccountId": value["userPrincipalName"] or value["userId"],
            "ObjectId": value["objectId"],
            "RawEventData": raw,
            "AdditionalFields": {"localAuditSource": str(source.ref.id)},
        }
        if value.get("clientIp"):
            row["IPAddress"] = value["clientIp"]
        result.append(
            HuntingProjection(
                table="CloudAppEvents",
                source_id=f"native:{source.generation}:{source.ref.kind}:{source.ref.id}:{item.id}",
                row=row,
            )
        )
    return result
