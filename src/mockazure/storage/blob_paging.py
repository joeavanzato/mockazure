"""Opaque, reset-fenced Blob enumeration over bounded SQL batches."""

from datetime import UTC, timedelta
from email.utils import format_datetime
from uuid import UUID
from xml.etree.ElementTree import Element, SubElement

from sqlalchemy import and_, or_, select, tuple_

from mockazure.cloud.models import CloudState
from mockazure.providers.storage.errors import StorageError
from mockazure.storage.blob_models import Blob, BlobCursor, BlobVersion
from mockazure.storage.models import StorageNamespace


async def list_blobs(session, access, container, args, now, endpoint):
    if (
        await session.get(
            StorageNamespace, (access.tenant_id, access.account_id, "blob", container)
        )
        is None
    ):
        raise StorageError(404, "ContainerNotFound", "Container was not found")
    maximum = int(args.get("maxresults", "5000"))
    if maximum < 1:
        raise StorageError(400, "InvalidQueryParameterValue", "maxresults must be positive")
    maximum = min(maximum, 5000)
    include = set(args.get("include", "").lower().split(",")) - {""}
    if include - {"metadata", "snapshots", "versions", "deleted"}:
        raise StorageError(400, "InvalidQueryParameterValue", "Unsupported Blob listing include")
    prefix = args.get("prefix", "")
    delimiter = args.get("delimiter", "")
    if len(prefix) > 1024 or len(delimiter) > 1024:
        raise StorageError(
            400, "InvalidQueryParameterValue", "Prefix or delimiter exceeds name limits"
        )
    query = {
        "prefix": prefix,
        "delimiter": delimiter,
        "include": sorted(include),
        "maxresults": maximum,
    }
    generation = (await session.get(CloudState, access.tenant_id)).generation
    last = None
    if args.get("marker"):
        try:
            identifier = UUID(args["marker"])
        except ValueError:
            raise StorageError(400, "InvalidMarker", "Invalid Blob continuation") from None
        cursor = await session.get(BlobCursor, (access.tenant_id, identifier))
        if (
            cursor is None
            or cursor.account_id != access.account_id
            or cursor.container != container
            or cursor.generation != generation
            or cursor.query != query
            or cursor.expires_at <= now
        ):
            raise StorageError(
                400, "InvalidMarker", "Blob continuation is expired or does not match this request"
            )
        last = cursor.last_key
    name_column = Blob.name.collate("C")
    selection = or_(
        Blob.current_id == BlobVersion.id,
        and_(BlobVersion.kind == "version", ("versions" in include)),
        and_(BlobVersion.kind == "snapshot", ("snapshots" in include)),
        and_(
            BlobVersion.kind == "version",
            BlobVersion.deleted_at.is_not(None),
            ("deleted" in include),
        ),
    )
    base = (
        select(Blob, BlobVersion)
        .join(
            BlobVersion,
            and_(BlobVersion.tenant_id == Blob.tenant_id, BlobVersion.blob_id == Blob.id),
        )
        .where(
            Blob.tenant_id == access.tenant_id,
            Blob.account_id == access.account_id,
            Blob.container == container,
            Blob.name.startswith(prefix, autoescape=True),
            selection,
        )
    )
    if "deleted" not in include:
        base = base.where(BlobVersion.deleted_at.is_(None))
    if last:
        if last[1] == "prefix":
            base = base.where(
                name_column > last[0], ~Blob.name.startswith(last[0], autoescape=True)
            )
        else:
            base = base.where(
                tuple_(name_column, BlobVersion.kind, BlobVersion.stamp) > tuple(last)
            )
    entries = []
    seen = set()
    scan = None
    while len(entries) <= maximum:
        batch = base
        if scan:
            batch = batch.where(
                tuple_(name_column, BlobVersion.kind, BlobVersion.stamp) > tuple(scan)
            )
        rows = (
            await session.execute(
                batch.order_by(name_column, BlobVersion.kind, BlobVersion.stamp).limit(1000)
            )
        ).all()
        if not rows:
            break
        for blob, version in rows:
            scan = [blob.name, version.kind, version.stamp]
            suffix = blob.name[len(prefix) :]
            if delimiter and delimiter in suffix:
                display = prefix + suffix.split(delimiter, 1)[0] + delimiter
                if display in seen:
                    continue
                seen.add(display)
                entries.append((display, None, None, [display, "prefix", ""]))
            else:
                entries.append((blob.name, blob, version, [blob.name, version.kind, version.stamp]))
            if len(entries) > maximum:
                break
        if len(rows) < 1000:
            break
    xml = Element(
        "EnumerationResults",
        {"ServiceEndpoint": endpoint.rsplit("/", 1)[0] + "/", "ContainerName": container},
    )
    for tag, value in [
        ("Prefix", prefix),
        ("Marker", args.get("marker", "")),
        ("MaxResults", str(maximum)),
        ("Delimiter", delimiter),
    ]:
        SubElement(xml, tag).text = value
    group = SubElement(xml, "Blobs")
    for display, blob, version, key in entries[:maximum]:
        if blob is None:
            node = SubElement(group, "BlobPrefix")
            SubElement(node, "Name").text = display
            continue
        node = SubElement(group, "Blob")
        SubElement(node, "Name").text = display
        if version.kind == "snapshot":
            SubElement(node, "Snapshot").text = version.stamp
        elif "versions" in include:
            SubElement(node, "VersionId").text = version.stamp
            SubElement(node, "IsCurrentVersion").text = str(blob.current_id == version.id).lower()
        if version.deleted_at is not None:
            SubElement(node, "Deleted").text = "true"
        props = SubElement(node, "Properties")
        for tag, value in [
            ("Last-Modified", format_datetime(version.created_at.astimezone(UTC), usegmt=True)),
            ("Etag", version.etag),
            ("Content-Length", str(version.size)),
            ("Content-Type", version.properties.get("content_type", "application/octet-stream")),
            ("BlobType", "BlockBlob"),
        ]:
            SubElement(props, tag).text = value
        if version.properties.get("content_md5"):
            SubElement(props, "Content-MD5").text = version.properties["content_md5"]
        if "metadata" in include:
            meta = SubElement(node, "Metadata")
            for key, value in version.properties.get("metadata", {}).items():
                SubElement(meta, key).text = value
    marker = ""
    if len(entries) > maximum:
        row = BlobCursor(
            tenant_id=access.tenant_id,
            account_id=access.account_id,
            container=container,
            generation=generation,
            query=query,
            last_key=entries[maximum - 1][3],
            expires_at=now + timedelta(hours=1),
        )
        session.add(row)
        await session.flush()
        marker = str(row.id)
    SubElement(xml, "NextMarker").text = marker
    return xml
