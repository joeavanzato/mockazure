"""Synthetic Microsoft 365 / Entra tenants (VTW M33): the data behind the lab's mock provider (lab/mockcloud) and
its tests.

Deterministic for a seed: every object and log record derives from (seed, tenant, kind, day, index), so the mock
serves identical pages after a restart and a test can recompute what a collector must have received. Log records are
built per UTC day on first use and cached, in the shapes Graph (beta sign-ins and message trace, v1.0 elsewhere) and
Exchange Online's admin API (Get-InboxRule, Get-Mailbox) return them. The collectors add their envelope keys (_tenant,
_object_type, _client, _mailbox, _kind; plan ruling R13); nothing here does. Written for VTW: nothing is copied from
Microsoft samples or BECalm. Stdlib only (the mock runs it in the backend image, the tests in the backend venv).

Tenants (TENANTS): Contoso Lab (5,000 users by default; E5, Entra ID P1 + P2) holds the planted BEC. Fabrikam Lab
(200 users; P1 only, so risky users and risk detections are licence-limited; VTW's app has no Exchange management
role). Data days: 2026-10-01 .. 2026-10-08 UTC. PLANTS lists what is planted in Contoso on 2026-10-07.

    cd backend && uv run python ../tools/synth/cloud.py [--users 5000] [--seed 33]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

SEED = 33
NS = uuid.UUID("6f1c2a9e-33aa-4c5e-9d1b-0d2a7e3c5b33")          # uuid5 namespace of every synthetic id
FIRST_DAY, DAYS = date(2026, 10, 1), 8                           # data days 2026-10-01 .. 2026-10-08
PLANT_DAY = date(2026, 10, 7)
SIGNIN_TYPES = ("interactiveUser", "nonInteractiveUser", "servicePrincipal", "managedIdentity")
TOR_IP, TOR_ASN = "185.220.101.47", 60729        # a Tor exit and its ASN; Task 8's anonymizer_asn list holds it
EXTERNAL_FWD = "m.bowen.archive@proton-lab.example"
ALEX_FWD = "alex.w@gmail-lab.example"
FED_DOMAIN = "contoso-lab-sso.example"
MAIL_APP, MAIL_APP_ID = "Mail Sync Pro", "7d9e0a4b-2c5f-4e81-b3a6-90c1d2e3f4a5"
CORP_IP, CORP_ASN, CORP_LOC = "203.0.113.10", 7922, ("Seattle", "Washington", "US")
GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"            # public first-party application ids
EXO_APP_ID = "00000002-0000-0ff1-ce00-000000000000"
MESSAGE_TRACE_APP_ID = "8bd644d1-64a1-4d4b-ae52-2e0cbf64e373"    # message trace's first-party app (Task 1 note §3)
GA_ROLE = "62e90394-69f5-4237-9190-012177145e10"                  # Global Administrator role template
MICROSOFT_TENANT = "f8cdef31-a31e-4b4a-93e4-5f571e91255a"
FIRM_TENANT = "0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0"
MAIL_APP_TENANT = "5e4d3c2b-1a09-4f8e-9d7c-6b5a49382716"
# M34 plants (spec §15): one per detector of spec §6.3. The kit and MFA-denial values are what Task 2's Sigma lists
# hold (%aitm_kit_asn%, %aitm_kit_user_agents%, %mfa_denied_reasons%; tests/test_synth_cloud_plants.py checks it).
KIT_IP, KIT_ASN, KIT_UA = "66.42.98.17", 20473, "axios/1.7.2"
SPRAY_IP, SPRAY_ASN, SPRAY_UA = "103.251.167.10", 135377, "python-requests/2.32.3"
AITM_IP, AITM_ASN = "45.134.26.19", 9009
MFA_DENIED = "MFA denied; user declined the authentication"
# (city, state, country, latitude, longitude, IP, ASN): Graph's location.geoCoordinates for the distance detectors
LISBON = ("Lisbon", "Lisboa", "PT", 38.7223, -9.1393, "85.240.10.20", 3243)
SINGAPORE = ("Singapore", "Singapore", "SG", 1.3521, 103.8198, "119.74.33.20", 7473)
LONDON = ("London", "England", "GB", 51.5074, -0.1278, "81.2.69.160", 2856)
NEW_YORK = ("New York", "New York", "US", 40.7128, -74.0060, "24.29.99.10", 701)
SAO_PAULO = ("São Paulo", "São Paulo", "BR", -23.5505, -46.6333, "177.12.40.8", 28573)
ANDROID_UA = ("Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 "
              "Mobile Safari/537.36")
TOR_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
PASSWORD_METHOD = "28c10230-6103-485e-b985-444c60001490"           # Graph's fixed id of the password method
ONPREM_DOMAIN = "corp"        # the on-prem NetBIOS name: tools/synth/synth.py's ACME scenario logs on as CORP\<sam>

# Microsoft's application permission names the mock knows. Every permission a VTW collector declares must be one of
# these (test_mockcloud), so a typo in REGISTRY fails in a test, not at a client.
GRAPH_ROLES = ("AuditLog.Read.All", "AuditLogsQuery.Read.All", "Application.Read.All",
               "DelegatedPermissionGrant.Read.All", "Device.Read.All", "Directory.Read.All",
               "ExchangeMessageTrace.Read.All", "Group.Read.All", "GroupMember.Read.All", "IdentityRiskEvent.Read.All",
               "IdentityRiskyUser.Read.All", "MailboxSettings.Read", "Organization.Read.All", "Policy.Read.All",
               "RoleManagement.Read.Directory", "User.Read.All", "UserAuthenticationMethod.Read.All",
               "DeviceManagementManagedDevices.Read.All")
EXCHANGE_ROLES = ("Exchange.ManageAsApp",)


@dataclass(frozen=True)
class App:
    client_id: str
    name: str
    roles: tuple[str, ...]                     # Graph application permissions with admin consent
    exchange_roles: tuple[str, ...] = ()       # roles on the Exchange Online resource
    exchange_rbac: bool = False                # an Exchange management role is assigned to its service principal


@dataclass(frozen=True)
class TenantSpec:
    key: str
    name: str
    tenant_id: str
    domain: str
    users: int
    licences: tuple[str, ...]                  # skuPartNumber values
    apps: tuple[App, ...]
    audit_search: bool = True


FIRM_APP_ID = "f17a0c3e-5b8d-4a2f-9e61-c4d7b0a9e812"
_FIRM = App(FIRM_APP_ID, "VTW firm app", GRAPH_ROLES, EXCHANGE_ROLES, True)
TENANTS = {
    "contoso": TenantSpec(
        "contoso", "Contoso Lab", "3c1f8a52-0d4e-4b7a-9f13-6a2e5c7d0b11", "contoso-lab.onmicrosoft.com", 5000,
        ("SPE_E5", "AAD_PREMIUM", "AAD_PREMIUM_P2"),
        (App("5a0e3c1d-7b2f-4e8a-9c46-1d3f5e7a9b20", "VTW collector", GRAPH_ROLES, EXCHANGE_ROLES, True),
         App("9b4d2f60-1e3a-4c7b-8d05-6f2a1b3c4d5e", "VTW collector (awaiting consent)", ()),
         _FIRM)),
    "fabrikam": TenantSpec(
        "fabrikam", "Fabrikam Lab", "8d2b6e14-7f3a-4b9c-a1d2-e3f405162738", "fabrikam-lab.onmicrosoft.com", 200,
        ("SPE_E3", "AAD_PREMIUM"),
        (App("2e6c8a10-3d5f-4b7e-9a1c-0b2d4f6e8a13", "VTW collector", GRAPH_ROLES, EXCHANGE_ROLES, False),
         App(FIRM_APP_ID, "VTW firm app", GRAPH_ROLES, EXCHANGE_ROLES, False))),
}
SKU_IDS = {"SPE_E5": "06ebc4ee-1bb5-47dd-8120-11324bc54e06", "SPE_E3": "05e9a617-0261-4cee-bb44-138d3ef5d965",
           "AAD_PREMIUM": "078d2b04-f1bd-4111-bbd4-b4b1b354cef4",
           "AAD_PREMIUM_P2": "84a661c4-e949-4bd2-a560-ed7766fcaf2b"}
PLAN_IDS = {"AAD_PREMIUM": "41781fb2-bc02-4b7c-bd55-b576c07bb09d",
            "AAD_PREMIUM_P2": "eec0eb4f-6444-4f95-aba0-50c24d67f998"}

# The plants' people first, then odd UPNs (an apostrophe, non-ASCII) for the escaping checks: (given, surname, local).
SPECIAL = (("Megan", "Bowen", "megan.bowen"), ("Adele", "Vance", "adele.vance"), ("Alex", "Wilber", "alex.wilber"),
           ("Lee", "Gu", "lee.gu"), ("Sync", "Service", "svc-sync"), ("Patrick", "O'Neil", "patrick.o'neil"),
           ("Zoë", "Ångström", "zoë.ångström"),
           # M34's detector plants (tools/synth/README.md "M34 plants")
           ("Nestor", "Wilke", "nestor.wilke"), ("Joni", "Sherman", "joni.sherman"),
           ("Lidia", "Holloway", "lidia.holloway"), ("Isaiah", "Langer", "isaiah.langer"),
           ("Grady", "Archie", "grady.archie"), ("Pradeep", "Gupta", "pradeep.gupta"),
           ("Diego", "Siciliani", "diego.siciliani"), ("Miriam", "Graham", "miriam.graham"))
MEGAN, ADELE, ALEX, LEE, SVC = range(5)
TRAVEL, CONCURRENT, AITM, PUSH, KIT, RELAY, DOWNGRADE, RARE = range(7, 15)
SPRAYED = 12                       # users the password spray tries: the first random users after SPECIAL
# On-prem accounts of the synced plant users (M34 D14): the endpoint synth's ACME scenario logs these on
# (CORP\it.admin to the DC, CORP\svc_backup to the pivot, the SQL server and five workstations).
ONPREM_SAM = {ADELE: "it.admin", SVC: "svc_backup"}
ROLE_NAMES = {GA_ROLE: "Global Administrator", "fe930be7-5e62-47db-91af-98c3a49a38b1": "User Administrator",
              "29232cdf-9323-42fd-ade2-1d097af3e4de": "Exchange Administrator",
              "5d6b6bb7-de71-4623-b4af-96380a352509": "Security Reader"}
FIRST_NAMES = ("ava", "ben", "carla", "dmitri", "elena", "farah", "gita", "hugo", "ines", "jonas", "kenji", "lena",
               "marco", "nadia", "omar", "paula", "quinn", "rosa", "sami", "tara", "umar", "vera", "wen", "xavier",
               "yara", "zane", "aiko", "bruno", "chloe", "diego", "emma", "felix", "grace", "henry", "isla", "jack",
               "kira", "liam", "maya", "noah")
LAST_NAMES = ("adams", "baker", "chen", "diaz", "evans", "fischer", "garcia", "hughes", "ito", "jones", "khan",
              "lopez", "muller", "nguyen", "okafor", "patel", "quist", "rossi", "silva", "tanaka", "usman", "vargas",
              "weber", "xu", "young", "zimmer", "alvarez", "becker", "costa", "dubois", "eriksen", "fernandes",
              "gruber", "hansen", "ivanov", "jensen", "kowalski", "larsen", "moreau", "novak")
DEPTS = ("Finance", "Sales", "Engineering", "HR", "Legal", "Operations", "Marketing", "IT")
TITLES = ("Analyst", "Manager", "Engineer", "Specialist", "Director", "Coordinator", "Consultant")
# (appId, app, resource, resourceId, clientAppUsed): Microsoft first-party ids are public identifiers.
USER_APPS = (("4765445b-32c6-49b0-83e6-1d93765276ca", "OfficeHome", "Office 365 Exchange Online", EXO_APP_ID,
              "Browser"),
             ("1fec8e78-bce4-4aaf-ab1b-5451cc387264", "Microsoft Teams", "Microsoft Graph", GRAPH_APP_ID,
              "Mobile Apps and Desktop clients"),
             ("d3590ed6-52b3-4102-aeff-aad2292ab01c", "Microsoft Office", "Office 365 Exchange Online", EXO_APP_ID,
              "Mobile Apps and Desktop clients"),
             ("c44b4083-3bb0-49c1-b47d-974e53cbdf3c", "Azure Portal", "Windows Azure Service Management API",
              "797f4846-ba00-4fd7-ba43-dac1f8f63013", "Browser"))
FIRST_PARTY = (("Microsoft Graph", GRAPH_APP_ID), ("Office 365 Exchange Online", EXO_APP_ID),
               ("Message trace (first party)", MESSAGE_TRACE_APP_ID)) + tuple((a[1], a[0]) for a in USER_APPS)
HOME_ISPS = ((7922, ("Seattle", "Washington", "US")), (701, ("New York", "New York", "US")),
             (3320, ("Berlin", "Berlin", "DE")), (2856, ("London", "England", "GB")))
USER_AGENTS = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 "
               "Safari/537.36 Edg/129.0.0.0",
               "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 "
               "Safari/605.1.15",
               "Microsoft Office/16.0 (Windows NT 10.0; Microsoft Outlook 16.0.17928; Pro)",
               "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
               "Mobile/15E148")
FAIL = {0: None, 50057: "User account is disabled.", 50074: "Strong Authentication is required.",
        50126: "Error validating credentials due to invalid username or password.",
        50140: "This error occurred due to 'Keep me signed in' interrupt when the user was signing in.",
        53003: "Access has been blocked by Conditional Access policies.",
        500121: "Authentication failed during strong authentication request."}
ERR_CODES, ERR_WEIGHTS = (0, 50126, 50074, 50140, 53003), (94, 3, 1.5, 1, 0.5)
RATES = {"interactiveUser": 0.8, "nonInteractiveUser": 1.2, "servicePrincipal": 0.08, "managedIdentity": 0.016}
AUDIT_BACKGROUND = (("UserManagement", "Update user", "Update", "User"),
                    ("GroupManagement", "Add member to group", "Assign", "Group"),
                    ("Device", "Update device", "Update", "Device"),
                    ("UserManagement", "Change user password", "Update", "User"),
                    ("ApplicationManagement", "Update application", "Update", "Application"))
# (operation, service, Graph auditLogRecordType, UAL RecordType number, weight)
UAL_BACKGROUND = (("MailItemsAccessed", "Exchange", "exchangeItemAggregated", 50, 50),
                  ("UserLoggedIn", "AzureActiveDirectory", "azureActiveDirectoryStsLogon", 15, 15),
                  ("FileAccessed", "SharePoint", "sharePointFileOperation", 6, 15),
                  ("FileModified", "OneDrive", "sharePointFileOperation", 6, 8),
                  ("Send", "Exchange", "exchangeItem", 2, 12))
CA_POLICIES = (("Require MFA for all users", "enabled"), ("Block legacy authentication", "enabled"),
               ("Require compliant device for admins", "enabled"), ("Sign-in risk: require MFA", "enabled"),
               ("Block countries outside allow-list", "enabledForReportingButNotEnforced"),
               ("Require MFA for Azure management", "enabled"), ("Guest access terms of use", "disabled"),
               ("Session lifetime 12h for unmanaged", "enabled"))
ROLES = ((GA_ROLE, 2), ("fe930be7-5e62-47db-91af-98c3a49a38b1", 3), ("29232cdf-9323-42fd-ade2-1d097af3e4de", 2),
         ("5d6b6bb7-de71-4623-b4af-96380a352509", 3))
SUBJECTS = ("Quarterly report", "Meeting notes", "Re: proposal", "Invoice", "Lunch?", "Project update", "Contract")

# What is planted in Contoso on 2026-10-07, by plant name (log records and the grant have the id
# Tenant.oid("plant", name[, index]); rules and mailboxes keep their directory ids), the collector that reads it and
# the starter rule (Task 8's title, content/detections/cloud/) it exists for.
FWD_RULE = "Inbox rule forwards or redirects mail outside the organisation"
PLANTS = (
    {"name": "tor sign-in", "collector": "Azure.SignIns", "rule": "Sign-in from an anonymizing network"},
    {"name": "tor session", "collector": "Azure.SignIns", "rule": "Sign-in from an anonymizing network", "count": 6},
    {"name": "legacy auth", "collector": "Azure.SignIns", "rule": "Sign-in with a legacy authentication protocol"},
    {"name": "boundary twin", "collector": "Azure.SignIns", "rule": None, "count": 2},
    {"name": "oauth consent", "collector": "Azure.DirectoryAudits", "rule": None},
    {"name": "grant", "collector": "Azure.Directory",
     "rule": "OAuth grant with mail scopes to an unverified application"},
    {"name": "sp credential", "collector": "Azure.DirectoryAudits",
     "rule": "Credential added to a service principal or application"},
    {"name": "global admin", "collector": "Azure.DirectoryAudits", "rule": "Global Administrator assigned outside PIM"},
    {"name": "ca disabled", "collector": "Azure.DirectoryAudits",
     "rule": "Conditional access policy disabled or deleted"},
    {"name": "federated domain", "collector": "Azure.DirectoryAudits",
     "rule": "Domain federated or federation settings changed"},
    {"name": "guest invited", "collector": "Azure.DirectoryAudits", "rule": "External guest invited"},
    {"name": "inbox forwarding rule", "collector": "Azure.InboxRules", "rule": FWD_RULE},
    {"name": "hidden inbox rule", "collector": "Azure.ExoMailbox", "rule": FWD_RULE},
    {"name": "mailbox forwarding", "collector": "Azure.ExoMailbox", "rule": None},
    {"name": "ual new-inboxrule", "collector": "Azure.UAL", "rule": None},
    {"name": "ual set-mailbox", "collector": "Azure.UAL", "rule": "Mailbox forwarding set with Set-Mailbox"},
    {"name": "ual app mail access", "collector": "Azure.UAL", "rule": None, "count": 40},
    {"name": "risky megan", "collector": "Azure.RiskyUsers", "rule": None},
    {"name": "risk anonymized ip", "collector": "Azure.RiskDetections", "rule": None},
    {"name": "forwarded mail", "collector": "Azure.MessageTrace", "rule": None, "count": 12},
    # M34 (spec §15): "detector" is the rule key or hunt template id of spec §6.3 the plant exists for
    {"name": "travel pair", "collector": "Azure.SignIns", "rule": None, "detector": "impossible-travel", "count": 2},
    {"name": "concurrent sessions", "collector": "Azure.SignIns", "rule": None, "detector": "concurrent-sessions",
     "count": 2},
    {"name": "aitm", "collector": "Azure.SignIns", "rule": None, "detector": "aitm-failure-then-success",
     "count": 2},
    {"name": "push bombing", "collector": "Azure.SignIns", "rule": None, "detector": "mfa-push-bombing", "count": 7},
    {"name": "kit sign-in", "collector": "Azure.SignIns", "rule": None, "detector": "aitm-kit-fingerprint"},
    {"name": "kit security info", "collector": "Azure.DirectoryAudits", "rule": None,
     "detector": "persistence-after-signal"},
    {"name": "kit consent", "collector": "Azure.DirectoryAudits", "rule": None,
     "detector": "persistence-after-signal"},
    {"name": "relay hop", "collector": "Azure.SignIns", "rule": None, "detector": "session-asn-relay", "count": 3},
    {"name": "ual relay inbox rule", "collector": "Azure.UAL", "rule": None, "detector": None},
    {"name": "spray", "collector": "Azure.SignIns", "rule": None, "detector": "password-spray", "count": SPRAYED},
    {"name": "downgrade", "collector": "Azure.SignIns", "rule": None, "detector": "mfa-downgrade", "count": 31},
    {"name": "rare country", "collector": "Azure.SignIns", "rule": None, "detector": "rare-signin-country",
     "count": 25},
)


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(s: str) -> datetime:
    d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def days(lo: datetime | None = None, hi: datetime | None = None) -> list[date]:
    """The data days a half-open window [lo, hi) touches (all of them when unbounded)."""
    first, last = FIRST_DAY, FIRST_DAY + timedelta(days=DAYS - 1)
    a = max(first, lo.astimezone(UTC).date()) if lo else first
    b = min(last, (hi - timedelta(microseconds=1)).astimezone(UTC).date()) if hi else last
    return [a + timedelta(days=k) for k in range((b - a).days + 1)] if a <= b else []


def _uti(rid: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(rid.encode()).digest()[:16]).decode().rstrip("=")


def graph_rule(r: dict) -> dict:
    """A rule as Graph's messageRule (Graph never sees hidden rules: the caller filters them)."""
    return {"id": r["id"], "displayName": r["name"], "sequence": r["priority"], "isEnabled": r["enabled"],
            "hasError": False, "isReadOnly": False, "conditions": {"subjectContains": r["words"]}, "exceptions": None,
            "actions": {"forwardTo": [{"emailAddress": {"name": a, "address": a}} for a in r["forward"]],
                        "moveToFolder": r["move"], "markAsRead": r["read"], "delete": r["delete"],
                        "stopProcessingRules": r["stop"]}}


def exo_rule(r: dict, upn: str) -> dict:
    """A rule as Exchange's Get-InboxRule returns it."""
    return {"Name": r["name"], "Identity": f"{upn}\\{r['id']}", "RuleIdentity": str(uuid.UUID(r["id"]).int % 10**19),
            "Enabled": r["enabled"], "Priority": r["priority"], "MailboxOwnerId": upn, "From": None,
            "SubjectContainsWords": r["words"], "ForwardTo": [f'"{a}" [SMTP:{a}]' for a in r["forward"]] or None,
            "ForwardAsAttachmentTo": None, "RedirectTo": None, "DeleteMessage": r["delete"],
            "MoveToFolder": r["move"], "MarkAsRead": r["read"], "StopProcessingRules": r["stop"]}


class Tenant:
    """One synthetic tenant: the directory is built at once, log records per (kind, day) on first use."""

    def __init__(self, spec: TenantSpec, seed: int = SEED, users: int | None = None):
        self.spec, self.seed, self.key, self.tid = spec, seed, spec.key, spec.tenant_id
        self.n_users = max(len(SPECIAL), users or spec.users)
        self.plants = spec.key == "contoso"
        self._cache: dict = {}
        self._lock = threading.Lock()
        self.users = self._users()
        self._index: dict[str, int] = {}
        for i, u in enumerate(self.users):
            for k in (u["id"], u["userPrincipalName"], u["mail"]):
                self._index[k.lower()] = i
        self.mailbox = [i < len(SPECIAL) or (u["accountEnabled"] and u["userType"] == "Member"
                                             and self._rng("mailbox", i).random() < 0.7)
                        for i, u in enumerate(self.users)]
        self._directory = self._build_directory()

    # --- helpers -----------------------------------------------------------------------------------------------
    def oid(self, *parts) -> str:
        """A stable object id for (seed, tenant, *parts)."""
        return str(uuid.uuid5(NS, ":".join(map(str, (self.seed, self.key, *parts)))))

    def _rng(self, *parts) -> random.Random:
        return random.Random(":".join(map(str, (self.seed, self.key, *parts))))

    def _cached(self, key, build):
        hit = self._cache.get(key)
        if hit is None:
            hit = build()
            with self._lock:
                if len(self._cache) >= 96:
                    self._cache.clear()
                self._cache[key] = hit
        return hit

    def user(self, ref: str) -> int | None:
        """A user's index by object id, UPN or mail (case-insensitive)."""
        return self._index.get(ref.lower())

    def app(self, client_id: str) -> App | None:
        return next((a for a in self.spec.apps if a.client_id == client_id.lower()), None)

    def directory(self, path: str) -> list[dict]:
        return self._directory[path]

    def _home(self, i: int):
        r = self._rng("home", i)
        asn, loc = r.choice(HOME_ISPS)
        h = r.randrange(1 << 17)
        return f"198.{18 + (h >> 16)}.{(h >> 8) & 255}.{h & 255}", asn, loc, r.choice(USER_AGENTS), r.random() < 0.8

    # --- directory ---------------------------------------------------------------------------------------------
    def _users(self) -> list[dict]:
        rng, out, seen = self._rng("users"), [], set()
        born = datetime(2023, 1, 1, tzinfo=UTC)
        for i in range(self.n_users):
            if i < len(SPECIAL):
                given, sur, local = SPECIAL[i]
            else:
                given, sur = rng.choice(FIRST_NAMES).title(), rng.choice(LAST_NAMES).title()
                local = f"{given}.{sur}".lower()
            stem, n = local, 1
            while local in seen:
                n += 1
                local = f"{stem}{n}"
            seen.add(local)
            guest = i >= len(SPECIAL) and rng.random() < 0.02
            mail = f"{local}@partner-lab.example" if guest else f"{local}@{self.spec.domain}"
            u = {"id": self.oid("user", i), "displayName": f"{given} {sur}", "givenName": given,
                 "surname": sur, "mail": mail, "userType": "Guest" if guest else "Member",
                 "userPrincipalName": f"{local}_partner-lab.example#EXT#@{self.spec.domain}" if guest else mail,
                 "accountEnabled": i < len(SPECIAL) or rng.random() >= 0.03,
                 "createdDateTime": iso(born + timedelta(minutes=rng.randrange(800_000))),
                 "department": rng.choice(DEPTS), "jobTitle": rng.choice(TITLES),
                 "onPremisesSyncEnabled": rng.random() < 0.6}
            u["onPremisesSyncEnabled"] = u["onPremisesSyncEnabled"] or i in ONPREM_SAM   # same draws, then forced
            u.update(self._onprem(i, given, sur, u["onPremisesSyncEnabled"]))
            out.append(u)
        return out

    def _onprem(self, i: int, given: str, sur: str, synced: bool) -> dict:
        """A synced user's on-prem identity (Graph's onPremises* properties; M34 D14): a SID in the tenant's
        domain, the SAM account name (ONPREM_SAM, else initial + surname), the DNS domain and the on-prem UPN."""
        if not synced:
            return dict.fromkeys(("onPremisesSecurityIdentifier", "onPremisesSamAccountName", "onPremisesDomainName",
                                  "onPremisesUserPrincipalName"))
        h = hashlib.sha256(f"{self.seed}:{self.key}:domain".encode()).digest()
        sid = "S-1-5-21-" + "-".join(str(int.from_bytes(h[k:k + 4], "big")) for k in (0, 4, 8))
        sam = ONPREM_SAM.get(i) or "".join(c for c in (given[:1] + sur).lower() if c.isascii() and c.isalnum())[:20]
        dom = f"{ONPREM_DOMAIN}.{self.key}-lab.example"
        return {"onPremisesSecurityIdentifier": f"{sid}-{1100 + i}", "onPremisesSamAccountName": sam,
                "onPremisesDomainName": dom, "onPremisesUserPrincipalName": f"{sam}@{dom}"}

    def _build_directory(self) -> dict[str, list[dict]]:
        rng, s = self._rng("directory"), self.spec
        t0 = datetime(2024, 3, 1, tzinfo=UTC)

        def when() -> str:
            return iso(t0 + timedelta(minutes=rng.randrange(900_000)))

        groups = []
        for g in range(max(10, self.n_users // 15)):
            unified = rng.random() < 0.5
            groups.append({"id": self.oid("group", g), "displayName": f"{rng.choice(DEPTS)} team {g}",
                           "mailEnabled": unified, "securityEnabled": not unified,
                           "groupTypes": ["Unified"] if unified else [], "visibility": "Private",
                           "mail": f"team{g}@{s.domain}" if unified else None, "createdDateTime": when()})
        devices = []
        for k in range(int(self.n_users * 0.8)):
            managed = rng.random() < 0.85
            devices.append({"id": self.oid("device", k), "deviceId": self.oid("device-id", k),
                            "displayName": f"{s.key[:3].upper()}-{k:05d}", "operatingSystem": "Windows",
                            "operatingSystemVersion": "10.0.22631", "trustType": "AzureAd" if managed else "Workplace",
                            "isCompliant": managed, "isManaged": managed, "accountEnabled": True,
                            "registrationDateTime": when(),
                            "approximateLastSignInDateTime": iso(datetime(2026, 10, 1, tzinfo=UTC)
                                                                 + timedelta(minutes=rng.randrange(DAYS * 1440)))})
        own = [a for a in s.apps if a.client_id != FIRM_APP_ID]
        applications = [{"id": self.oid("application", a.client_id), "appId": a.client_id, "displayName": a.name,
                         "signInAudience": "AzureADMyOrg", "publisherDomain": s.domain,
                         "createdDateTime": "2026-10-05T09:00:00Z", "keyCredentials": [], "passwordCredentials": []}
                        for a in own]
        for k in range(30):
            creds = ([{"keyId": self.oid("lob-key", k), "displayName": "deploy", "endDateTime": "2027-06-30T00:00:00Z"}]
                     if rng.random() < 0.5 else [])
            applications.append({"id": self.oid("application", k), "appId": self.oid("appid", k),
                                 "displayName": f"LOB app {k:02d}", "signInAudience": "AzureADMyOrg",
                                 "publisherDomain": s.domain, "createdDateTime": when(), "keyCredentials": [],
                                 "passwordCredentials": creds})

        def sp(app_id: str, name: str, owner: str, kind: str = "Application", **extra) -> dict:
            return {"id": self.oid("sp", app_id), "appId": app_id, "displayName": name, "accountEnabled": True,
                    "appOwnerOrganizationId": owner, "servicePrincipalType": kind, "publisherName": None,
                    "verifiedPublisher": {"displayName": None, "verifiedPublisherId": None, "addedDateTime": None},
                    "createdDateTime": "2025-01-15T10:00:00Z", "passwordCredentials": [], "keyCredentials": [], **extra}

        microsoft = {"displayName": "Microsoft Corporation", "verifiedPublisherId": "MSFT",
                     "addedDateTime": "2019-01-01T00:00:00Z"}
        sps = [sp(a, n, MICROSOFT_TENANT, publisherName="Microsoft Services", verifiedPublisher=microsoft)
               for n, a in FIRST_PARTY]
        self._graph_sp = sps[0]
        self._app_sps = [sp(a["appId"], a["displayName"], self.tid) for a in applications]
        self._mi_sps = [sp(self.oid("mi-app", k), f"mi-func-{k}", MICROSOFT_TENANT, "ManagedIdentity")
                        for k in range(8)]
        sps += self._app_sps + self._mi_sps + [sp(FIRM_APP_ID, "VTW firm app", FIRM_TENANT)]
        if self.plants:
            self._mail_sp = sp(MAIL_APP_ID, MAIL_APP, MAIL_APP_TENANT, publisherName="Mail Sync Pro Ltd",
                               createdDateTime="2026-10-07T03:30:09Z",
                               passwordCredentials=[{"keyId": self.oid("plant", "sp key"), "displayName": "sync",
                                                     "startDateTime": "2026-10-07T03:41:27Z",
                                                     "endDateTime": "2028-10-07T03:41:27Z"}])
            sps.append(self._mail_sp)
        grants = []
        for g in range(max(5, self.n_users // 40)):
            principal = rng.random() < 0.5
            grants.append({"id": self.oid("grant", g), "clientId": rng.choice(self._app_sps)["id"],
                           "consentType": "Principal" if principal else "AllPrincipals",
                           "principalId": self.users[rng.randrange(self.n_users)]["id"] if principal else None,
                           "resourceId": self._graph_sp["id"],
                           "scope": rng.choice(("User.Read", "openid profile User.Read", "Files.Read.All"))})
        if self.plants:
            self._plant_grant = {"id": self.oid("plant", "grant"), "clientId": self._mail_sp["id"],
                                 "consentType": "Principal", "principalId": self.users[MEGAN]["id"],
                                 "resourceId": self._graph_sp["id"], "scope": "Mail.Read Mail.Send offline_access"}
            grants.append(self._plant_grant)
        assignments = []
        for role, n in ROLES:
            holders = [ADELE] if role == GA_ROLE else []
            holders += [rng.randrange(len(SPECIAL), self.n_users) for _ in range(n - len(holders))]
            if role == GA_ROLE and self.plants:
                holders.append(SVC)
            assignments += [{"id": self.oid("ra", role, h), "principalId": self.users[h]["id"],
                             "roleDefinitionId": role, "directoryScopeId": "/"} for h in holders]
        policies = []
        for k, (name, state) in enumerate(CA_POLICIES):
            tampered = self.plants and k == 0
            policies.append({"id": self.oid("ca", k), "displayName": name, "state": "disabled" if tampered else state,
                             "createdDateTime": "2025-02-01T12:00:00Z",
                             "modifiedDateTime": "2026-10-07T04:20:00Z" if tampered else "2025-02-01T12:00:00Z",
                             "conditions": {"users": {"includeUsers": ["All"], "excludeUsers": []},
                                            "applications": {"includeApplications": ["All"]},
                                            "clientAppTypes": ["all"]},
                             "grantControls": {"operator": "OR", "builtInControls": ["mfa"]}})
        self._ca_mfa = policies[0]
        domains = [{"name": s.domain, "isDefault": True, "isInitial": True, "type": "Managed"}]
        if self.plants:
            domains.append({"name": FED_DOMAIN, "isDefault": False, "isInitial": False, "type": "Federated"})
        organization = [{"id": self.tid, "displayName": s.name, "tenantType": "AAD", "verifiedDomains": domains,
                         "createdDateTime": "2019-05-01T00:00:00Z",
                         "assignedPlans": [{"capabilityStatus": "Enabled", "service": "AADPremiumService",
                                            "servicePlanId": PLAN_IDS[x], "assignedDateTime": "2025-01-01T00:00:00Z"}
                                           for x in s.licences if x in PLAN_IDS]}]
        skus = [{"id": f"{self.tid}_{SKU_IDS[x]}", "skuId": SKU_IDS[x], "skuPartNumber": x, "appliesTo": "User",
                 "capabilityStatus": "Enabled", "consumedUnits": self.n_users,
                 "prepaidUnits": {"enabled": self.n_users}}
                for x in s.licences]
        return {"users": self.users, "groups": groups, "devices": devices, "applications": applications,
                "servicePrincipals": sps, "oauth2PermissionGrants": grants,
                "roleManagement/directory/roleAssignments": assignments,
                "identity/conditionalAccess/policies": policies, "organization": organization,
                "subscribedSkus": skus}

    # --- sign-ins ----------------------------------------------------------------------------------------------
    def signins(self, kind: str, day: date) -> list[dict]:
        """One signInEventTypes value's sign-ins on one day, ascending by (createdDateTime, id)."""
        return self._cached(("signin", kind, day), lambda: self._signins(kind, day))

    def _signins(self, kind: str, day: date) -> list[dict]:
        rng, start, rows = self._rng("signin", kind, day), datetime.combine(day, time(), UTC), []
        for i in range(max(1, round(self.n_users * RATES[kind]))):
            t, rid = start + timedelta(seconds=rng.randrange(86_400)), self.oid("signin", kind, day, i)
            if kind in ("servicePrincipal", "managedIdentity"):
                pool = self._mi_sps if kind == "managedIdentity" else self._app_sps
                rows.append(self._sp_signin(kind, rid, t, rng.choice(pool), rng))
            else:
                rows.append(self._user_signin(kind, rid, t, rng.randrange(self.n_users), rng))
        if self.plants and day == PLANT_DAY:
            rows += [r for r in self._plant_signins() if r["signInEventTypes"] == [kind]]
        if self.plants:
            rows += [r for r in self._m34_signins(day) if r["signInEventTypes"] == [kind]]
        rows.sort(key=lambda r: (r["createdDateTime"], r["id"]))
        return rows

    def _m34_signins(self, day: date) -> list[dict]:
        """M34's detector plants (spec §15; PLANTS "detector"): the baselines the per-user rules need on the days
        before PLANT_DAY, and on it one pair, burst or session per detector of spec §6.3."""
        rng, d0, p = self._rng("m34-signins", day), datetime.combine(day, time(), UTC), self.oid
        k = (day - FIRST_DAY).days
        ok = {"errorCode": 0, "failureReason": None, "additionalDetails": None}
        out: list[dict] = []

        def at(h: int, m: int, s: int = 0) -> datetime:
            return d0 + timedelta(hours=h, minutes=m, seconds=s)

        def place(where: tuple) -> dict:
            city, state, cc, lat, lon, ip, asn = where
            return {"ipAddress": ip, "autonomousSystemNumber": asn,
                    "location": {"city": city, "state": state, "countryOrRegion": cc,
                                 "geoCoordinates": {"altitude": None, "latitude": lat, "longitude": lon}}}

        def home(ui: int) -> dict:
            ip, asn, loc, ua, _ = self._home(ui)
            return {"ipAddress": ip, "autonomousSystemNumber": asn, "userAgent": ua,
                    "location": {"city": loc[0], "state": loc[1], "countryOrRegion": loc[2], "geoCoordinates": None}}

        def corp() -> dict:
            return {"ipAddress": CORP_IP, "autonomousSystemNumber": CORP_ASN,
                    "location": {"city": CORP_LOC[0], "state": CORP_LOC[1], "countryOrRegion": CORP_LOC[2],
                                 "geoCoordinates": None}}

        def mfa(method: str = "Mobile app notification") -> dict:
            return {"authenticationRequirement": "multiFactorAuthentication", "authenticationDetails": [
                {"authenticationMethod": "Password", "succeeded": True,
                 "authenticationStepRequirement": "Primary authentication",
                 "authenticationStepResultDetail": "Correct password"},
                {"authenticationMethod": method, "succeeded": True,
                 "authenticationStepRequirement": "Multi-factor authentication",
                 "authenticationStepResultDetail": "MFA successfully completed"}]}

        def signin(name: str, j: int | None, kind: str, t: datetime, ui: int, **over) -> dict:
            rid = p("plant", name) if j is None else p("plant", name, j)
            return self._user_signin(kind, rid, t, ui, rng,
                                     **{"status": ok, "conditionalAccessStatus": "success", **over})

        def sess(name: str) -> str:
            return p("plant", f"{name} session")

        if day < PLANT_DAY:
            if k < 5:                     # 30 MFA successes before the downgrade (D37: 10 at least, 60 % MFA)
                out += [signin("downgrade", 6 * k + j, "interactiveUser", at(7 + 2 * j, 30), DOWNGRADE, **corp(),
                               **mfa()) for j in range(6)]
            if k < 6:                     # 24 sign-ins of the rare-country user's own (D38: 20 at least)
                out += [signin("rare country", 4 * k + j, "interactiveUser", at(9 + 3 * j, 15), RARE, **home(RARE),
                               **mfa()) for j in range(4)]
            return out
        if day != PLANT_DAY:
            return out
        # Impossible travel (D30): Lisbon 09:00, Singapore 10:05, about 11,900 km in 65 min.
        out += [signin("travel pair", 0, "interactiveUser", at(9, 0), TRAVEL, **place(LISBON), **mfa(),
                       sessionId=sess("travel a")),
                signin("travel pair", 1, "interactiveUser", at(10, 5), TRAVEL, **place(SINGAPORE), **mfa(),
                       sessionId=sess("travel b"))]
        # Concurrent sessions (D31): two sessions 9 min apart, London and New York (about 5,570 km).
        out += [signin("concurrent sessions", 0, "interactiveUser", at(13, 0), CONCURRENT, **place(LONDON),
                       **mfa(), sessionId=sess("concurrent a")),
                signin("concurrent sessions", 1, "interactiveUser", at(13, 9), CONCURRENT, **place(NEW_YORK),
                       **mfa(), sessionId=sess("concurrent b"))]
        # AiTM (D32): 50074, then a success 3.5 min later in the same session, both through the relay's IP.
        aitm = {"ipAddress": AITM_IP, "autonomousSystemNumber": AITM_ASN, "sessionId": sess("aitm"),
                "location": {"city": "Amsterdam", "state": "Noord-Holland", "countryOrRegion": "NL",
                             "geoCoordinates": None}}
        out += [signin("aitm", 0, "interactiveUser", at(8, 0, 10), AITM, **aitm, conditionalAccessStatus="notApplied",
                       status={"errorCode": 50074, "failureReason": FAIL[50074],
                               "additionalDetails": "MFA required in Azure AD"}),
                signin("aitm", 1, "interactiveUser", at(8, 3, 40), AITM, **aitm, **mfa())]
        # MFA push bombing (D36): six denied pushes in 7 min 30 s, then the one the user accepts.
        bomb = {**home(PUSH), "sessionId": sess("push")}
        out += [signin("push bombing", j, "interactiveUser", at(22, 0) + timedelta(seconds=90 * j), PUSH, **bomb,
                       conditionalAccessStatus="notApplied",
                       status={"errorCode": 500121, "failureReason": FAIL[500121], "additionalDetails": MFA_DENIED})
                for j in range(6)]
        out.append(signin("push bombing", 6, "interactiveUser", at(22, 9), PUSH, **bomb, **mfa()))
        # Kit fingerprint (D39): the kit's ASN and user agent together; persistence follows 12 min later (audits).
        out.append(signin("kit sign-in", None, "interactiveUser", at(11, 0), KIT, ipAddress=KIT_IP,
                          autonomousSystemNumber=KIT_ASN, userAgent=KIT_UA, sessionId=sess("kit"),
                          location={"city": "Piscataway", "state": "New Jersey", "countryOrRegion": "US",
                                    "geoCoordinates": None}, **mfa()))
        # ASN relay (D34) and session integrity (D33): one session; the user's own network and managed device first,
        # then the Tor hop on another device with another user agent, where a token claim satisfies MFA.
        relay = sess("relay")
        own = {**corp(), "userAgent": USER_AGENTS[0], "sessionId": relay,
               "deviceDetail": {"deviceId": p("plant", "relay device"), "displayName": "PGUPTA-LT",
                                "operatingSystem": "Windows10", "browser": "Edge 129.0.0", "isCompliant": True,
                                "isManaged": True, "trustType": "Azure AD joined"}}
        hop = {"ipAddress": TOR_IP, "autonomousSystemNumber": TOR_ASN, "userAgent": TOR_UA, "sessionId": relay,
               "location": {"city": "Frankfurt am Main", "state": "Hessen", "countryOrRegion": "DE",
                            "geoCoordinates": None},
               "deviceDetail": {"deviceId": p("plant", "relay attacker device"), "displayName": "",
                                "operatingSystem": "Linux", "browser": "Firefox 128.0", "isCompliant": False,
                                "isManaged": False, "trustType": ""},
               "authenticationRequirement": "multiFactorAuthentication",
               "authenticationDetails": [{"authenticationMethod": "Previously satisfied", "succeeded": True,
                                          "authenticationStepRequirement": "Multi-factor authentication",
                                          "authenticationStepResultDetail":
                                              "MFA requirement satisfied by claim in the token"}]}
        out += [signin("relay hop", 0, "interactiveUser", at(13, 58), RELAY, **own, **mfa()),
                signin("relay hop", 1, "nonInteractiveUser", at(14, 2), RELAY, **hop),
                signin("relay hop", 2, "nonInteractiveUser", at(14, 20), RELAY, **hop)]
        # Password spray (D35): one IP, 50126 for SPRAYED users, two minutes apart.
        sprayed = range(len(SPECIAL), min(self.n_users, len(SPECIAL) + SPRAYED))
        out += [signin("spray", j, "interactiveUser", at(2, 2 * j), ui, ipAddress=SPRAY_IP,
                       autonomousSystemNumber=SPRAY_ASN, userAgent=SPRAY_UA, conditionalAccessStatus="notApplied",
                       location={"city": "Hong Kong", "state": "Hong Kong", "countryOrRegion": "HK",
                                 "geoCoordinates": None},
                       status={"errorCode": 50126, "failureReason": FAIL[50126], "additionalDetails": None},
                       authenticationRequirement="singleFactorAuthentication")
                for j, ui in enumerate(sprayed)]
        # MFA downgrade (D37): a single-factor success after 30 MFA ones.
        out.append(signin("downgrade", 30, "interactiveUser", at(16, 0), DOWNGRADE, **corp(),
                          authenticationRequirement="singleFactorAuthentication"))
        # Rare country, ASN, IP and user agent (D38): São Paulo, never seen for this user or anyone in the tenant.
        out.append(signin("rare country", 24, "interactiveUser", at(18, 30), RARE, **place(SAO_PAULO),
                          userAgent=ANDROID_UA, **mfa()))
        return out

    def _user_signin(self, kind: str, rid: str, t: datetime, ui: int, rng: random.Random, **over) -> dict:
        u = self.users[ui]
        ip, asn, loc, ua, managed = self._home(ui)
        if rng.random() < 0.6:
            ip, asn, loc = CORP_IP, CORP_ASN, CORP_LOC
        app_id, app, res, res_id, client = rng.choice(USER_APPS)
        err = rng.choices(ERR_CODES, ERR_WEIGHTS)[0] if u["accountEnabled"] else 50057
        interactive = kind == "interactiveUser"
        r = {"id": rid, "createdDateTime": iso(t), "userDisplayName": u["displayName"],
             "userPrincipalName": u["userPrincipalName"], "userId": u["id"], "userType": u["userType"].lower(),
             "appId": app_id, "appDisplayName": app, "resourceDisplayName": res, "resourceId": res_id,
             "ipAddress": ip, "autonomousSystemNumber": asn, "clientAppUsed": client, "userAgent": ua,
             "correlationId": self.oid("corr", rid), "sessionId": self.oid("session", ui, t.date()),
             "uniqueTokenIdentifier": _uti(rid), "isInteractive": interactive, "signInEventTypes": [kind],
             "status": {"errorCode": err, "failureReason": FAIL.get(err, "Other."), "additionalDetails": None},
             "conditionalAccessStatus": "failure" if err == 53003 else ("success" if err == 0 else "notApplied"),
             "authenticationRequirement": ("multiFactorAuthentication" if interactive and rng.random() < 0.7
                                           else "singleFactorAuthentication"),
             "riskLevelDuringSignIn": "none", "riskLevelAggregated": "none", "riskState": "none", "riskDetail": "none",
             "riskEventTypes_v2": [],
             "deviceDetail": {"deviceId": self.oid("device-of", ui) if managed else "", "displayName": "",
                              "operatingSystem": "Windows10" if managed else "MacOs",
                              "browser": "Edge 129.0.0" if client == "Browser" else "",
                              "isCompliant": managed, "isManaged": managed,
                              "trustType": "Azure AD joined" if managed else ""},
             "location": {"city": loc[0], "state": loc[1], "countryOrRegion": loc[2], "geoCoordinates": None},
             "tokenIssuerType": "AzureAD", "incomingTokenType": "none" if interactive else "primaryRefreshToken",
             "servicePrincipalId": "", "servicePrincipalName": None}
        r.update(over)
        return r

    def _sp_signin(self, kind: str, rid: str, t: datetime, sp: dict, rng: random.Random) -> dict:
        mi = kind == "managedIdentity"
        return {"id": rid, "createdDateTime": iso(t), "appId": sp["appId"], "appDisplayName": sp["displayName"],
                "servicePrincipalId": sp["id"], "servicePrincipalName": sp["displayName"],
                "resourceDisplayName": "Microsoft Graph", "resourceId": GRAPH_APP_ID,
                "ipAddress": f"20.190.{rng.randrange(128, 192)}.{rng.randrange(256)}", "autonomousSystemNumber": 8075,
                "location": {"city": "Quincy", "state": "Washington", "countryOrRegion": "US", "geoCoordinates": None},
                "correlationId": self.oid("corr", rid), "uniqueTokenIdentifier": _uti(rid), "isInteractive": False,
                "signInEventTypes": [kind],
                "status": {"errorCode": 0, "failureReason": None, "additionalDetails": None},
                "userPrincipalName": None, "userId": "", "userDisplayName": None,
                "clientCredentialType": "managedIdentity" if mi else "certificate", "tokenIssuerType": "AzureAD",
                "managedServiceIdentity": ({"msiType": "systemAssigned", "associatedResourceId":
                                            f"/subscriptions/{self.oid('sub')}/resourceGroups/rg-apps/providers/"
                                            f"Microsoft.Web/sites/{sp['displayName']}"} if mi else None)}

    def _plant_signins(self) -> list[dict]:
        rng, day = self._rng("plant-signins"), datetime.combine(PLANT_DAY, time(), UTC)
        ok = {"errorCode": 0, "failureReason": None, "additionalDetails": None}
        tor = {"ipAddress": TOR_IP, "autonomousSystemNumber": TOR_ASN, "status": ok,
               "conditionalAccessStatus": "notApplied",
               "location": {"city": "Frankfurt am Main", "state": "Hessen", "countryOrRegion": "DE",
                            "geoCoordinates": None},
               "userAgent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
               "sessionId": self.oid("plant", "tor-session"),
               "deviceDetail": {"deviceId": "", "displayName": "", "operatingSystem": "Linux",
                                "browser": "Firefox 128.0",
                                "isCompliant": False, "isManaged": False, "trustType": ""}}
        t = day + timedelta(hours=3, minutes=12, seconds=44)
        out = [self._user_signin("interactiveUser", self.oid("plant", "tor sign-in"), t, MEGAN, rng, **tor,
                                 appId=USER_APPS[0][0], appDisplayName="OfficeHome", clientAppUsed="Browser",
                                 authenticationRequirement="singleFactorAuthentication", riskLevelDuringSignIn="high",
                                 riskLevelAggregated="high", riskState="atRisk",
                                 riskEventTypes_v2=["anonymizedIPAddress"])]
        out += [self._user_signin("nonInteractiveUser", self.oid("plant", "tor session", j),
                                  t + timedelta(minutes=2 + 9 * j), MEGAN, rng, **tor, appId=USER_APPS[2][0],
                                  appDisplayName="Microsoft Office") for j in range(6)]
        out.append(self._user_signin(
            "interactiveUser", self.oid("plant", "legacy auth"), day + timedelta(hours=5, minutes=20), LEE, rng,
            ipAddress="192.0.2.66", autonomousSystemNumber=14061, clientAppUsed="IMAP4", userAgent="", status=ok,
            conditionalAccessStatus="notApplied", authenticationRequirement="singleFactorAuthentication",
            location={"city": "Amsterdam", "state": "Noord-Holland", "countryOrRegion": "NL", "geoCoordinates": None}))
        # Two sign-ins at exactly a 6 h slice boundary (plan Review Focus 2): both belong to [06:00, 12:00) only.
        out += [self._user_signin("nonInteractiveUser", self.oid("plant", "boundary twin", j), day + timedelta(hours=6),
                                  ALEX, rng) for j in range(2)]
        return out

    # --- directory audits --------------------------------------------------------------------------------------
    def directory_audits(self, day: date) -> list[dict]:
        return self._cached(("audit", day), lambda: self._directory_audits(day))

    def _audit(self, rid, t, cat, act, op, actor, target, mods=(), ip="", service="Core Directory") -> dict:
        return {"id": rid, "category": cat, "correlationId": self.oid("corr", rid), "result": "success",
                "resultReason": "", "activityDisplayName": act, "activityDateTime": iso(t),
                "loggedByService": service, "operationType": op, "additionalDetails": [],
                "initiatedBy": {"app": None, "user": {"id": actor["id"], "displayName": None, "ipAddress": ip,
                                                      "userPrincipalName": actor["userPrincipalName"],
                                                      "userType": None}},
                "targetResources": [{**target, "modifiedProperties": [
                    {"displayName": k, "oldValue": o, "newValue": n} for k, o, n in mods]}]}

    def _directory_audits(self, day: date) -> list[dict]:
        rng, start, rows = self._rng("audit", day), datetime.combine(day, time(), UTC), []
        colls = {"Group": "groups", "Device": "devices", "Application": "applications"}

        def target(ttype: str) -> dict:
            if ttype == "User":
                u = self.users[rng.randrange(self.n_users)]
                return {"id": u["id"], "displayName": u["displayName"], "type": "User",
                        "userPrincipalName": u["userPrincipalName"]}
            obj = rng.choice(self._directory[colls[ttype]])
            return {"id": obj["id"], "displayName": obj["displayName"], "type": ttype, "userPrincipalName": None}

        for i in range(max(5, self.n_users // 20)):
            cat, act, op, ttype = rng.choice(AUDIT_BACKGROUND)
            actor = self.users[rng.choice((ADELE, rng.randrange(self.n_users)))]
            rows.append(self._audit(f"Directory_{self.oid('audit', day, i)}",
                                    start + timedelta(seconds=rng.randrange(86_400)), cat, act, op, actor,
                                    target(ttype)))
        if self.plants and day == PLANT_DAY:
            rows += self._plant_audits()
        rows.sort(key=lambda r: (r["activityDateTime"], r["id"]))
        return rows

    def _plant_audits(self) -> list[dict]:
        day = datetime.combine(PLANT_DAY, time(), UTC)

        def at(h: int, m: int, s: int = 0) -> datetime:
            return day + timedelta(hours=h, minutes=m, seconds=s)

        megan, adele, svc, a, p = self.users[MEGAN], self.users[ADELE], self.users[SVC], self._audit, self.oid
        grady = self.users[KIT]
        sp_t = {"id": self._mail_sp["id"], "displayName": MAIL_APP, "type": "ServicePrincipal",
                "userPrincipalName": None}
        graph_t = {"id": self._graph_sp["id"], "displayName": "Microsoft Graph", "type": "ServicePrincipal",
                   "userPrincipalName": None}
        perms = (f"[] => [[Id: {self._plant_grant['id']}, ClientId: {self._mail_sp['id']}, PrincipalId: {megan['id']}, "
                 f"ResourceId: {self._graph_sp['id']}, ConsentType: Principal, "
                 "Scope: Mail.Read Mail.Send offline_access]]; ")
        return [
            a(p("plant", "add service principal"), at(3, 30, 9), "ApplicationManagement", "Add service principal",
              "Add", megan, sp_t, ip=TOR_IP),
            a(p("plant", "oauth consent"), at(3, 30, 10), "ApplicationManagement", "Consent to application", "Assign",
              megan, sp_t, ip=TOR_IP, mods=(("ConsentContext.IsAdminConsent", None, '"False"'),
                                            ("ConsentContext.OnBehalfOfAll", None, '"False"'),
                                            ("ConsentAction.Permissions", None, json.dumps(perms)),
                                            ("TargetId.ServicePrincipalNames", None, json.dumps(MAIL_APP_ID)))),
            a(p("plant", "delegated grant"), at(3, 30, 10), "ApplicationManagement", "Add delegated permission grant",
              "Assign", megan, graph_t, ip=TOR_IP,
              mods=(("DelegatedPermissionGrant.Scope", None, '"Mail.Read Mail.Send offline_access"'),)),
            a(p("plant", "sp credential"), at(3, 41, 27), "ApplicationManagement", "Add service principal credentials",
              "Update", megan, sp_t, ip=TOR_IP,
              mods=(("KeyDescription", "[]", f'"[KeyIdentifier={p("plant", "sp key")},KeyType=Password,'
                                             'KeyUsage=Verify,DisplayName=sync]"'),)),
            a(p("plant", "global admin"), at(4, 12), "RoleManagement", "Add member to role", "Assign", adele,
              {"id": svc["id"], "displayName": svc["displayName"], "type": "User",
               "userPrincipalName": svc["userPrincipalName"]},
              mods=(("Role.DisplayName", None, '"Global Administrator"'), ("Role.TemplateId", None, f'"{GA_ROLE}"'))),
            a(p("plant", "ca disabled"), at(4, 20), "Policy", "Update conditional access policy", "Update", adele,
              {"id": self._ca_mfa["id"], "displayName": self._ca_mfa["displayName"], "type": "Policy",
               "userPrincipalName": None},
              mods=(("ConditionalAccessPolicy", '{"state":"enabled"}', '{"state":"disabled"}'),)),
            a(p("plant", "federated domain"), at(4, 31), "DirectoryManagement", "Set domain authentication", "Update",
              adele, {"id": p("domain", FED_DOMAIN), "displayName": FED_DOMAIN, "type": "Other",
                      "userPrincipalName": None}, mods=(("LiveType", '"Managed"', '"Federated"'),)),
            a(p("plant", "guest invited"), at(4, 45), "UserManagement", "Invite external user", "Add", adele,
              {"id": p("plant", "guest"), "displayName": "x@partner-lab.example", "type": "User",
               "userPrincipalName": f"x_partner-lab.example#EXT#@{self.spec.domain}"}),
            # M34: persistence 12 min after the kit sign-in's signal (D40): a new MFA method, then a consent
            a(p("plant", "kit security info"), at(11, 12), "UserManagement", "User registered security info", "Add",
              grady, {"id": grady["id"], "displayName": grady["displayName"], "type": "User",
                      "userPrincipalName": grady["userPrincipalName"]}, ip=KIT_IP, service="Authentication Methods",
              mods=(("StrongAuthenticationMethod", "[]", '[{"MethodType":6,"Default":true}]'),)),
            a(p("plant", "kit consent"), at(11, 12, 30), "ApplicationManagement", "Consent to application", "Assign",
              grady, sp_t, ip=KIT_IP, mods=(("ConsentContext.IsAdminConsent", None, '"False"'),
                                            ("ConsentContext.OnBehalfOfAll", None, '"False"'),
                                            ("TargetId.ServicePrincipalNames", None, json.dumps(MAIL_APP_ID)))),
        ]

    # --- risk --------------------------------------------------------------------------------------------------
    def risky_users(self) -> list[dict]:
        return self._cached(("risky",), self._risky_users)

    def _risky_users(self) -> list[dict]:
        rng, start = self._rng("risky"), datetime.combine(FIRST_DAY, time(), UTC)

        def row(i: int, level: str, t: datetime) -> dict:
            u = self.users[i]
            return {"id": u["id"], "isDeleted": False, "isProcessing": False, "riskLastUpdatedDateTime": iso(t),
                    "riskLevel": level, "riskState": "atRisk", "riskDetail": "none",
                    "userDisplayName": u["displayName"], "userPrincipalName": u["userPrincipalName"]}

        picks = rng.sample(range(len(SPECIAL), self.n_users), max(1, self.n_users // 200))
        rows = [row(i, rng.choice(("low", "medium")), start + timedelta(minutes=rng.randrange(DAYS * 1440)))
                for i in sorted(picks)]
        if self.plants:
            rows.append(row(MEGAN, "high", datetime.combine(PLANT_DAY, time(3, 13, 30), UTC)))
        return sorted(rows, key=lambda r: (r["riskLastUpdatedDateTime"], r["id"]))

    def risk_detections(self, day: date) -> list[dict]:
        return self._cached(("risk", day), lambda: self._risk_detections(day))

    def _risk_detections(self, day: date) -> list[dict]:
        rng, start, rows = self._rng("risk", day), datetime.combine(day, time(), UTC), []

        def row(rid, t, ui, typ, level, ip, loc, request_id) -> dict:
            u = self.users[ui]
            return {"id": rid, "requestId": request_id, "correlationId": self.oid("corr", request_id),
                    "riskEventType": typ, "riskState": "atRisk", "riskLevel": level, "riskDetail": "none",
                    "source": "IdentityProtection", "detectionTimingType": "realtime", "activity": "signin",
                    "tokenIssuerType": "AzureAD", "ipAddress": ip, "additionalInfo": "[]",
                    "location": {"city": loc[0], "state": loc[1], "countryOrRegion": loc[2]},
                    "activityDateTime": iso(t), "detectedDateTime": iso(t), "lastUpdatedDateTime": iso(t),
                    "userId": u["id"], "userDisplayName": u["displayName"], "userPrincipalName": u["userPrincipalName"]}

        for i in range(max(1, self.n_users // 500)):
            ui = rng.randrange(self.n_users)
            ip, _, loc, _, _ = self._home(ui)
            rid = self.oid("risk", day, i)
            rows.append(row(rid, start + timedelta(seconds=rng.randrange(86_400)), ui,
                            rng.choice(("unfamiliarFeatures", "unlikelyTravel")), rng.choice(("low", "medium")),
                            ip, loc, rid))
        if self.plants and day == PLANT_DAY:
            rows.append(row(self.oid("plant", "risk anonymized ip"), datetime.combine(PLANT_DAY, time(3, 12, 44), UTC),
                            MEGAN, "anonymizedIPAddress", "medium", TOR_IP, ("Frankfurt am Main", "Hessen", "DE"),
                            self.oid("plant", "tor sign-in")))
        rows.sort(key=lambda r: (r["detectedDateTime"], r["id"]))
        return rows

    # --- Unified Audit Log (Graph audit search records) --------------------------------------------------------
    def ual(self, day: date) -> list[dict]:
        return self._cached(("ual", day), lambda: self._ual(day))

    def _ual_record(self, rid, t, u, op, svc, rtype, rnum, ip, object_id=None, **extra) -> dict:
        stamp = iso(t)
        return {"id": rid, "createdDateTime": stamp, "auditLogRecordType": rtype, "operation": op,
                "organizationId": self.tid, "userType": "regular", "userId": u["userPrincipalName"], "service": svc,
                "objectId": object_id or u["userPrincipalName"], "userPrincipalName": u["userPrincipalName"],
                "clientIp": ip, "administrativeUnits": [],
                "auditData": {"CreationTime": stamp[:-1], "Id": rid, "Operation": op, "OrganizationId": self.tid,
                              "RecordType": rnum, "ResultStatus": "Succeeded", "UserKey": u["id"], "UserType": 0,
                              "Version": 1, "Workload": svc, "ClientIP": ip, "UserId": u["userPrincipalName"], **extra}}

    def _ual(self, day: date) -> list[dict]:
        rng, start, rows = self._rng("ual", day), datetime.combine(day, time(), UTC), []
        weights = [w[4] for w in UAL_BACKGROUND]
        for i in range(self.n_users):
            ui = rng.randrange(self.n_users)
            op, svc, rtype, rnum, _ = rng.choices(UAL_BACKGROUND, weights)[0]
            ip = CORP_IP if rng.random() < 0.6 else self._home(ui)[0]
            rows.append(self._ual_record(self.oid("ual", day, i), start + timedelta(seconds=rng.randrange(86_400)),
                                         self.users[ui], op, svc, rtype, rnum, ip))
        if self.plants and day == PLANT_DAY:
            rows += self._plant_ual()
        rows.sort(key=lambda r: (r["createdDateTime"], r["id"]))
        return rows

    def _plant_ual(self) -> list[dict]:
        day, megan, alex, p = datetime.combine(PLANT_DAY, time(), UTC), self.users[MEGAN], self.users[ALEX], self.oid

        def at(h: int, m: int, s: int = 0) -> datetime:
            return day + timedelta(hours=h, minutes=m, seconds=s)

        sess = {"SessionId": p("plant", "tor-session"),
                "AppAccessContext": {"UniqueTokenId": _uti(p("plant", "tor sign-in")),
                                     "AADSessionId": p("plant", "tor-session")}}
        rows = [
            self._ual_record(p("plant", "ual new-inboxrule"), at(3, 52, 30), megan, "New-InboxRule", "Exchange",
                             "exchangeAdmin", 1, f"{TOR_IP}:51234", ObjectId=f"{megan['userPrincipalName']}\\.",
                             Parameters=[{"Name": "Name", "Value": "."}, {"Name": "ForwardTo", "Value": EXTERNAL_FWD},
                                         {"Name": "MoveToFolder", "Value": "RSS Subscriptions"},
                                         {"Name": "SubjectContainsWords", "Value": "invoice;payment"},
                                         {"Name": "StopProcessingRules", "Value": "True"}], **sess),
            self._ual_record(p("plant", "ual set-mailbox"), at(4, 2), alex, "Set-Mailbox", "Exchange", "exchangeAdmin",
                             1, "192.0.2.80", ObjectId=alex["userPrincipalName"],
                             Parameters=[{"Name": "Identity", "Value": alex["userPrincipalName"]},
                                         {"Name": "ForwardingSmtpAddress", "Value": f"smtp:{ALEX_FWD}"},
                                         {"Name": "DeliverToMailboxAndForward", "Value": "False"}]),
            self._ual_record(p("plant", "ual consent"), at(3, 30, 10), megan, "Consent to application.",
                             "AzureActiveDirectory", "azureActiveDirectory", 8, TOR_IP, object_id=self._mail_sp["id"],
                             Target=[{"ID": MAIL_APP_ID, "Type": 2}]),
            self._ual_record(p("plant", "ual sp credential"), at(3, 41, 27), megan,
                             "Add service principal credentials.",
                             "AzureActiveDirectory", "azureActiveDirectory", 8, TOR_IP, object_id=self._mail_sp["id"]),
        ]
        rows += [self._ual_record(p("plant", "ual app mail access", j), at(4, j), megan, "MailItemsAccessed",
                                  "Exchange", "exchangeItemAggregated", 50, "192.0.2.77", AppId=MAIL_APP_ID,
                                  ClientAppId=MAIL_APP_ID,
                                  MailboxOwnerUPN=megan["userPrincipalName"],
                                  OperationProperties=[{"Name": "MailAccessType", "Value": "Bind"}])
                 for j in range(40)]
        # M34: the relayed session's inbox rule, 4 min after its first Tor hop, on that session and token (D13)
        pradeep, relay = self.users[RELAY], p("plant", "relay session")
        rows.append(self._ual_record(
            p("plant", "ual relay inbox rule"), at(14, 6), pradeep, "New-InboxRule", "Exchange", "exchangeAdmin", 1,
            f"{TOR_IP}:51990", ObjectId=f"{pradeep['userPrincipalName']}\\..",
            Parameters=[{"Name": "Name", "Value": ".."}, {"Name": "MoveToFolder", "Value": "Archive"},
                        {"Name": "SubjectContainsWords", "Value": "wire;bank"}, {"Name": "MarkAsRead", "Value": "True"}],
            SessionId=relay, AppAccessContext={"UniqueTokenId": _uti(p("plant", "relay hop", 1)),
                                               "AADSessionId": relay}))
        return rows

    # --- mailboxes, inbox rules, message trace -----------------------------------------------------------------
    def inbox_rules(self, i: int) -> list[dict]:
        """A mailbox's rules as definitions (graph_rule / exo_rule render them); `hidden` ones Graph never sees."""
        if not self.mailbox[i]:
            return []
        rules = []
        if self._rng("rules", i).random() < 0.1:
            rules.append({"name": "Newsletters", "enabled": True, "words": ["newsletter"], "forward": [],
                          "move": "Newsletters", "read": False, "delete": False, "stop": False, "hidden": False})
        if self.plants and i == MEGAN:
            rules += [{"name": ".", "enabled": True, "words": ["invoice", "payment"], "forward": [EXTERNAL_FWD],
                       "move": "RSS Subscriptions", "read": True, "delete": False, "stop": True, "hidden": False},
                      {"name": " ", "enabled": True, "words": ["wire"], "forward": [EXTERNAL_FWD], "move": None,
                       "read": True, "delete": True, "stop": True, "hidden": True}]
        for k, r in enumerate(rules):
            r.update(id=self.oid("rule", i, k), priority=k + 1)
        return rules

    def mailboxes(self) -> list[dict]:
        return self._cached(("mailboxes",), self._mailboxes)

    def _mailboxes(self) -> list[dict]:
        rows = []
        for i, u in enumerate(self.users):
            if not self.mailbox[i]:
                continue
            fwd = f"smtp:{ALEX_FWD}" if self.plants and i == ALEX else None
            rows.append({"ExternalDirectoryObjectId": u["id"], "Guid": self.oid("mbx", i),
                         "DisplayName": u["displayName"],
                         "UserPrincipalName": u["userPrincipalName"], "PrimarySmtpAddress": u["mail"],
                         "RecipientTypeDetails": "UserMailbox", "ForwardingSmtpAddress": fwd, "ForwardingAddress": None,
                         "DeliverToMailboxAndForward": False,
                         "WhenChanged": "2026-10-07T04:02:00Z" if fwd else u["createdDateTime"]})
        return rows

    def message_trace(self, day: date) -> list[dict]:
        """One day's messages as Graph's exchangeMessageTrace (beta /admin/exchange/tracing/messageTraces, R14)."""
        return self._cached(("trace", day), lambda: self._message_trace(day))

    def _message_trace(self, day: date) -> list[dict]:
        rng, start, rows = self._rng("trace", day), datetime.combine(day, time(), UTC), []
        boxes = [i for i, m in enumerate(self.mailbox) if m]

        def msg(mid, t, sender, rcpt, subject, status, from_ip, to_ip) -> dict:
            return {"id": mid, "messageId": f"<{mid}@{self.spec.domain}>", "receivedDateTime": iso(t),
                    "senderAddress": sender, "recipientAddress": rcpt, "subject": subject, "status": status,
                    "size": 4_000 + int(hashlib.sha256(mid.encode()).hexdigest()[:5], 16),
                    "fromIP": from_ip, "toIP": to_ip}

        for k in range(int(len(boxes) * 0.7)):
            u = self.users[rng.choice(boxes)]
            ext = f"{rng.choice(FIRST_NAMES)}@{rng.choice(('fabrikam-lab.example', 'northwind-lab.example'))}"
            inbound = rng.random() < 0.5
            sender, rcpt = (ext, u["mail"]) if inbound else (u["mail"], ext)
            rows.append(msg(self.oid("mt", day, k), start + timedelta(seconds=rng.randrange(86_400)), sender, rcpt,
                            rng.choice(SUBJECTS), rng.choices(("delivered", "filteredAsSpam", "failed"), (95, 4, 1))[0],
                            f"192.0.2.{rng.randrange(1, 255)}" if inbound else "203.0.113.25",
                            None if inbound else f"198.51.100.{rng.randrange(1, 255)}"))
        if self.plants and day == PLANT_DAY:
            megan = self.users[MEGAN]["mail"]
            rows += [msg(self.oid("plant", "forwarded mail", j), start + timedelta(hours=4, minutes=5 * j), megan,
                         EXTERNAL_FWD, f"FW: invoice {j + 1}", "delivered", "203.0.113.25", "198.51.100.66")
                     for j in range(12)]
        rows.sort(key=lambda r: (r["receivedDateTime"], r["id"]))
        return rows

    # --- live blades (M34): what Graph returns per object for VTW's live reads (lab/mockcloud serves them) -----
    def member_of(self, i: int) -> list[dict]:
        """Groups (deterministic per user) and the directory roles the user holds."""
        groups = self._directory["groups"]
        out = [{"@odata.type": "#microsoft.graph.group", **groups[(i * 7 + k) % len(groups)]} for k in range(1 + i % 3)]
        uid = self.users[i]["id"]
        roles = {a["roleDefinitionId"] for a in self._directory["roleManagement/directory/roleAssignments"]
                 if a["principalId"] == uid}
        return out + [{"@odata.type": "#microsoft.graph.directoryRole", "id": self.oid("role", r),
                       "roleTemplateId": r, "displayName": ROLE_NAMES.get(r, r)} for r in sorted(roles)]

    def licence_states(self, i: int) -> list[dict]:
        """users/{id}?$select=licenseAssignmentStates (the Licences blade; licenseDetails has no application
        permission, Task 9's notes)."""
        return [{"skuId": SKU_IDS[x], "state": "Active", "assignedByGroup": None, "disabledPlans": [],
                 "error": "None", "lastUpdatedDateTime": self.users[i]["createdDateTime"]} for x in self.spec.licences]

    def managed_devices(self, i: int) -> list[dict]:
        """deviceManagement/managedDevices?$filter=userId eq '{id}' (the Devices blade: Intune, Task 9's notes),
        shaped from the user's directory device."""
        devices = self._directory["devices"]
        if i >= len(devices):
            return []
        d = devices[i]
        return [{"id": self.oid("managed", i), "userId": self.users[i]["id"], "deviceName": d.get("displayName"),
                 "operatingSystem": d.get("operatingSystem"), "osVersion": d.get("operatingSystemVersion"),
                 "complianceState": "compliant" if d.get("isCompliant") else "noncompliant",
                 "managedDeviceOwnerType": "company", "lastSyncDateTime": d.get("approximateLastSignInDateTime")}]

    def auth_methods(self, i: int) -> list[dict]:
        """Password for everyone, the Authenticator app for most; megan's attacker phone (03:50) and the kit user's
        new Authenticator (11:12, the audit's) are the plants."""
        u = self.users[i]
        out = [{"@odata.type": "#microsoft.graph.passwordAuthenticationMethod", "id": PASSWORD_METHOD,
                "password": None, "createdDateTime": u["createdDateTime"]}]
        if i < len(SPECIAL) or self._rng("authm", i).random() < 0.7:
            out.append({"@odata.type": "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
                        "id": self.oid("authm", i), "displayName": f"{u['givenName']}'s iPhone",
                        "deviceTag": "SoftwareTokenActivated", "phoneAppVersion": "6.8.12",
                        "createdDateTime": u["createdDateTime"]})
        if self.plants and i == MEGAN:
            out.append({"@odata.type": "#microsoft.graph.phoneAuthenticationMethod",
                        "id": self.oid("plant", "megan phone"), "phoneNumber": "+1 5550100199", "phoneType": "mobile",
                        "smsSignInState": "notAllowedByPolicy", "createdDateTime": "2026-10-07T03:50:12Z"})
        if self.plants and i == KIT:
            out.append({"@odata.type": "#microsoft.graph.microsoftAuthenticatorAuthenticationMethod",
                        "id": self.oid("plant", "kit authenticator"), "displayName": "SM-A515F",
                        "deviceTag": "SoftwareTokenActivated", "phoneAppVersion": "6.2408.5612",
                        "createdDateTime": "2026-10-07T11:12:00Z"})
        return out

    def _mail_assignment(self) -> dict:
        megan = self.users[MEGAN]
        return {"id": self.oid("plant", "mail app assignment"), "appRoleId": "00000000-0000-0000-0000-000000000000",
                "principalId": megan["id"], "principalDisplayName": megan["displayName"], "principalType": "User",
                "resourceId": self._mail_sp["id"], "resourceDisplayName": MAIL_APP,
                "createdDateTime": "2026-10-07T03:30:10Z"}

    def user_app_roles(self, i: int) -> list[dict]:
        return [self._mail_assignment()] if self.plants and i == MEGAN else []

    def sp_grants(self, sp_id: str) -> list[dict]:
        return [g for g in self._directory["oauth2PermissionGrants"] if g["clientId"] == sp_id]

    def sp_assigned_to(self, sp_id: str) -> list[dict]:
        return [self._mail_assignment()] if self.plants and sp_id == self._mail_sp["id"] else []

    def owners(self, coll: str, obj_id: str) -> list[dict]:
        """The tenant's own app registrations are owned by adele (an admin); service principals have no owner
        here."""
        if coll != "applications":
            return []
        adele = self.users[ADELE]
        return [{"@odata.type": "#microsoft.graph.user", "id": adele["id"], "displayName": adele["displayName"],
                 "userPrincipalName": adele["userPrincipalName"]}]

    def risky_user(self, i: int) -> dict | None:
        uid = self.users[i]["id"]
        return next((r for r in self.risky_users() if r["id"] == uid), None)


def summary(t: Tenant) -> dict:
    d = PLANT_DAY
    return {"tenant": t.spec.name, "tenant_id": t.tid, "domain": t.spec.domain, "users": t.n_users,
            "mailboxes": sum(t.mailbox),
            "on_2026-10-07": {**{f"signins.{k}": len(t.signins(k, d)) for k in SIGNIN_TYPES},
                              "directory_audits": len(t.directory_audits(d)), "ual": len(t.ual(d)),
                              "risk_detections": len(t.risk_detections(d)), "message_trace": len(t.message_trace(d))},
            "directory": {k: len(t.directory(k)) for k in t._directory},
            "plants": [p["name"] for p in PLANTS] if t.plants else []}


def main() -> None:
    ap = argparse.ArgumentParser(description="Print the synthetic tenants' sizes and plants.")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--users", type=int, default=None, help="Contoso's user count (default 5000)")
    a = ap.parse_args()
    for key, spec in TENANTS.items():
        print(json.dumps(summary(Tenant(spec, a.seed, a.users if key == "contoso" else None)), indent=2))


if __name__ == "__main__":
    main()
