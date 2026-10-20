from mockazure.population.corpus.models import ContentSpec
from mockazure.population.corpus.render import render
from mockazure.population.generators.business import edge, linked
from mockazure.population.types import Mutation


def item(g, kind, i):
    if g.count > g.projects:
        raise ValueError("Project fixture count exceeds organization projects")
    site, drive, root, folder, document = (
        g.site(i),
        g.id("drive", i),
        g.id("root", i),
        g.id("folder", i),
        g.id("document", i),
    )
    times = {"createdDateTime": g.time(), "lastModifiedDateTime": g.time()}
    if kind == "site-access":
        return Mutation(
            operation="grant",
            logical_key=f"business.site-access:{i}",
            payload={
                "id": g.id("site-grant", i),
                "scope": site,
                "principal_kind": "user",
                "principal_id": g.person(i)["id"],
                "role": "write",
                "display_name": g.person(i)["displayName"],
            },
        )
    if kind == "sites":
        return Mutation(
            operation="import",
            logical_key=f"business.sites:{i}",
            parent=site,
            payload={
                **times,
                "id": site,
                "name": f"project-{i}",
                "displayName": f"Project {i + 1}",
                "webUrl": f"https://{g.domain}/sites/project-{i}",
            },
        )
    if kind == "drives":
        return linked(
            kind,
            i,
            site,
            {**times, "id": drive, "name": "Documents", "driveType": "documentLibrary"},
            [edge("site", site, "drives", "drive", drive)],
        )
    if kind in {"roots", "folders"}:
        identifier = root if kind == "roots" else folder
        return linked(
            kind,
            i,
            site,
            {
                **times,
                "id": identifier,
                "name": "root" if kind == "roots" else "Project documents",
                "folder": {},
            },
            [edge("drive", drive, "root", "driveItem", root)]
            if kind == "roots"
            else [edge("driveItem", root, "children", "driveItem", folder)],
        )
    if kind == "file-permissions":
        identifier = g.id("file-permission", i)
        return linked(
            kind,
            i,
            site,
            {
                "id": identifier,
                "roles": ["write"],
                "grantedToV2": {
                    "user": {"id": g.person(i)["id"], "displayName": g.person(i)["displayName"]}
                },
            },
            [edge("driveItem", document, "permissions", "permission", identifier)],
        )
    spec = g.document(i, 1 if kind == "versions" else 2)
    content = render(ContentSpec.model_validate(spec))
    if kind == "versions":
        identifier = g.id("document-version-1", i)
        return linked(
            kind,
            i,
            site,
            {
                "id": identifier,
                "size": content.length,
                "lastModifiedDateTime": g.time(),
                "lastModifiedBy": {"user": {"id": g.person(i)["id"]}},
            },
            [edge("driveItem", document, "versions", "driveItemVersion", identifier)],
            document=spec,
        )
    return linked(
        kind,
        i,
        site,
        {
            **times,
            "id": document,
            "name": content.filename,
            "size": content.length,
            "file": {"mimeType": content.media_type},
            "webUrl": f"https://{g.domain}/sites/project-{i}/documents/review",
            "createdBy": {"user": {"id": g.person(i)["id"]}},
            "lastModifiedBy": {"user": {"id": g.person(i)["id"]}},
        },
        [edge("driveItem", folder, "children", "driveItem", document)],
        document=spec,
    )
