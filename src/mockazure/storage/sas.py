"""Version-pinned account SAS validation and operation restrictions."""

from datetime import datetime
from ipaddress import ip_address
from uuid import UUID

from sqlalchemy import select

from mockazure.providers.storage.errors import StorageError
from mockazure.storage.models import StorageDelegationKey, StorageNamespace
from mockazure.storage.signing import matches


def denied(code="AuthenticationFailed"):
    raise StorageError(403, code, "The request is not authorized by this signed access signature")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed


def check_constraints(args, request, now):
    try:
        if (
            not args.get("se")
            or timestamp(args["se"]) <= now
            or (args.get("st") and timestamp(args["st"]) > now)
        ):
            denied()
        if args.get("spr") and (
            args["spr"] not in {"https", "https,http"}
            or request.url.scheme not in args["spr"].split(",")
        ):
            denied("AuthorizationProtocolMismatch")
        if args.get("sip"):
            parts = args["sip"].split("-")
            if len(parts) > 2:
                denied()
            low, high = ip_address(parts[0]), ip_address(parts[-1])
            peer = ip_address(request.client.host if request.client else "")
            if (
                peer.version != low.version
                or low.version != high.version
                or not low <= peer <= high
            ):
                denied("AuthorizationSourceIPMismatch")
    except (ValueError, TypeError):
        denied()


def authorize_account_sas(keys, account, request, service, action, resource, now):
    pairs = request.query_params.multi_items()
    args = dict(pairs)
    if (
        len(args) != len(pairs)
        or args.get("sv") != "2025-11-05"
        or args.get("ses")
        or args.get("si")
    ):
        denied()
    value = (
        account
        + "\n"
        + "\n".join(
            args.get(k, "") for k in ("sp", "ss", "srt", "st", "se", "sip", "spr", "sv", "ses")
        )
        + "\n"
    )
    if not matches(keys, args.get("sig"), value.encode()):
        denied()
    check_constraints(args, request, now)
    if service[0] not in args.get("ss", ""):
        denied("AuthorizationServiceMismatch")
    level = "s" if not resource else "c" if "/" not in resource else "o"
    if level not in args.get("srt", ""):
        denied("AuthorizationResourceTypeMismatch")
    needed = {"create": "cw", "read": "r", "list": "l", "delete": "d", "metadata": "w"}.get(
        action, ""
    )
    if not needed or not set(needed) & set(args.get("sp", "")):
        denied("AuthorizationPermissionMismatch")
    return frozenset(args["sp"])


DATA_PERMISSIONS = {
    "read_blob": "r",
    "write_blob": "cw",
    "delete_blob": "d",
    "list_blobs": "l",
    "read_file": "r",
    "write_file": "cw",
    "delete_file": "d",
    "list_files": "l",
}


async def authorize_service_sas(session, keys, account, request, service, action, resource, now):
    args = dict(request.query_params.multi_items())
    if (
        len(args) != len(request.query_params.multi_items())
        or args.get("sv") != "2025-11-05"
        or any(
            args.get(k)
            for k in ("ses", "snapshot", "saoid", "suoid", "sdd", "skdutid", "sduoid", "srh", "srq")
        )
    ):
        denied()
    # Namespace management is account-SAS only. Service SAS grants content
    # operations on one signed container/share or one signed object.
    needed = DATA_PERMISSIONS.get(action)
    sr = args.get("sr")
    if not needed or sr not in ({"c", "b"} if service == "blob" else {"s", "f"}):
        denied("AuthorizationPermissionMismatch")
    signed_resource = resource.split("/")[0] if sr in {"c", "s"} else resource
    fields = [args.get(k, "") for k in ("sp", "st", "se")] + [
        "/" + service + "/" + account.name + "/" + signed_resource
    ]
    delegation = None
    if "skoid" in args:
        if (
            service != "blob"
            or args.get("si")
            or args.get("sks") != "b"
            or args.get("skv") != "2025-11-05"
            or args.get("sktid") != str(account.tenant_id)
        ):
            denied()
        try:
            delegation = await session.scalar(
                select(StorageDelegationKey).where(
                    StorageDelegationKey.tenant_id == account.tenant_id,
                    StorageDelegationKey.account_id == account.scope_key,
                    StorageDelegationKey.object_id == UUID(args["skoid"]),
                    StorageDelegationKey.starts_at == timestamp(args["skt"]),
                    StorageDelegationKey.expires_at == timestamp(args["ske"]),
                    StorageDelegationKey.version == args["skv"],
                )
            )
        except (ValueError, KeyError):
            denied()
        if delegation is None or delegation.starts_at > now or delegation.expires_at <= now:
            denied()
        from mockazure.storage.accounts import cipher

        keys = [cipher(session).decrypt(delegation.secret.encode()).decode()]
        fields += [
            args.get(k, "")
            for k in (
                "skoid",
                "sktid",
                "skt",
                "ske",
                "sks",
                "skv",
                "saoid",
                "suoid",
                "scid",
                "skdutid",
                "sduoid",
            )
        ]
    else:
        fields.append(args.get("si", ""))
    fields += [args.get(k, "") for k in ("sip", "spr", "sv")]
    if service == "blob":
        fields += [sr, "", args.get("ses", "")]
    fields += [args.get(k, "") for k in ("rscc", "rscd", "rsce", "rscl", "rsct")]
    if not matches(keys, args.get("sig"), "\n".join(fields).encode()):
        denied()
    effective = dict(args)
    if args.get("si"):
        namespace = await session.get(
            StorageNamespace,
            (account.tenant_id, account.scope_key, service, resource.split("/")[0]),
        )
        policy = (
            next((p for p in namespace.policies if p["id"] == args["si"]), None)
            if namespace
            else None
        )
        if policy is None:
            denied()
        for query, key in (("sp", "permission"), ("st", "start"), ("se", "expiry")):
            if args.get(query) and policy.get(key):
                denied()
            effective[query] = args.get(query) or policy.get(key, "")
    check_constraints(effective, request, now)
    if not set(needed) & set(effective.get("sp", "")):
        denied("AuthorizationPermissionMismatch")
    if delegation:
        from mockazure.storage.permissions import require_delegation_issuer

        if timestamp(effective["se"]) > delegation.expires_at:
            denied()
        await require_delegation_issuer(session, delegation, service, action, resource)
    return frozenset(effective["sp"]), bool(delegation)
