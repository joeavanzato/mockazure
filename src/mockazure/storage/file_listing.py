"""Account share/snapshot listing with bounded SQL pagination."""

import json
from datetime import UTC, timedelta
from email.utils import format_datetime
from uuid import UUID
from xml.etree.ElementTree import Element, SubElement

from sqlalchemy import literal, select, tuple_

from mockazure.cloud.models import CloudState
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.file_models import FileCursor, FileTree
from mockazure.storage.models import StorageNamespace


async def list_share_snapshots(session, request, access, now):
    from mockazure.providers.storage.routes import xml_response

    args = request.query_params
    anchor = await session.scalar(
        select(FileTree.id)
        .where(
            FileTree.tenant_id == access.tenant_id,
            FileTree.account_id == access.account_id,
            FileTree.stamp == "",
        )
        .order_by(FileTree.share)
        .limit(1)
    )
    if anchor is None and not args.get("marker"):
        return None
    maximum = int(args.get("maxresults", "5000"))
    if not 1 <= maximum <= 5000:
        raise StorageError(400, "InvalidQueryParameterValue", "Invalid share page size")
    query = {
        "kind": "share-snapshots",
        "prefix": args.get("prefix", ""),
        "maxresults": maximum,
        "include": sorted(args.get("include", "").lower().split(",")),
    }
    generation = (await session.get(CloudState, access.tenant_id)).generation
    active = select(
        StorageNamespace.name.label("name"),
        literal("").label("stamp"),
        StorageNamespace.changed_at.label("changed"),
        StorageNamespace.etag.label("etag"),
        StorageNamespace.properties.label("properties"),
    ).where(
        StorageNamespace.tenant_id == access.tenant_id,
        StorageNamespace.account_id == access.account_id,
        StorageNamespace.service == "file",
    )
    snapshots = (
        select(
            FileTree.share.label("name"),
            FileTree.stamp.label("stamp"),
            FileTree.created_at.label("changed"),
            StorageNamespace.etag.label("etag"),
            FileTree.properties.label("properties"),
        )
        .join(
            StorageNamespace,
            (StorageNamespace.tenant_id == FileTree.tenant_id)
            & (StorageNamespace.account_id == FileTree.account_id)
            & (StorageNamespace.service == "file")
            & (StorageNamespace.name == FileTree.share),
        )
        .where(
            FileTree.tenant_id == access.tenant_id,
            FileTree.account_id == access.account_id,
            FileTree.stamp != "",
        )
    )
    union = active.union_all(snapshots).subquery()
    statement = select(union).where(union.c.name.startswith(query["prefix"], autoescape=True))
    if args.get("marker"):
        try:
            cursor = await session.get(FileCursor, (access.tenant_id, UUID(args["marker"])))
        except ValueError:
            cursor = None
        tree = await session.get(FileTree, (access.tenant_id, cursor.tree_id)) if cursor else None
        if (
            cursor is None
            or tree is None
            or tree.account_id != access.account_id
            or cursor.generation != generation
            or cursor.query != query
            or cursor.expires_at <= now
        ):
            raise StorageError(400, "InvalidMarker", "Share continuation is invalid or expired")
        statement = statement.where(
            tuple_(union.c.name, union.c.stamp) > tuple(json.loads(cursor.last_key))
        )
    rows = (
        await session.execute(statement.order_by(union.c.name, union.c.stamp).limit(maximum + 1))
    ).all()
    xml = Element("EnumerationResults", {"ServiceEndpoint": str(request.url).split("?")[0]})
    for tag, value in [
        ("Prefix", query["prefix"]),
        ("Marker", args.get("marker", "")),
        ("MaxResults", str(maximum)),
    ]:
        SubElement(xml, tag).text = value
    group = SubElement(xml, "Shares")
    for row in rows[:maximum]:
        item = SubElement(group, "Share")
        SubElement(item, "Name").text = row.name
        if row.stamp:
            SubElement(item, "Snapshot").text = row.stamp
        props = SubElement(item, "Properties")
        SubElement(props, "Last-Modified").text = format_datetime(
            row.changed.astimezone(UTC), usegmt=True
        )
        SubElement(props, "Etag").text = row.etag
        SubElement(props, "Quota").text = str(row.properties.get("quota", 5120))
        if "metadata" in query["include"]:
            meta = SubElement(item, "Metadata")
            for k, v in row.properties.get("metadata", {}).items():
                SubElement(meta, k).text = v
    marker = ""
    if len(rows) > maximum:
        cursor = FileCursor(
            tenant_id=access.tenant_id,
            tree_id=anchor,
            generation=generation,
            query=query,
            last_key=json.dumps([rows[maximum - 1].name, rows[maximum - 1].stamp]),
            expires_at=now + timedelta(hours=1),
        )
        session.add(cursor)
        await session.flush()
        marker = str(cursor.id)
    SubElement(xml, "NextMarker").text = marker
    return xml_response(request, xml)
