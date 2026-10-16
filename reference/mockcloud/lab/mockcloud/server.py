"""Mock Microsoft cloud for the VTW lab and tests (M33): Entra token endpoint, Graph v1.0/beta, Exchange Online's
admin API, serving tools/synth/cloud.py's tenants.

It serves exactly what VTW calls: sign-ins (beta), directory audits, risk, the directory lists and objects, Graph
inbox rules, message trace on Graph beta /admin/exchange/tracing/messageTraces (plan ruling R14), audit search on
/security/auditLog/queries (v1.0, R16; beta answers the same), and Exchange's InvokeCommand for Get-InboxRule and
Get-Mailbox (R15). M34: the live blades' reads (users/{id}/transitiveMemberOf, users/{id}?$select=
licenseAssignmentStates, deviceManagement/managedDevices?$filter=userId, authentication/methods, app role
assignments, grants filtered on principalId; servicePrincipals|applications/{id} and their sub-resources;
identityProtection/riskyUsers/{id}), with POST /_mock/live switching a blade (VTW's blade ids) to forbidden or
licence-limited until POST /_mock/reset.

It checks what matters to VTW and nothing more: the tenant and client exist, the token's own signature, expiry,
audience and roles, the tenant's licences. A client assertion's signature is NOT verified; any client secret works.
Only client_credentials is served (VTW never uses delegated auth).

Faults, once per tenant until POST /_mock/reset: the first sign-ins request carrying a $skiptoken answers 429 with
Retry-After 1.5, the third answers 400 "skip token has expired". Audit-search queries read `running` twice, then
`succeeded`. Error texts live in ERRORS; keep them in step with docs/superpowers/notes/m33-cloud-research.md (fix the
mock there, never VTW to fit the mock).

Logging: the request line only (never headers, never bodies), so no token or assertion is printed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import operator
import os
import re
import ssl
import sys
import threading
import time
import traceback
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit

from tools.synth import cloud

JWT_BEARER = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
EXO_ROLE = "Exchange.ManageAsApp"
RESOURCE_PREFIX = {"graph.": "graph", "outlook.": "exchange", "management.": "arm"}
UAL_LIST_FILTERS = ("recordTypeFilters", "serviceFilters", "operationFilters", "userPrincipalNameFilters",
                    "ipAddressFilters", "objectIdFilters", "administrativeUnitIdFilters")
NEXTLINK_SAFE = "$',:()"          # kept literal in nextLinks, as Graph writes them; spaces become %20

# Provider error bodies, one place. Licence texts contain "licen", permission texts never do: VTW tells
# license_limited from forbidden by the provider's wording (plan Review Focus 4).
ERRORS = {
    "denied": (403, "Authorization_RequestDenied", "Insufficient privileges to complete the operation."),
    "no_p1": (403, "Authentication_RequestFromNonPremiumTenantOrB2CTenant",
              "Neither tenant is B2C or tenant doesn't have premium license"),
    "no_p2": (403, "Forbidden", "Your tenant is not licensed for this feature. Microsoft Entra ID P2 is required."),
    "no_audit_search": (403, "Forbidden", "Audit log search is not enabled: the tenant's licence does not include it."),
    "no_mailbox": (404, "MailboxNotEnabledForRESTAPI",
                   "The mailbox is either inactive, soft-deleted, or is hosted on-premise."),
    "not_found": (404, "Request_ResourceNotFound",
                  "Resource '{}' does not exist or one of its queried reference-property objects are not present."),
    "segment": (400, "BadRequest", "Resource not found for the segment '{}'."),
    "filter": (400, "BadRequest", "Invalid filter clause: {}"),
    "page_size": (400, "Request_BadRequest", "Invalid page size specified: '{}'. Must be between 1 and {} inclusive."),
    "count": (400, "Request_BadRequest", "$count requires the ConsistencyLevel header set to 'eventual'."),
    "skiptoken": (400, "BadRequest", "The skip token has expired or is invalid. Restart the request."),
    "throttled": (429, "TooManyRequests", "Too many requests. Retry after the indicated time."),
    "query_running": (400, "BadRequest", "The audit log query has not completed. Poll it until status is succeeded."),
    "bad_request": (400, "BadRequest", "{}"),
    "token_missing": (401, "InvalidAuthenticationToken", "Access token is empty."),
    "token_invalid": (401, "InvalidAuthenticationToken", "Access token validation failure. {}"),
    "exo_role": (403, "Forbidden", "The application is not assigned an Exchange Online management role."),
    "exo_cmdlet": (400, "BadRequest", "The term '{}' is not recognized as a name of a cmdlet."),
    "exo_not_found": (404, "NotFound", "The operation couldn't be performed because object '{}' couldn't be found."),
}


class HttpError(Exception):
    def __init__(self, status: int, body: dict, headers: dict | None = None):
        super().__init__(status)
        self.status, self.body, self.headers = status, body, headers or {}


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def gerr(name: str, *fmt, headers: dict | None = None) -> HttpError:
    status, code, msg = ERRORS[name]
    return HttpError(status, {"error": {"code": code, "message": msg.format(*fmt),
                                        "innerError": {"date": _now_iso(), "request-id": str(uuid.uuid4())}}}, headers)


def aad_err(status: int, error: str, code: int, text: str) -> HttpError:
    now = _now_iso()
    return HttpError(status, {"error": error, "error_codes": [code], "timestamp": now,
                              "trace_id": str(uuid.uuid4()), "correlation_id": str(uuid.uuid4()),
                              "error_description": f"AADSTS{code}: {text} Trace ID: {uuid.uuid4()} Timestamp: {now}"})


@dataclass
class Req:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]          # lower-cased names
    body: bytes
    base: str                        # scheme://host[:port] as the client addressed it
    m: re.Match | None = None


# --- OData $filter: the clauses VTW's collectors send, nothing more ----------------------------------------------
_TIME = r"(\d{4}-\d\d-\d\dT[\d:.]+(?:Z|[+-]\d\d:\d\d)?)"
_STR = r"'((?:[^']|'')*)'"
CLAUSES = (("cmp", re.compile(rf"(\w+)\s+(ge|gt|le|lt)\s+{_TIME}")),
           ("eq", re.compile(rf"(\w+)\s+eq\s+{_STR}")),
           ("eqb", re.compile(r"(\w+)\s+eq\s+(true|false)")),
           ("in", re.compile(rf"(\w+)\s+in\s+\(\s*({_STR}(?:\s*,\s*{_STR})*)\s*\)")),
           ("any", re.compile(rf"(\w+)/any\((\w+):\s*\2\s+eq\s+{_STR}\)")))
OPS = {"ge": operator.ge, "gt": operator.gt, "le": operator.le, "lt": operator.lt}


@dataclass
class Filter:
    preds: list = field(default_factory=list)
    lo: datetime | None = None
    hi: datetime | None = None
    any: dict[str, list[str]] = field(default_factory=dict)

    def __call__(self, r: dict) -> bool:
        return all(p(r) for p in self.preds)


def _split_and(text: str) -> list[str]:
    parts, buf, quoted, i = [], [], False, 0
    while i < len(text):
        c = text[i]
        if c == "'":
            quoted = not quoted                   # a doubled '' toggles twice: still inside the literal
        if not quoted and text[i:i + 5].lower() == " and ":
            parts.append("".join(buf))
            buf, i = [], i + 5
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def parse_filter(text: str) -> Filter:
    f = Filter()
    for raw in _split_and(text or ""):
        clause = raw
        while clause.startswith("(") and clause.endswith(")"):
            clause = clause[1:-1].strip()
        for kind, rx in CLAUSES:
            m = rx.fullmatch(clause)
            if m:
                break
        else:
            raise gerr("filter", raw)
        if kind == "cmp":
            name, op, val = m[1], m[2], cloud.parse_time(m[3])
            if op in ("ge", "gt"):
                f.lo = val if f.lo is None else max(f.lo, val)
            else:
                edge = val + timedelta(microseconds=1) if op == "le" else val
                f.hi = edge if f.hi is None else min(f.hi, edge)
            f.preds.append(lambda r, n=name, o=OPS[op], v=val: r.get(n) is not None and o(cloud.parse_time(r[n]), v))
        elif kind == "eq":
            name, val = m[1], m[2].replace("''", "'").lower()
            f.preds.append(lambda r, n=name, v=val: str(r.get(n) if r.get(n) is not None else "").lower() == v)
        elif kind == "eqb":
            f.preds.append(lambda r, n=m[1], v=m[2] == "true": r.get(n) is v)
        elif kind == "in":
            vals = {v.replace("''", "'").lower() for v in re.findall(_STR, m[2])}
            f.preds.append(lambda r, n=m[1], v=vals: str(r.get(n) if r.get(n) is not None else "").lower() in v)
        else:
            name, val = m[1], m[3].replace("''", "'")
            f.any.setdefault(name, []).append(val)
            f.preds.append(lambda r, n=name, v=val: v in (r.get(n) or []))
    return f


def _key(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def _skip_token(offset: int, key: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset, "k": key}).encode()).decode().rstrip("=")


def _skip_offset(tok: str, key: str) -> int:
    try:
        d = json.loads(base64.urlsafe_b64decode(tok + "=" * (-len(tok) % 4)))
        if d["k"] != key:
            raise ValueError
        return int(d["o"])
    except (ValueError, KeyError, TypeError):
        raise gerr("skiptoken") from None


def _resource_kind(resource: str) -> str | None:
    host = (urlsplit(resource).hostname or "").lower()
    return next((k for p, k in RESOURCE_PREFIX.items() if host.startswith(p)), None)


def _b64json(seg: str) -> dict | None:
    try:
        v = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        return v if isinstance(v, dict) else None
    except ValueError:
        return None


def _bare_ip(ip: str) -> str:
    return ip.rsplit(":", 1)[0] if ip.count(":") == 1 else ip


class Mock:
    LOGS = {  # path -> (time field, permissions (any of), licence SKU or None, licence error)
        "auditLogs/signIns": ("createdDateTime", ("AuditLog.Read.All",), "AAD_PREMIUM", "no_p1"),
        "auditLogs/directoryAudits": ("activityDateTime", ("AuditLog.Read.All",), None, None),
        "identityProtection/riskDetections": ("detectedDateTime", ("IdentityRiskEvent.Read.All",),
                                              "AAD_PREMIUM_P2", "no_p2"),
        "identityProtection/riskyUsers": ("riskLastUpdatedDateTime", ("IdentityRiskyUser.Read.All",),
                                          "AAD_PREMIUM_P2", "no_p2"),
    }
    DIRECTORY = {  # path -> permissions (any of)
        "users": ("User.Read.All", "Directory.Read.All"),
        "groups": ("Group.Read.All", "GroupMember.Read.All", "Directory.Read.All"),
        "devices": ("Device.Read.All", "Directory.Read.All"),
        "applications": ("Application.Read.All", "Directory.Read.All"),
        "servicePrincipals": ("Application.Read.All", "Directory.Read.All"),
        "oauth2PermissionGrants": ("DelegatedPermissionGrant.Read.All", "Directory.Read.All"),
        "roleManagement/directory/roleAssignments": ("RoleManagement.Read.Directory", "Directory.Read.All"),
        "identity/conditionalAccess/policies": ("Policy.Read.All",),
        "organization": ("Organization.Read.All", "Directory.Read.All"),
        "subscribedSkus": ("Organization.Read.All", "Directory.Read.All"),
    }
    OBJECTS = re.compile(r"(users|groups|devices|applications|servicePrincipals|oauth2PermissionGrants|"
                         r"identity/conditionalAccess/policies)/([^/]+)")
    RULES = re.compile(r"users/([^/]+)/mailFolders/inbox/messageRules")
    UAL = re.compile(r"security/auditLog/queries(?:/([^/]+)(/records)?)?")
    TRACE = "admin/exchange/tracing/messageTraces"
    TRACE_MAX = timedelta(days=10)
    # M34 live blades (plan Task 9, R39): the per-object sub-resources VTW reads, their permissions (any of), and the
    # blade id (VTW's, so POST /_mock/live names blades as VTW does) each path belongs to. The other blade reads are
    # served by existing routes: users/{id} and applications|servicePrincipals/{id} (with $select) by _object, the
    # grants filtered on principalId by _directory, riskDetections filtered on userId by _log; MANAGED is new.
    LIVE = re.compile(r"(users|servicePrincipals|applications)/([^/]+)/(transitiveMemberOf|authentication/methods|"
                      r"appRoleAssignments|oauth2PermissionGrants|appRoleAssignedTo|owners)")
    RISKY = re.compile(r"identityProtection/riskyUsers/([^/]+)")
    MANAGED = "deviceManagement/managedDevices"
    LIVE_PERMS = {
        "transitiveMemberOf": ("User.Read.All", "GroupMember.Read.All", "Directory.Read.All"),
        "authentication/methods": ("UserAuthenticationMethod.Read.All",),
        "appRoleAssignments": ("Application.Read.All", "Directory.Read.All"),
        "oauth2PermissionGrants": ("Application.Read.All", "DelegatedPermissionGrant.Read.All", "Directory.Read.All"),
        "appRoleAssignedTo": ("Application.Read.All", "Directory.Read.All"),
        "owners": ("Application.Read.All", "Directory.Read.All"),
    }
    FAMILY = {("users", "transitiveMemberOf"): "groups", ("users", "authentication/methods"): "auth_methods",
              ("users", "appRoleAssignments"): "app_access",
              ("servicePrincipals", "oauth2PermissionGrants"): "api_permissions",
              ("servicePrincipals", "appRoleAssignments"): "api_permissions",
              ("servicePrincipals", "appRoleAssignedTo"): "assignments",
              ("servicePrincipals", "owners"): "owners", ("applications", "owners"): "owners"}
    SELECT_FAMILY = {"users": "licences", "applications": "credentials", "servicePrincipals": "app_roles"}

    def __init__(self, seed: int = cloud.SEED, users: int | None = None, faults: bool = True, clock=time.time):
        self.tenants = {k: cloud.Tenant(spec, seed, users if k == "contoso" else None)
                        for k, spec in cloud.TENANTS.items()}
        self._lookup = {}
        for t in self.tenants.values():
            self._lookup[t.tid] = self._lookup[t.spec.domain.lower()] = t
        self._secret = hashlib.sha256(f"mockcloud:{seed}".encode()).digest()
        self.faults_on, self.clock = faults, clock
        self.lock = threading.Lock()
        self._results: dict = {}
        self._routes = [(m, re.compile(p), h) for m, p, h in (
            ("GET", r"/_mock/health", self._health),
            ("GET", r"/_mock/state", lambda req: (200, {}, self.state())),
            ("POST", r"/_mock/reset", self._reset),
            ("POST", r"/_mock/live", self._live_switch),
            ("GET", r"/common/discovery/instance", self._instance),
            ("GET", r"/(?P<tenant>[^/]+)/v2\.0/\.well-known/openid-configuration", self._openid),
            ("POST", r"/(?P<tenant>[^/]+)/oauth2/v2\.0/token", self._token),
            ("GET", r"/(?P<tenant>[^/]+)/adminconsent", self._consent),
            ("POST", r"/adminapi/(?:beta|v2\.0)/(?P<tenant>[^/]+)/InvokeCommand", self._exo),
            ("*", r"/(?P<v>v1\.0|beta)/(?P<rest>.+)", self._graph))]
        self.reset()

    # --- admin (lab only) --------------------------------------------------------------------------------------
    def reset(self) -> None:
        with self.lock:
            self.faults = {k: {"skiptoken_requests": 0, "throttled": 0, "expired_skiptoken": 0} for k in self.tenants}
            self.queries: dict[str, dict] = {}
            self.counts: Counter = Counter()
            self.live_forbid: set[str] = set()       # blade ids answering Authorization_RequestDenied (M34)
            self.live_licence: set[str] = set()      # blade ids answering the P2 licence error

    def state(self) -> dict:
        with self.lock:
            return {"faults": json.loads(json.dumps(self.faults)), "counts": dict(self.counts),
                    "queries": {q["id"]: q["status"] for q in self.queries.values()},
                    "live": {"forbid": sorted(self.live_forbid), "licence": sorted(self.live_licence)}}

    def _health(self, req: Req):
        return 200, {}, {"ok": True, "tenants": {k: {"tenant_id": t.tid, "domain": t.spec.domain, "users": t.n_users}
                                                 for k, t in self.tenants.items()}}

    def _reset(self, req: Req):
        self.reset()
        return 200, {}, {"ok": True}

    def _live_switch(self, req: Req):
        """M34: {"forbid": [blade id, ...], "licence": [blade id, ...]} replaces both lists (lab only)."""
        try:
            body = json.loads(req.body or b"{}")
            forbid, licence = set(body.get("forbid") or []), set(body.get("licence") or [])
        except (ValueError, TypeError, AttributeError):
            raise gerr("bad_request", 'Expected {"forbid": [blade, ...], "licence": [blade, ...]}.') from None
        with self.lock:
            self.live_forbid, self.live_licence = forbid, licence
        return 200, {}, {"ok": True, "forbid": sorted(forbid), "licence": sorted(licence)}

    # --- dispatch ----------------------------------------------------------------------------------------------
    def handle(self, req: Req) -> tuple[int, dict, object]:
        for method, rx, fn in self._routes:
            m = rx.fullmatch(req.path)
            if m and method in ("*", req.method):
                req.m = m
                with self.lock:
                    self.counts[getattr(fn, "__name__", "route")] += 1
                try:
                    return fn(req)
                except HttpError as e:
                    return e.status, e.headers, e.body
                except Exception as e:                      # a mock bug: answer loudly, never hang the client
                    traceback.print_exc()
                    return 500, {}, {"error": {"code": "MockError", "message": repr(e)}}
        return 404, {}, {"error": {"code": "NotFound",
                                   "message": f"mockcloud has no route for {req.method} {req.path}"}}

    def _tenant(self, ref: str) -> cloud.Tenant | None:
        return self._lookup.get(unquote(ref).lower())

    # --- login: discovery, token, consent ----------------------------------------------------------------------
    def _login_tenant(self, req: Req) -> cloud.Tenant:
        t = self._tenant(req.m["tenant"])
        if t is None:
            raise aad_err(400, "invalid_request", 90002,
                          f"Tenant '{req.m['tenant']}' not found. Check to make sure you have the correct tenant ID "
                          "and are signing into the correct cloud.")
        return t

    def _instance(self, req: Req):
        host = urlsplit(req.base).netloc
        return 200, {}, {"api-version": "1.1",
                         "tenant_discovery_endpoint": f"{req.base}/common/v2.0/.well-known/openid-configuration",
                         "metadata": [{"preferred_network": host, "preferred_cache": host, "aliases": [host]}]}

    def _openid(self, req: Req):
        t = self._login_tenant(req)
        b = f"{req.base}/{t.tid}"
        return 200, {}, {"token_endpoint": f"{b}/oauth2/v2.0/token",
                         "authorization_endpoint": f"{b}/oauth2/v2.0/authorize",
                         "issuer": f"{b}/v2.0", "jwks_uri": f"{b}/discovery/v2.0/keys",
                         "token_endpoint_auth_methods_supported": ["client_secret_post", "private_key_jwt"],
                         "response_types_supported": ["code", "id_token"], "subject_types_supported": ["pairwise"],
                         "id_token_signing_alg_values_supported": ["RS256"], "cloud_instance_name": "mockcloud"}

    def _consent(self, req: Req):
        t = self._login_tenant(req)
        cid = req.query.get("client_id", "")
        ok = t.app(cid) is not None
        text = "Admin consent recorded (mock) for" if ok else "Unknown application"
        page = (f"<!doctype html><title>mockcloud consent</title>"
                f"<p>{text} {html.escape(cid)} in {html.escape(t.spec.domain)}.</p>")
        return (200 if ok else 400), {"Content-Type": "text/html; charset=utf-8"}, page.encode()

    def _token(self, req: Req):
        t = self._login_tenant(req)
        form = dict(parse_qsl(req.body.decode("utf-8", "replace")))
        if form.get("grant_type") != "client_credentials":
            raise aad_err(400, "unsupported_grant_type", 70003, "The app requested an unsupported grant type "
                                                                "(this mock serves client_credentials only).")
        cid = form.get("client_id", "")
        if t.app(cid) is None:
            raise aad_err(400, "unauthorized_client", 700016,
                          f"Application with identifier '{cid}' was not found in the directory '{t.spec.domain}'.")
        app = t.app(cid)
        if form.get("client_assertion_type") == JWT_BEARER:
            parts = form.get("client_assertion", "").split(".")
            c = _b64json(parts[1]) if len(parts) == 3 else None       # the signature is NOT verified (lab mock)
            if c is None or c.get("iss") != cid or c.get("sub") != cid:
                raise aad_err(401, "invalid_client", 50027, "JWT token is invalid or malformed.")
        elif not form.get("client_secret"):
            raise aad_err(401, "invalid_client", 7000216, "'client_assertion', 'client_secret' or 'request' is "
                                                          "required for the 'client_credentials' grant type.")
        scope = form.get("scope", "")
        resource = scope.removesuffix("/.default")
        kind = _resource_kind(resource) if scope.endswith("/.default") else None
        if kind is None:
            raise aad_err(400, "invalid_resource", 500011,
                          f"The resource principal named {resource} was not found in the tenant named {t.spec.name}.")
        roles = {"graph": app.roles, "exchange": app.exchange_roles}.get(kind, ())
        now = int(self.clock())
        claims = {"aud": resource, "iss": f"https://sts.mockcloud/{t.tid}/", "tid": t.tid, "appid": cid,
                  "idtyp": "app", "oid": t.oid("sp", cid), "iat": now, "nbf": now, "exp": now + 3599, "mock_kind": kind}
        if roles:
            claims["roles"] = list(roles)
        return 200, {}, {"token_type": "Bearer", "expires_in": 3599, "ext_expires_in": 3599,
                         "access_token": self._sign(claims)}

    def _sign(self, claims: dict) -> str:
        seg = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")  # noqa: E731
        head = f"{seg({'alg': 'HS256', 'typ': 'JWT', 'kid': 'mockcloud'})}.{seg(claims)}"
        sig = base64.urlsafe_b64encode(hmac.new(self._secret, head.encode(), hashlib.sha256).digest()).decode()
        return f"{head}.{sig.rstrip('=')}"

    def _verify(self, tok: str) -> dict | None:
        parts = tok.split(".")
        if len(parts) != 3:
            return None
        sig = base64.urlsafe_b64encode(hmac.new(self._secret, f"{parts[0]}.{parts[1]}".encode(),
                                                hashlib.sha256).digest()).decode().rstrip("=")
        return _b64json(parts[1]) if hmac.compare_digest(sig, parts[2]) else None

    def _auth(self, req: Req, kind: str) -> tuple[cloud.Tenant, dict]:
        h = req.headers.get("authorization", "")
        if not h.lower().startswith("bearer ") or not h[7:].strip():
            raise gerr("token_missing")
        claims = self._verify(h[7:].strip())
        if claims is None:
            raise gerr("token_invalid", "Signature validation failed.")
        if claims["exp"] <= self.clock():
            raise gerr("token_invalid", "Lifetime validation failed, the token is expired.")
        if claims.get("mock_kind") != kind:
            raise gerr("token_invalid", "Invalid audience.")
        return self._lookup[claims["tid"]], claims

    @staticmethod
    def _need(claims: dict, *perms: str) -> None:
        if not set(claims.get("roles", ())) & set(perms):
            raise gerr("denied")

    def _result(self, key: str, build):
        with self.lock:
            hit = self._results.get(key)
        if hit is None:
            hit = build()
            with self.lock:
                if len(self._results) >= 128:
                    self._results.clear()
                self._results[key] = hit
        return hit

    def _fault(self, tkey: str) -> None:
        if not self.faults_on:
            return
        with self.lock:
            f = self.faults[tkey]
            f["skiptoken_requests"] += 1
            n = f["skiptoken_requests"]
            if n == 1:
                f["throttled"] += 1
            elif n == 3:
                f["expired_skiptoken"] += 1
        if n == 1:
            raise gerr("throttled", headers={"Retry-After": "1.5"})
        if n == 3:
            raise gerr("skiptoken")

    def _page(self, req: Req, items: list, key: str, *, default: int, maximum: int, fault: str | None = None,
              count: bool = False):
        raw = req.query.get("$top")
        try:
            top = default if raw is None else int(raw)
        except ValueError:
            raise gerr("page_size", raw, maximum) from None
        if not 1 <= top <= maximum:
            raise gerr("page_size", raw, maximum)
        offset = 0
        if "$skiptoken" in req.query:
            if fault:
                self._fault(fault)
            offset = _skip_offset(req.query["$skiptoken"], key)
        body = {"@odata.context": f"{req.base}/$metadata", "value": items[offset:offset + top]}
        if count:
            body["@odata.count"] = len(items)
        if offset + top < len(items):
            q = ({k: v for k, v in req.query.items() if k != "$skiptoken"}
                 | {"$skiptoken": _skip_token(offset + top, key)})
            body["@odata.nextLink"] = f"{req.base}{req.path}?{urlencode(q, quote_via=quote, safe=NEXTLINK_SAFE)}"
        return 200, {}, body

    # --- Graph -------------------------------------------------------------------------------------------------
    def _graph(self, req: Req):
        rest = "/".join(unquote(p) for p in req.m["rest"].split("/"))
        t, claims = self._auth(req, "graph")
        self._switch(self._family(rest, req))
        if (m := self.LIVE.fullmatch(rest)) and req.method == "GET":
            return self._live(req, t, claims, m[1], m[2], m[3])
        if (m := self.RISKY.fullmatch(rest)) and req.method == "GET":
            return self._risky_user(t, claims, m[1])
        if rest == self.MANAGED and req.method == "GET":
            return self._managed(req, t, claims)
        if rest == "$batch" and req.method == "POST":
            return self._batch(req)
        if rest in self.LOGS and req.method == "GET":
            return self._log(req, t, claims, rest)
        if rest in self.DIRECTORY and req.method == "GET":
            return self._directory(req, t, claims, rest)
        if (m := self.OBJECTS.fullmatch(rest)) and req.method == "GET":
            return self._object(req, t, claims, m[1], m[2])
        if (m := self.RULES.fullmatch(rest)) and req.method == "GET":
            return self._rules(t, claims, m[1])
        if m := self.UAL.fullmatch(rest):
            return self._ual(req, t, claims, m[1], bool(m[2]))
        if rest == self.TRACE and req.method == "GET":
            return self._trace(req, t, claims)
        raise gerr("segment", rest.split("/")[0])

    def _trace(self, req: Req, t: cloud.Tenant, claims: dict):
        """Message trace (R14): receivedDateTime ge/le, at most 10 days per request; no filter = the last 48 hours,
        which in the fixed synthetic data are its last two days. Newest first, then by id (stable for resumes)."""
        self._need(claims, "ExchangeMessageTrace.Read.All")
        text = req.query.get("$filter", "")
        flt = parse_filter(text)
        if text and (flt.lo is None or flt.hi is None):
            raise gerr("bad_request", "Filter receivedDateTime with both a ge and a le bound.")
        if flt.lo is not None and flt.hi - flt.lo > self.TRACE_MAX + timedelta(microseconds=1):
            raise gerr("bad_request", "The time span of a message trace query must be at most 10 days.")
        key = _key(t.key, self.TRACE, text)

        def build() -> list[dict]:
            span = cloud.days(flt.lo, flt.hi) if text else cloud.days()[-2:]
            rows = [r for d in span for r in t.message_trace(d) if flt(r)]
            rows.sort(key=lambda r: r["id"])
            rows.sort(key=lambda r: r["receivedDateTime"], reverse=True)
            return rows
        return self._page(req, self._result(key, build), key, default=1000, maximum=5000)

    def _log(self, req: Req, t: cloud.Tenant, claims: dict, path: str):
        tfield, perms, lic, lic_err = self.LOGS[path]
        self._need(claims, *perms)
        if lic and lic not in t.spec.licences:
            raise gerr(lic_err)
        text = req.query.get("$filter", "")
        flt = parse_filter(text)
        order = (req.query.get("$orderby") or f"{tfield} desc").split()
        if order[0] != tfield or len(order) > 2 or (len(order) == 2 and order[1] not in ("asc", "desc")):
            raise gerr("bad_request", f"Unsupported $orderby '{req.query.get('$orderby')}'.")
        desc = len(order) == 2 and order[1] == "desc"
        kinds = flt.any.get("signInEventTypes") or ["interactiveUser"]     # Graph's default: interactive only
        if path == "auditLogs/signIns" and (len(kinds) > 1 or kinds[0] not in cloud.SIGNIN_TYPES):
            raise gerr("bad_request", "Filter on exactly one known signInEventTypes value.")
        key = _key(t.key, path, text, desc)

        def build() -> list[dict]:
            if path == "identityProtection/riskyUsers":
                rows = t.risky_users()
            else:
                source = {"auditLogs/signIns": lambda d: t.signins(kinds[0], d),
                          "auditLogs/directoryAudits": t.directory_audits,
                          "identityProtection/riskDetections": t.risk_detections}[path]
                rows = [r for d in cloud.days(flt.lo, flt.hi) for r in source(d)]
            return sorted((r for r in rows if flt(r)), key=lambda r: (r[tfield], r["id"]), reverse=desc)

        audit = path.startswith("auditLogs")
        return self._page(req, self._result(key, build), key, default=1000 if audit else 100,
                          maximum=1000 if audit else 500, fault=t.key if path == "auditLogs/signIns" else None)

    def _directory(self, req: Req, t: cloud.Tenant, claims: dict, path: str):
        self._need(claims, *self.DIRECTORY[path])
        src = t.directory(path)
        if path in ("organization", "subscribedSkus"):
            return 200, {}, {"@odata.context": f"{req.base}/$metadata", "value": src}
        count = req.query.get("$count", "").lower() == "true"
        if count and req.headers.get("consistencylevel", "").lower() != "eventual":
            raise gerr("count")
        text = req.query.get("$filter", "")
        key = _key(t.key, path, text)
        flt = parse_filter(text)
        items = self._result(key, lambda: [r for r in src if flt(r)]) if text else src
        return self._page(req, items, key, default=100, maximum=999, count=count)

    def _object(self, req: Req, t: cloud.Tenant, claims: dict, coll: str, ref: str):
        self._need(claims, *self.DIRECTORY[coll])
        if coll == "users":
            i = t.user(ref)
            obj = t.users[i] if i is not None else None
            if obj is not None and "licenseAssignmentStates" in req.query.get("$select", ""):
                obj = {**obj, "licenseAssignmentStates": t.licence_states(i)}      # M34: the Licences blade
        else:
            obj = next((o for o in t.directory(coll) if o["id"] == ref), None)
        if obj is None:
            raise gerr("not_found", ref)
        return 200, {}, obj

    def _rules(self, t: cloud.Tenant, claims: dict, ref: str):
        self._need(claims, "MailboxSettings.Read")
        i = t.user(ref)
        if i is None:
            raise gerr("not_found", ref)
        if not t.mailbox[i]:
            raise gerr("no_mailbox")
        return 200, {}, {"value": [cloud.graph_rule(r) for r in t.inbox_rules(i) if not r["hidden"]]}

    # --- M34 live blades -----------------------------------------------------------------------------------------
    def _family(self, rest: str, req: Req) -> str | None:
        """The blade id a Graph path belongs to (POST /_mock/live). The risk-detections list counts as `risk` only
        with a userId filter, and the grants list as `app_access` only with a principalId filter (the blades'
        reads), never as the collectors read them; a single object read with $select is the blade that selects it
        (users: licences, applications: credentials, servicePrincipals: app_roles)."""
        flt = req.query.get("$filter", "")
        if m := self.LIVE.fullmatch(rest):
            return self.FAMILY.get((m[1], m[3]))
        if self.RISKY.fullmatch(rest) or (rest == "identityProtection/riskDetections" and "userId" in flt):
            return "risk"
        if rest == self.MANAGED:
            return "devices"
        if rest == "oauth2PermissionGrants" and "principalId" in flt:
            return "app_access"
        if (m := self.OBJECTS.fullmatch(rest)) and "$select" in req.query:
            if m[1] == "servicePrincipals" and "Credentials" in req.query["$select"]:
                return "sp_credentials"
            return self.SELECT_FAMILY.get(m[1])
        return None

    def _switch(self, family: str | None) -> None:
        with self.lock:
            forbid, licence = family in self.live_forbid, family in self.live_licence
        if forbid:
            raise gerr("denied")
        if licence:
            raise gerr("no_p2")

    def _live(self, req: Req, t: cloud.Tenant, claims: dict, coll: str, ref: str, seg: str):
        self._need(claims, *self.LIVE_PERMS[seg])
        if coll == "users":
            i = t.user(ref)
            if i is None:
                raise gerr("not_found", ref)
            read = {"transitiveMemberOf": t.member_of, "authentication/methods": t.auth_methods,
                    "appRoleAssignments": t.user_app_roles}.get(seg)
            if read is None:
                raise gerr("segment", seg)
            items = read(i)
        else:
            if next((o for o in t.directory(coll) if o["id"] == ref), None) is None:
                raise gerr("not_found", ref)
            read = ({"owners": lambda: t.owners(coll, ref)} if coll == "applications" else
                    {"oauth2PermissionGrants": lambda: t.sp_grants(ref), "appRoleAssignments": lambda: [],
                     "appRoleAssignedTo": lambda: t.sp_assigned_to(ref), "owners": lambda: t.owners(coll, ref)}
                    ).get(seg)
            if read is None:
                raise gerr("segment", seg)
            items = read()
        return self._page(req, items, _key(t.key, coll, ref, seg), default=100, maximum=999)

    def _managed(self, req: Req, t: cloud.Tenant, claims: dict):
        """Intune managed devices, filtered on one user (the Devices blade's only read); unfiltered: none."""
        self._need(claims, "DeviceManagementManagedDevices.Read.All")
        text = req.query.get("$filter", "").strip()
        m = re.fullmatch(r"userId eq '([^']*)'", text)
        i = t.user(m[1]) if m else None
        items = t.managed_devices(i) if i is not None else []
        return self._page(req, items, _key(t.key, self.MANAGED, text), default=100, maximum=1000)

    def _risky_user(self, t: cloud.Tenant, claims: dict, ref: str):
        self._need(claims, "IdentityRiskyUser.Read.All")
        if "AAD_PREMIUM_P2" not in t.spec.licences:
            raise gerr("no_p2")
        i = t.user(ref)
        row = t.risky_user(i) if i is not None else None
        if row is None:
            raise gerr("not_found", ref)
        return 200, {}, row

    def _ual(self, req: Req, t: cloud.Tenant, claims: dict, qid: str | None, records: bool):
        self._need(claims, "AuditLogsQuery.Read.All")
        if not t.spec.audit_search:
            raise gerr("no_audit_search")
        if qid is None and req.method == "POST":
            return self._ual_create(req, t)
        if qid is None and req.method == "GET":
            with self.lock:
                mine = [self._public(q) for q in self.queries.values() if q["_tenant"] == t.key]
            text = req.query.get("$filter", "")
            if text:
                flt = parse_filter(text)
                mine = [q for q in mine if flt(q)]
            return self._page(req, sorted(mine, key=lambda q: q["id"]), _key(t.key, "ual-list", text),
                              default=100, maximum=1000)
        q = self.queries.get(qid or "")
        if q is None or q["_tenant"] != t.key or req.method != "GET":
            raise gerr("not_found", qid)
        if not records:
            with self.lock:
                q["_polls"] += 1
                q["status"] = "succeeded" if q["_polls"] >= 3 else "running"
            return 200, {}, self._public(q)
        if q["status"] != "succeeded":
            raise gerr("query_running")
        key = _key(t.key, "ual", qid)
        return self._page(req, self._result(key, lambda: self._ual_records(t, q)), key, default=150, maximum=1000)

    def _ual_create(self, req: Req, t: cloud.Tenant):
        try:
            body = json.loads(req.body or b"{}")
        except ValueError:
            raise gerr("bad_request", "The request body is not valid JSON.") from None
        for k in ("displayName", "filterStartDateTime", "filterEndDateTime"):
            if not body.get(k):
                raise gerr("bad_request", f"{k} is required.")
        if cloud.parse_time(body["filterEndDateTime"]) <= cloud.parse_time(body["filterStartDateTime"]):
            raise gerr("bad_request", "filterEndDateTime must be after filterStartDateTime.")
        qid = str(uuid.uuid4())
        q = {"@odata.type": "#microsoft.graph.security.auditLogQuery", "id": qid, "displayName": body["displayName"],
             "filterStartDateTime": body["filterStartDateTime"], "filterEndDateTime": body["filterEndDateTime"],
             "keywordFilter": body.get("keywordFilter"), **{k: body.get(k) or [] for k in UAL_LIST_FILTERS},
             "status": "notStarted", "isRecordCountLimitExceeded": False, "_tenant": t.key, "_polls": 0}
        with self.lock:
            self.queries[qid] = q
        return 201, {"Location": f"{req.base}{req.path}/{qid}"}, self._public(q)

    @staticmethod
    def _public(q: dict) -> dict:
        return {k: v for k, v in q.items() if not k.startswith("_")}

    @staticmethod
    def _ual_records(t: cloud.Tenant, q: dict) -> list[dict]:
        lo, hi = cloud.parse_time(q["filterStartDateTime"]), cloud.parse_time(q["filterEndDateTime"])
        want = {k: {str(v).lower() for v in q.get(k) or []} for k in UAL_LIST_FILTERS}
        fields = (("userPrincipalNameFilters", "userPrincipalName"), ("operationFilters", "operation"),
                  ("recordTypeFilters", "auditLogRecordType"), ("serviceFilters", "service"),
                  ("objectIdFilters", "objectId"))
        kw = (q.get("keywordFilter") or "").lower()

        def ok(r: dict) -> bool:
            if not lo <= cloud.parse_time(r["createdDateTime"]) < hi:
                return False
            if any(want[k] and str(r.get(f) or "").lower() not in want[k] for k, f in fields):
                return False
            if want["ipAddressFilters"] and _bare_ip(r["clientIp"]).lower() not in want["ipAddressFilters"]:
                return False
            return not kw or kw in json.dumps(r).lower()

        return [r for d in cloud.days(lo, hi) for r in t.ual(d) if ok(r)]

    def _batch(self, req: Req):
        try:
            reqs = json.loads(req.body or b"{}")["requests"]
        except (ValueError, KeyError, TypeError):
            raise gerr("bad_request", "Invalid batch payload.") from None
        if not 1 <= len(reqs) <= 20:
            raise gerr("bad_request", "A batch holds 1 to 20 requests.")
        out = []
        for r in reqs:
            u = urlsplit(r.get("url", ""))
            path = u.path if u.path.startswith("/") else f"/{u.path}"
            sub = Req(str(r.get("method", "GET")).upper(), f"/{req.m['v']}{path}",
                      dict(parse_qsl(u.query, keep_blank_values=True)),
                      {**req.headers, **{k.lower(): v for k, v in (r.get("headers") or {}).items()}},
                      json.dumps(r["body"]).encode() if "body" in r else b"", req.base)
            status, headers, body = self.handle(sub)
            out.append({"id": r.get("id"), "status": status, "headers": headers, "body": body})
        return 200, {}, {"responses": out}

    # --- Exchange Online admin API (app-only) ------------------------------------------------------------------
    def _exo(self, req: Req):
        t = self._tenant(req.m["tenant"])
        if t is None:
            raise gerr("exo_not_found", req.m["tenant"])
        tt, claims = self._auth(req, "exchange")
        if tt is not t:
            raise gerr("token_invalid", "The token's tenant does not match the request.")
        if EXO_ROLE not in claims.get("roles", ()):
            raise gerr("denied")
        app = t.app(claims["appid"])
        if app is None or not app.exchange_rbac:
            raise gerr("exo_role")
        try:
            cmd = json.loads(req.body or b"{}")["CmdletInput"]
            name, params = cmd["CmdletName"], cmd.get("Parameters") or {}
        except (ValueError, KeyError, TypeError):
            raise gerr("bad_request", 'Expected {"CmdletInput": {"CmdletName": ..., "Parameters": {...}}}.') from None
        handler = {"Get-OrganizationConfig": self._exo_org, "Get-AcceptedDomain": self._exo_domains,
                   "Get-Mailbox": self._exo_mailbox, "Get-InboxRule": self._exo_rules}.get(name)   # trace: Graph (R14)
        if handler is None:
            raise gerr("exo_cmdlet", name)
        return handler(req, t, params)

    @staticmethod
    def _exo_out(req: Req, t: cloud.Tenant, value: list, next_link: str | None = None, warnings=()):
        body = {"@odata.context": f"{req.base}/adminapi/beta/{t.tid}/$metadata#Collection(Exchange.GenericHashTable)",
                "value": value}
        if next_link:
            body["@odata.nextLink"] = next_link
        if warnings:
            body["@adminapi.warnings"] = list(warnings)
        return 200, {}, body

    def _exo_org(self, req: Req, t: cloud.Tenant, p: dict):
        return self._exo_out(req, t, [{"Name": t.spec.domain, "DisplayName": t.spec.name, "Guid": t.tid}])

    def _exo_domains(self, req: Req, t: cloud.Tenant, p: dict):
        return self._exo_out(req, t, [{"DomainName": t.spec.domain, "DomainType": "Authoritative", "Default": True}])

    def _exo_mailbox(self, req: Req, t: cloud.Tenant, p: dict):
        rows = t.mailboxes()
        if p.get("Identity"):
            i = t.user(str(p["Identity"]))
            rows = [m for m in rows if i is not None and m["ExternalDirectoryObjectId"] == t.users[i]["id"]]
            if not rows:
                raise gerr("exo_not_found", p["Identity"])
        size = p.get("ResultSize", 1000)
        limit = len(rows) if str(size).lower() == "unlimited" else min(int(size), len(rows))
        key = _key(t.key, "exo-mailbox", p.get("Identity") or "", limit)
        offset = _skip_offset(req.query["$skiptoken"], key) if "$skiptoken" in req.query else 0
        end = min(offset + 1000, limit)
        nxt = f"{req.base}{req.path}?$skiptoken={_skip_token(end, key)}" if end < limit else None
        return self._exo_out(req, t, rows[offset:end], nxt)

    def _exo_rules(self, req: Req, t: cloud.Tenant, p: dict):
        ref = str(p.get("Mailbox") or p.get("Identity") or "")
        i = t.user(ref) if ref else None
        if i is None or not t.mailbox[i]:
            raise gerr("exo_not_found", ref)
        hidden = str(p.get("IncludeHidden", False)).lower() == "true"
        upn = t.users[i]["userPrincipalName"]
        return self._exo_out(req, t, [cloud.exo_rule(r, upn) for r in t.inbox_rules(i) if hidden or not r["hidden"]])


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mockcloud/1.0"

    def _serve(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        u = urlsplit(self.path)
        host = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
        req = Req(self.command, u.path, dict(parse_qsl(u.query, keep_blank_values=True)),
                  {k.lower(): v for k, v in self.headers.items()}, body, f"{self.server.scheme}://{host}")
        status, headers, payload = self.server.mock.handle(req)
        headers = dict(headers)
        ctype = headers.pop("Content-Type", "application/json; charset=utf-8")
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PATCH = do_DELETE = _serve

    def log_message(self, fmt, *args) -> None:     # the request line only: never headers (tokens) or bodies
        if not self.server.quiet:
            sys.stderr.write(f"mockcloud {self.address_string()} {fmt % args}\n")


def serve(mock: Mock, host: str = "0.0.0.0", port: int = 8443, cert_dir: str | None = None,
          quiet: bool = False) -> ThreadingHTTPServer:
    """A threaded server for the mock; TLS when cert_dir holds server.pem / server.key (certs.ensure)."""
    srv = ThreadingHTTPServer((host, port), _Handler)
    srv.daemon_threads = True
    srv.mock, srv.quiet, srv.scheme = mock, quiet, "http"
    if cert_dir:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(os.path.join(cert_dir, "server.pem"), os.path.join(cert_dir, "server.key"))
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        srv.scheme = "https"
    return srv
