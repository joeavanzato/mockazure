"""Connected business fixtures, independently versioned from older profile generators."""

from mockazure.population.generators.common import Generation
from mockazure.population.organization import OrganizationSpec, entity_at
from mockazure.population.types import Mutation

VERSION = "business-v1"
FAMILIES = {
    "users": "directory.users",
    "mailboxes": "mail.mailboxes",
    "mail-folders": "mail.folders",
    "messages": "mail.messages",
    "events": "mail.events",
    "sites": "sharepoint.site",
    "drives": "sharepoint.drive",
    "roots": "sharepoint.driveItem",
    "folders": "sharepoint.driveItem",
    "documents": "sharepoint.driveItem",
    "versions": "sharepoint.driveItemVersion",
    "file-permissions": "sharepoint.permission",
    "site-access": "sharepoint.access",
    "teams": "teams.team",
    "channels": "teams.channel",
    "team-members": "teams.conversationMember",
    "channel-members": "teams.conversationMember",
    "channel-messages": "teams.chatMessage",
    "channel-replies": "teams.chatMessage",
    "files-folders": "teams.filesFolderReference",
    "chats": "teams.chat",
    "chat-members": "teams.conversationMember",
    "chat-messages": "teams.chatMessage",
}


class Business(Generation):
    def __init__(self, parameters, refs, offset, limit):
        super().__init__(parameters, refs, offset, limit)
        self.spec = OrganizationSpec(
            users=self.users,
            days=self.days,
            seed=self.seed,
            reference_time=self.reference,
            projects=parameters.get("projects", 4),
            departments=parameters.get("departments", 6),
            locations=parameters.get("locations", 3),
        )
        self.projects = self.spec.projects
        self.domain = refs.get("domain", "lab.test")

    def person(self, index):
        entity = entity_at(self.spec, self.tenant, self.namespace, "user", index % self.users)
        data = dict(entity.properties)
        nickname = data["userPrincipalName"].split("@")[0]
        data.update(userPrincipalName=nickname + "@" + self.domain, mailNickname=nickname)
        return data

    def address(self, index):
        user = self.person(index)
        return {"emailAddress": {"address": user["userPrincipalName"], "name": user["displayName"]}}

    def site(self, project):
        return f"{self.domain},{self.id('site-collection', project)},{self.id('site', project)}"

    def time(self, index=0):
        return self.stamp(index % max(self.count, 1))

    def document(self, project, revision=2):
        return {
            "logical_key": f"project:{project}:review",
            "format": self.parameters.get("format", "docx"),
            "theme": ("finance", "hr", "engineering", "sales", "operations", "support")[
                project % 6
            ],
            "seed": self.seed,
            "revision": revision,
            "fields": {
                "project": f"Project {project + 1}",
                "namespace": self.namespace,
                "owner": self.person(project)["displayName"],
            },
        }


def edge(source_kind, source, relation, target_kind, target):
    return {
        "source": {"kind": source_kind, "id": source},
        "relation": relation,
        "target": {"kind": target_kind, "id": target},
    }


def linked(name, index, parent, payload, edges, **extra):
    return Mutation(
        operation="import-linked",
        logical_key=f"business.{name}:{index}",
        parent=parent,
        payload={"payload": payload, "edges": edges, **extra},
    )


def generate(name, parameters, refs, *, offset, limit):
    from mockazure.population.generators import business_files, business_mail, business_teams

    kind = name.removeprefix("business.")
    if kind not in FAMILIES:
        raise ValueError("Unknown business generator")
    g = Business(parameters, refs, offset, limit)
    indexes = g.indexes
    if kind == "mail-folders" and "folder" in parameters:
        if parameters["folder"] not in ("inbox", "sentitems", "drafts") or g.count > g.users:
            raise ValueError("Invalid mailbox folder variant")
        slot = ("inbox", "sentitems", "drafts").index(parameters["folder"])
        indexes = [i * 3 + slot for i in indexes]
    if kind in {"channels", "channel-members", "files-folders"} and "channel_kind" in parameters:
        variants = ("standard", "private", "shared")
        if parameters["channel_kind"] not in variants or g.count > g.projects:
            raise ValueError("Invalid project channel variant")
        indexes = [i * 3 + variants.index(parameters["channel_kind"]) for i in indexes]
    if kind in {"team-members", "chat-members"} and "member_slot" in parameters:
        slot = parameters["member_slot"]
        if type(slot) is not int or slot not in {0, 1} or g.count > g.projects:
            raise ValueError("Invalid member slot")
        members = min(g.users, 2)
        indexes = [i * members + min(slot, members - 1) for i in indexes]
    if (
        kind in {"channel-messages", "channel-replies", "chat-messages"}
        and "message_slot" in parameters
    ):
        slot = parameters["message_slot"]
        if type(slot) is not int or slot not in {0, 1} or g.count > g.projects:
            raise ValueError("Invalid message slot")
        indexes = [i * 2 + slot for i in indexes]
    if kind in {"messages", "events", "mailboxes", "mail-folders"}:
        return [business_mail.item(g, kind, i) for i in indexes]
    if kind in {
        "sites",
        "drives",
        "roots",
        "folders",
        "documents",
        "versions",
        "file-permissions",
        "site-access",
    }:
        return [business_files.item(g, kind, i) for i in g.indexes]
    if kind == "users":
        if g.count > g.users:
            raise ValueError("Business user count exceeds organization size")
        return [
            Mutation(operation="import", logical_key=f"business.users:{i}", payload=g.person(i))
            for i in g.indexes
        ]
    return [business_teams.item(g, kind, i) for i in indexes]
