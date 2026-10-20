"""Curated public API writes. Caller input cannot choose an origin, method or headers."""

import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit
from uuid import UUID
from xml.etree import ElementTree as ET

from pydantic import Field

from mockazure.admin.errors import AdminError
from mockazure.population.identity import resolve_identity
from mockazure.population.tokens import fingerprint
from mockazure.population.transport import LocalTransport
from mockazure.population.types import (
    BatchResult,
    Input,
    Inspection,
    OperationDescriptor,
    ResourceItem,
    VerificationResult,
)


class ActionInput(Input):
    variables: dict[str, str]
    body: dict = Field(default_factory=dict)
    content_id: UUID | None = None


@dataclass(frozen=True)
class Action:
    key: str
    service: str
    method: str
    path: str
    version: str
    graph_type: str | None = None
    params: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    binary: bool = False
    provider: str | None = None
    response_kind: str = "json"
    shared_key: bool = False


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            k in actual and subset(v, actual[k]) for k, v in expected.items()
        )
    return expected == actual


class PublicActions:
    def __init__(self, base, actions):
        self.base = base
        self.actions = {action.key: action for action in actions}

    def describe(self):
        result = self.base.describe().model_copy(deep=True)
        for action in self.actions.values():
            result.operations.append(
                OperationDescriptor(
                    key=action.key,
                    mode="service",
                    method=action.method,
                    path_template=action.path,
                    api_version=action.version,
                    input_schema=ActionInput.model_json_schema(),
                    identity_required=True,
                    replay_policy="manual"
                    if action.method == "POST" or action.response_kind == "file"
                    else "reconcilable",
                )
            )
        result.limitations.append(
            "Insert-only ARM, FileREST, Queue and Key Vault writes use local "
            "X-MockAzure preconditions checked under service locks; "
            "these are not Microsoft headers."
        )
        result.limitations.append(
            "Public actions use a named test identity. PUT is insert-only; POST outcomes "
            "require reconciliation before retry."
        )
        return result

    async def browse(self, ctx, query):
        return await self.base.browse(ctx, query)

    def prepare(self, mutation):
        action = self.actions[mutation.operation]
        data = ActionInput.model_validate(mutation.payload)
        variables = re.findall(r"\{([^}]+)\}", action.path)
        if set(data.variables) != set(variables):
            raise ValueError("Provide exactly the operation's path variables")
        escaped = {}
        for key, value in data.variables.items():
            if key == "resource":
                from mockazure.providers.arm.scopes import parse_scope

                parsed = parse_scope(value)
                if (
                    not action.provider
                    or "/providers/" + action.provider.lower() + "/" not in parsed.key
                ):
                    raise ValueError("Resource does not match the operation provider")
                escaped[key] = parsed.key
            else:
                if (
                    not 1 <= len(value) <= 512
                    or (
                        any(ord(c) < 32 or c in "/\\" for c in value)
                        if action.service == "graph"
                        else not re.fullmatch(r"[A-Za-z0-9_:@., -]+", value)
                    )
                    or value in {".", ".."}
                ):
                    raise ValueError("Invalid public resource path variable")
                escaped[key] = quote(value, safe="")
        if data.content_id and (not action.binary or data.body):
            raise ValueError("Uploaded content is accepted only by binary operations")
        if action.binary and data.content_id is None:
            raise ValueError("Select a finalized content upload")
        if action.response_kind == "queue" and (
            set(data.body) != {"text"} or not isinstance(data.body["text"], str)
        ):
            raise ValueError("Queue enqueue requires a text body")
        if action.response_kind == "table" and (
            set(data.body) != {"TableName"}
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,62}", str(data.body["TableName"]))
        ):
            raise ValueError("Invalid table name")
        if action.response_kind == "entity" and any(
            not isinstance(data.body.get(key), str) for key in ("PartitionKey", "RowKey")
        ):
            raise ValueError("Table entity requires PartitionKey and RowKey strings")
        if action.graph_type:
            from mockazure.providers.graph.schema_reference import validate_payload

            validate_payload("v1.0", action.graph_type, data.body, partial=True)
        return action, data, action.path.format(**escaped)

    def validate(self, mutation):
        if mutation.operation in self.actions:
            self.prepare(mutation)
        else:
            self.base.validate(mutation)

    async def transport(self, ctx, identity):
        return LocalTransport(ctx, await resolve_identity(ctx, identity))

    @staticmethod
    def read_headers(action):
        return {
            k: v
            for k, v in action.headers.items()
            if k in {"x-ms-version", "x-ms-file-request-intent"}
        }

    @staticmethod
    def read_method(action):
        return (
            "HEAD"
            if action.binary or (action.response_kind == "namespace" and action.service != "queue")
            else "GET"
        )

    @staticmethod
    def read_params(action):
        if action.service == "queue" and action.response_kind == "namespace":
            return {"comp": "metadata"}
        return (
            {"peekonly": "true", "numofmessages": "32"}
            if action.response_kind == "queue"
            else action.params
        )

    async def inspect(self, ctx, mutation):
        if mutation.operation not in self.actions:
            return await self.base.inspect(ctx, mutation)
        action, data, path = self.prepare(mutation)
        transport = await self.transport(ctx, mutation.identity_ref or ctx.identity_ref)
        transport.shared_key = action.shared_key
        if action.method == "POST":
            # Authenticate now; a collection is not the future server-assigned resource.
            response = await transport.request(
                action.service,
                self.read_method(action),
                path,
                params=self.read_params(action),
                headers=self.read_headers(action),
            )
            if response.status_code not in {200, 204, 404, 405}:
                self.check(response)
            return Inspection(
                actual_id=path,
                version=None,
                exists=False,
                conflicts=[
                    "Creates a server-assigned resource; uncertain results must not be blindly "
                    "retried."
                ],
            )
        response = await transport.request(
            action.service,
            self.read_method(action),
            path,
            params=self.read_params(action),
            headers=self.read_headers(action),
        )
        if response.status_code == 404:
            return Inspection(actual_id=path, version=None, exists=False)
        self.check(response)
        return Inspection(
            actual_id=path,
            version=fingerprint([response.headers.get("etag"), response.content.hex()]),
            exists=True,
        )

    @staticmethod
    def check(response):
        if not 200 <= response.status_code < 300:
            raise AdminError(
                response.status_code,
                "ServiceActionRejected",
                "The local public API rejected the operation; check identity, permissions and "
                "payload.",
            )

    async def apply(self, ctx, mutations):
        if all(m.operation not in self.actions for m in mutations):
            return await self.base.apply(ctx, mutations)
        if len(mutations) != 1 or mutations[0].operation not in self.actions:
            raise ValueError("Public actions execute one checkpointed operation at a time")
        mutation = mutations[0]
        action, data, path = self.prepare(mutation)
        identity = mutation.identity_ref or ctx.identity_ref
        transport = await self.transport(ctx, identity)
        transport.shared_key = action.shared_key
        headers = dict(action.headers)
        if action.method == "PUT":
            old = await transport.request(
                action.service,
                self.read_method(action),
                path,
                params=self.read_params(action),
                headers=self.read_headers(action),
            )
            if old.status_code != 404:
                self.check(old)
                if mutation.expected_version == "absent":
                    raise AdminError(
                        409, "ResourceConflict", "Public resource appeared after preview"
                    )
                if (
                    mutation.expected_version
                    and fingerprint([old.headers.get("etag"), old.content.hex()])
                    != mutation.expected_version
                ):
                    raise AdminError(
                        409, "ResourceConflict", "Public resource changed after preview"
                    )
                if action.binary:
                    from mockazure.content.repository import object_row

                    async with ctx.database.sessions() as session:
                        row = await object_row(
                            session, ctx.tenant_id, data.content_id, finalized=True
                        )
                        expected = {"sha256": row.sha256, "size": row.size}
                    actual = await transport.request(
                        action.service,
                        "GET",
                        path,
                        params=self.read_params(action),
                        headers=self.read_headers(action),
                        digest=True,
                    )
                    self.check(actual)
                    if actual.json() != expected:
                        raise AdminError(
                            409, "InsertOnly", "Public content differs from the requested upload"
                        )
                    result = self.result(action, path, identity, old, unchanged=True)
                    result.items[0].properties.update(
                        content_sha256=expected["sha256"], content_size=expected["size"]
                    )
                    return result
                try:
                    matches = not action.binary and subset(data.body, old.json())
                except ValueError:
                    matches = not data.body and not action.binary
                if not matches:
                    raise AdminError(
                        409, "InsertOnly", "This public action cannot replace an existing resource"
                    )
                return self.result(action, path, identity, old, unchanged=True)
            if mutation.expected_version not in {None, "absent"}:
                raise AdminError(
                    409, "ResourceConflict", "Public resource disappeared after preview"
                )
            if action.service not in {"queue", "arm", "keyvault", "file"}:
                headers["If-None-Match"] = "*"
            else:
                headers["x-mockazure-if-none-match"] = "*"
        content = None
        if action.binary:
            from mockazure.content.repository import object_row, stream_content

            async with ctx.database.sessions.begin() as session:
                row = await object_row(session, ctx.tenant_id, data.content_id, finalized=True)
                headers["Content-Length"] = str(row.size)
                headers["Content-Type"] = row.media_type
                expected_hash, expected_size = row.sha256, row.size
            if action.response_kind == "file":
                if expected_size > 4 * 1024 * 1024:
                    raise ValueError(
                        "Console FileREST uploads currently support one range up to 4 MiB"
                    )
                created = await transport.request(
                    "file",
                    "PUT",
                    path,
                    headers={
                        **self.read_headers(action),
                        "x-mockazure-if-none-match": "*",
                        "x-ms-type": "file",
                        "x-ms-content-length": str(expected_size),
                    },
                )
                self.check(created)
                headers.pop("If-None-Match", None)
                headers.pop("x-mockazure-if-none-match", None)
                if not created.headers.get("etag"):
                    raise AdminError(
                        502, "UncertainServiceResult", "File creation omitted its version"
                    )
                headers["x-mockazure-if-match"] = created.headers["etag"]
                headers.update(
                    {"x-ms-range": f"bytes=0-{expected_size - 1}", "x-ms-write": "update"}
                )

            async def upload_stream():
                # Release the shared source lock when the request body ends, before
                # the service deduplicates/finalizes the same content object.
                async with ctx.database.sessions.begin() as source:
                    async for chunk in stream_content(source, ctx.tenant_id, data.content_id):
                        yield chunk

            if action.response_kind == "file" and expected_size == 0:
                response = created
            else:
                response = await transport.request(
                    action.service,
                    action.method,
                    path,
                    params={"comp": "range"} if action.response_kind == "file" else action.params,
                    headers=headers,
                    content=upload_stream(),
                )
        else:
            body = data.body if data.body else None
            if action.response_kind == "queue":
                root = ET.Element("QueueMessage")
                ET.SubElement(root, "MessageText").text = data.body["text"]
                content, body = ET.tostring(root, encoding="utf-8"), None
                headers["Content-Type"] = "application/xml"
            response = await transport.request(
                action.service,
                action.method,
                path,
                params=action.params,
                headers=headers,
                json=body,
                content=content,
            )
        self.check(response)
        result = self.result(action, path, identity, response)
        if action.binary:
            result.items[0].properties.update(
                content_sha256=expected_hash, content_size=expected_size
            )
        return result

    def result(self, action, path, identity, response, *, unchanged=False):
        identifier = path
        read_path = path
        if action.response_kind == "registration":
            identifier = read_path = path.removesuffix("/register")
        elif action.response_kind == "queue":
            identifier = ET.fromstring(response.content).findtext(".//MessageId")
            if not identifier:
                raise AdminError(
                    502,
                    "UncertainServiceResult",
                    "Queue accepted request without a message identity",
                )
        elif action.response_kind == "table":
            name = response.json().get("TableName")
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,62}", name):
                raise AdminError(
                    502, "UncertainServiceResult", "Table accepted request without a valid identity"
                )
            identifier = name
            read_path = path
        elif action.response_kind == "entity":
            body = response.json()

            def key(name):
                return quote(body[name].replace("'", "''"), safe="'")

            identifier = read_path = (
                path + "(PartitionKey='" + key("PartitionKey") + "',RowKey='" + key("RowKey") + "')"
            )
        elif action.method == "POST":
            body = response.json()
            identifier = body.get("id")
            if not isinstance(identifier, str) or not identifier or len(identifier) > 2048:
                raise AdminError(
                    502,
                    "UncertainServiceResult",
                    "Service accepted the request but returned no resource ID",
                )
            read_path = path + "/" + quote(identifier, safe="")
        elif action.service == "keyvault":
            # Never retain a value-bearing response. Construct the alias from the expected path.
            identifier = response.json().get("id", path)
            version = urlsplit(identifier).path.rsplit("/", 1)[-1]
            if re.fullmatch(r"[a-fA-F0-9]{32}", version):
                read_path = path + "/" + version
        if self.base.describe().key == "monitor.workspaces":
            try:
                identifier = str(UUID(response.json()["properties"]["customerId"]))
            except (KeyError, ValueError, TypeError):
                raise AdminError(
                    502, "UncertainServiceResult", "Workspace returned no query identity"
                ) from None
        item = ResourceItem(
            id=identifier,
            parent=None,
            version=response.headers.get("etag", "public"),
            properties={
                "service": action.service,
                "read_path": read_path,
                "parameters": self.read_params(action),
                "read_method": "GET" if action.binary else self.read_method(action),
                "response_kind": action.response_kind,
                "headers": self.read_headers(action),
                "identity_ref": identity,
                "status_code": response.status_code,
                "public_action": True,
                "shared_key": action.shared_key,
            },
        )
        if action.response_kind in {"json", "entity"} and not action.binary:
            try:
                item.properties["response_fingerprint"] = fingerprint(response.json())
            except ValueError:
                pass
        if action.response_kind == "table":
            item.properties["parameters"] = {
                "$filter": "TableName eq '" + identifier + "'",
                "$top": "1",
            }
        return BatchResult(
            items=[item],
            effects={"created": int(not unchanged), "updated": 0, "unchanged": int(unchanged)},
        )

    async def verify(self, ctx, result):
        if not any(item.properties.get("public_action") for item in result.items):
            return await self.base.verify(ctx, result)
        count = 0
        for item in result.items:
            p = item.properties
            transport = await self.transport(ctx, p["identity_ref"])
            transport.shared_key = p.get("shared_key", False)
            response = await transport.request(
                p["service"],
                p.get("read_method", "GET"),
                p["read_path"],
                params=p["parameters"],
                headers=p["headers"],
                digest="content_sha256" in p,
            )
            if response.status_code in {404, 202}:
                continue
            self.check(response)
            if (
                "response_fingerprint" in p
                and fingerprint(response.json()) != p["response_fingerprint"]
            ):
                return VerificationResult(
                    status="failed",
                    expected=len(result.items),
                    actual=count,
                    errors=["Public resource changed after the recorded operation"],
                )
            if "content_sha256" in p and response.json() != {
                "sha256": p["content_sha256"],
                "size": p["content_size"],
            }:
                return VerificationResult(
                    status="failed",
                    expected=len(result.items),
                    actual=count,
                    errors=["Public content digest or length changed"],
                )
            if p.get("response_kind") == "queue" and item.id not in [
                node.text for node in ET.fromstring(response.content).findall(".//MessageId")
            ]:
                continue
            if p.get("response_kind") == "table" and not any(
                row.get("TableName") == item.id for row in response.json().get("value", [])
            ):
                continue
            count += 1
        return VerificationResult(
            status="succeeded" if count == len(result.items) else "pending",
            expected=len(result.items),
            actual=count,
        )


def attach_public_actions(entries):
    actions = {
        "teams.chat": [
            Action("create", "graph", "POST", "/v1.0/chats", "v1.0", graph_type="chat"),
        ],
        "teams.conversationMember": [
            Action(
                "add-" + kind,
                "graph",
                "POST",
                path + "/members",
                "v1.0",
                graph_type="aadUserConversationMember",
            )
            for kind, path in (
                ("team", "/v1.0/teams/{team}"),
                ("channel", "/v1.0/teams/{team}/channels/{channel}"),
                ("chat", "/v1.0/chats/{chat}"),
            )
        ],
        "teams.chatMessage": [
            Action("send-" + kind, "graph", "POST", path, "v1.0", graph_type="chatMessage")
            for kind, path in (
                ("channel", "/v1.0/teams/{team}/channels/{channel}/messages"),
                ("reply", "/v1.0/teams/{team}/channels/{channel}/messages/{message}/replies"),
                ("chat", "/v1.0/chats/{chat}/messages"),
            )
        ],
        "storage.messages": [
            Action(
                "enqueue",
                "queue",
                "POST",
                "/{account}/{queue}/messages",
                "2026-02-06",
                headers={"x-ms-version": "2026-02-06"},
                response_kind="queue",
            )
        ],
        "storage.tables": [
            Action(
                "create",
                "table",
                "POST",
                "/{account}/Tables",
                "2019-02-02",
                headers={
                    "x-ms-version": "2019-02-02",
                    "Accept": "application/json;odata=minimalmetadata",
                },
                response_kind="table",
            )
        ],
        "storage.entities": [
            Action(
                "insert",
                "table",
                "POST",
                "/{account}/{table}",
                "2019-02-02",
                headers={
                    "x-ms-version": "2019-02-02",
                    "Accept": "application/json;odata=minimalmetadata",
                    "Prefer": "return-content",
                },
                response_kind="entity",
            )
        ],
        "storage.files": [
            Action(
                "upload",
                "file",
                "PUT",
                "/{account}/{share}/{file}",
                "2025-11-05",
                headers={"x-ms-version": "2025-11-05", "x-ms-file-request-intent": "backup"},
                binary=True,
                response_kind="file",
            )
        ],
        "arm.providers": [
            Action(
                "register",
                "arm",
                "POST",
                "/subscriptions/{subscription}/providers/{namespace}/register",
                "2021-04-01",
                params={"api-version": "2021-04-01"},
                response_kind="registration",
            )
        ],
        "mail.events": [
            Action(
                "create", "graph", "POST", "/v1.0/users/{user}/events", "v1.0", graph_type="event"
            )
        ],
        "mail.messages": [
            Action(
                "create-draft",
                "graph",
                "POST",
                "/v1.0/users/{user}/messages",
                "v1.0",
                graph_type="message",
            )
        ],
        "storage.containers": [
            Action(
                "create",
                "storage",
                "PUT",
                "/{account}/{container}",
                "2025-11-05",
                params={"restype": "container"},
                headers={"x-ms-version": "2025-11-05"},
            )
        ],
        "storage.shares": [
            Action(
                "create",
                "file",
                "PUT",
                "/{account}/{share}",
                "2025-11-05",
                params={"restype": "share"},
                headers={"x-ms-version": "2025-11-05"},
                response_kind="namespace",
                shared_key=True,
            )
        ],
        "storage.blobs": [
            Action(
                "upload",
                "storage",
                "PUT",
                "/{account}/{container}/{blob}",
                "2025-11-05",
                headers={"x-ms-version": "2025-11-05", "x-ms-blob-type": "BlockBlob"},
                binary=True,
            )
        ],
        "storage.queues": [
            Action(
                "create",
                "queue",
                "PUT",
                "/{account}/{queue}",
                "2026-02-06",
                headers={"x-ms-version": "2026-02-06"},
                response_kind="namespace",
            )
        ],
        "keyvault.secrets": [
            Action(
                "set",
                "keyvault",
                "PUT",
                "/{vault}/secrets/{secret}",
                "2025-07-01",
                params={"api-version": "2025-07-01"},
            )
        ],
        "storage.accounts": [
            Action(
                "create",
                "arm",
                "PUT",
                "{resource}",
                "2025-06-01",
                params={"api-version": "2025-06-01"},
                provider="Microsoft.Storage/storageAccounts",
            )
        ],
        "keyvault.vaults": [
            Action(
                "create",
                "arm",
                "PUT",
                "{resource}",
                "2025-05-01",
                params={"api-version": "2025-05-01"},
                provider="Microsoft.KeyVault/vaults",
            )
        ],
        "monitor.workspaces": [
            Action(
                "create",
                "arm",
                "PUT",
                "{resource}",
                "2025-02-01",
                params={"api-version": "2025-02-01"},
                provider="Microsoft.OperationalInsights/workspaces",
            )
        ],
        "monitor.diagnostics": [
            Action(
                "create",
                "arm",
                "PUT",
                "{resource}",
                "2021-05-01-preview",
                params={"api-version": "2021-05-01-preview"},
                provider="Microsoft.Insights/diagnosticSettings",
            )
        ],
    }
    return [
        PublicActions(item, actions[item.describe().key])
        if item.describe().key in actions
        else item
        for item in entries
    ]
