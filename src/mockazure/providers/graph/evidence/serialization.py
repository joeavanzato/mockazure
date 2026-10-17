"""One SQL disclosure expression feeds both filtering and returned payloads."""

from sqlalchemy import and_, case, cast, exists, false, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from mockazure.evidence.models import EvidenceSource, EvidenceVersion
from mockazure.identity.models import User
from mockazure.licensing.catalog import P2_PLAN
from mockazure.licensing.models import SubscribedSku, UserLicense
from mockazure.providers.graph.evidence.contracts import public_fields
from mockazure.providers.graph.schema_reference import validate_payload


def effective_p2():
    assigned = exists(
        select(1)
        .select_from(UserLicense)
        .join(
            SubscribedSku,
            and_(
                SubscribedSku.tenant_id == UserLicense.tenant_id,
                SubscribedSku.sku_id == UserLicense.sku_id,
            ),
        )
        .where(
            UserLicense.tenant_id == EvidenceVersion.tenant_id,
            UserLicense.user_id == EvidenceVersion.subject_id,
            SubscribedSku.status == "Enabled",
            SubscribedSku.enabled > 0,
            ~UserLicense.disabled_plans.contains([str(P2_PLAN)]),
            SubscribedSku.service_plans.contains(
                [{"servicePlanId": str(P2_PLAN), "provisioningStatus": "Success"}]
            ),
        )
    )
    active = exists(
        select(1).where(
            User.tenant_id == EvidenceVersion.tenant_id,
            User.id == EvidenceVersion.subject_id,
            User.deleted_at.is_(None),
        )
    )
    historical = exists(
        select(1).where(
            EvidenceSource.tenant_id == EvidenceVersion.tenant_id,
            EvidenceSource.kind == EvidenceVersion.source_kind,
            EvidenceSource.id == EvidenceVersion.source_id,
            EvidenceSource.generation == EvidenceVersion.generation,
            EvidenceSource.kind == "historical",
            EvidenceSource.facts["entitlement_snapshot"].as_string() == "P2",
        )
    )
    return or_(and_(active, assigned), and_(~active, historical))


def disclosed_payload(family, access):
    raw = EvidenceVersion.payload
    if family == "directoryAudit":
        return raw
    detailed = effective_p2() if access.redaction == "P2" else false()
    if family == "signIn":
        # A v1.0 import can positively identify an interactive user event without
        # beta-only signInEventTypes. Normalize once for both filtering and output;
        # false/null cannot distinguish noninteractive users from workload identities.
        raw = cast(
            case(
                (
                    and_(~raw.has_key("signInEventTypes"), raw["isInteractive"].as_boolean()),
                    raw.op("||")(literal({"signInEventTypes": ["interactiveUser"]}, type_=JSONB)),
                ),
                else_=raw,
            ),
            JSONB,
        )
        event_types = raw["riskEventTypes_v2"]
        elements = (
            func.jsonb_array_elements_text(
                case(
                    (func.jsonb_typeof(event_types) == "array", event_types),
                    else_=literal([], type_=JSONB),
                )
            )
            .table_valued("value")
            .alias()
        )
        public_type = case(
            (elements.c.value.in_(["anonymizedIPAddress", "unfamiliarFeatures"]), "generic"),
            else_=elements.c.value,
        )
        masked_types = (
            select(func.coalesce(func.jsonb_agg(public_type), literal([], type_=JSONB)))
            .select_from(elements)
            .scalar_subquery()
        )
        additions = func.jsonb_build_object(
            "riskDetail",
            "hidden",
            "riskLevelAggregated",
            "hidden",
            "riskLevelDuringSignIn",
            "hidden",
            "riskEventTypes_v2",
            case((func.jsonb_typeof(event_types) == "array", masked_types), else_=event_types),
        )
    elif family == "riskDetection":
        premium = (
            raw["riskEventType"].as_string().in_(["anonymizedIPAddress", "unfamiliarFeatures"])
        )
        additions = func.jsonb_build_object(
            "riskEventType",
            case((premium, "generic"), else_=raw["riskEventType"].as_string()),
            "additionalInfo",
            case((premium, None), else_=raw["additionalInfo"].as_string()),
            "riskLevel",
            "hidden",
            "riskDetail",
            "hidden",
        )
    else:
        additions = func.jsonb_build_object("riskLevel", "hidden", "riskDetail", "hidden")
    return cast(case((detailed, raw), else_=raw.op("||")(additions)), JSONB)


def serialize_payload(version, family, payload, selected=None):
    fields = public_fields(version, family)
    if selected is not None:
        fields &= set(selected) | {"id"}
    result = {k: v for k, v in payload.items() if k in fields}
    if version == "beta" and family == "signIn" and "servicePrincipalId" in fields:
        result.setdefault("servicePrincipalId", "")
    result["@odata.type"] = "#microsoft.graph." + family
    validate_payload(version, family, result, partial=selected is not None)
    return result
