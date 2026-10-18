"""Deterministic public fixtures and constant-space exact coverage for scale runs."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

NAMESPACE = UUID("2d615aa7-e0df-4af7-84a0-8d09272a683d")
BASE = datetime(2026, 10, 7, 12, tzinfo=UTC)


def bounds(seed, users, records, batch_size):
    if not (
        type(seed) is int
        and -(2**63) <= seed < 2**63
        and type(users) is int
        and 1 <= users <= 100000
        and type(records) is int
        and 1 <= records <= 1000000
        and type(batch_size) is int
        and 1 <= batch_size <= 1000
    ):
        raise ValueError("Scale input exceeds the declared profile")


def domain(seed):
    return f"scale-{seed}.test"


def user_id(seed, index):
    return str(uuid5(NAMESPACE, f"{seed}:user:{index}"))


def record_id(seed, index):
    return str(uuid5(NAMESPACE, f"{seed}:signIn:{index}"))


def upn(seed, index):
    return f"u{index:06d}@{domain(seed)}"


def timestamp(index):
    return (
        (BASE + timedelta(microseconds=index))
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def provenance(seed):
    return {"source": "MockAzure M6 scale", "seed": seed, "generator": "mockazure-scale-v1"}


def iter_directory_batches(seed, users, batch_size=1000):
    bounds(seed, users, 1, batch_size)
    for start in range(0, users, batch_size):
        yield {
            "schema_version": 1,
            "provenance": provenance(seed),
            "users": [
                {
                    "id": user_id(seed, index),
                    "displayName": f"Scale user {index}",
                    "userPrincipalName": upn(seed, index),
                    "mailNickname": f"u{index:06d}",
                    "accountEnabled": True,
                    "createdDateTime": timestamp(0),
                }
                for index in range(start, min(start + batch_size, users))
            ],
        }


def iter_evidence_batches(seed, users, records, batch_size=1000):
    bounds(seed, users, records, batch_size)
    app_id = str(uuid5(NAMESPACE, f"{seed}:application"))
    for start in range(0, records, batch_size):
        batch = []
        for index in range(start, min(start + batch_size, records)):
            subject = index % users
            stamp = timestamp(index)
            payload = {
                "id": record_id(seed, index),
                "createdDateTime": stamp,
                "userId": user_id(seed, subject),
                "userPrincipalName": upn(seed, subject),
                "userDisplayName": f"Scale user {subject}",
                "appId": app_id,
                "appDisplayName": "Scale fixture",
                "ipAddress": "192.0.2.42",
                "isInteractive": True,
                "status": {"errorCode": 0},
            }
            batch.append(
                {
                    "family": "signIn",
                    "api_version": "v1.0",
                    "payload": payload,
                    "occurred_at": stamp,
                    "available_at": stamp,
                    "origin": "historical",
                    "entitlement_snapshot": "P1",
                }
            )
        yield {"schema_version": 1, "provenance": provenance(seed), "records": batch}


class ExactCoverage:
    """Every index must occur exactly once with its independently derived identity."""

    def __init__(self, family, *, seed, users, records):
        if family not in {"user", "signIn"}:
            raise ValueError("Unsupported coverage family")
        bounds(seed, users, max(records, 1), 1)
        self.family, self.seed, self.users = family, seed, users
        self.expected = users if family == "user" else records
        assert self.expected > 0
        self.seen = bytearray((self.expected + 7) // 8)
        self.count, self.digest = 0, 0

    def add(self, row):
        if self.family == "user":
            match = re.fullmatch(
                r"u([0-9]{6})@" + re.escape(domain(self.seed)), row["userPrincipalName"]
            )
            assert match is not None, "Unexpected user principal name"
            index = int(match[1])
            assert row["id"] == user_id(self.seed, index), "Changed directory identity"
        else:
            when = datetime.fromisoformat(row["createdDateTime"].replace("Z", "+00:00"))
            assert when.tzinfo is not None
            delta = when - BASE
            index = delta.days * 86400000000 + delta.seconds * 1000000 + delta.microseconds
            assert row["id"] == record_id(self.seed, index), "Changed evidence identity/time"
            assert row["userId"] == user_id(self.seed, index % self.users)
            assert row["userPrincipalName"] == upn(self.seed, index % self.users)
        assert 0 <= index < self.expected, "Unexpected scale row"
        byte, bit = divmod(index, 8)
        assert not self.seen[byte] & (1 << bit), "Duplicate scale row"
        self.seen[byte] |= 1 << bit
        self.count += 1
        self.digest ^= int.from_bytes(hashlib.sha256(UUID(row["id"]).bytes).digest(), "big")

    def finish(self):
        assert self.count == self.expected, "Missing scale rows"
        return {
            "count": self.count,
            "id_sha256_xor": f"{self.digest:064x}",
            "coverage_bytes": len(self.seen),
        }


def collection_id(kind, seed, index):
    if kind not in {"message", "driveItem"}:
        raise ValueError("Unsupported collection kind")
    return str(uuid5(NAMESPACE, f"{seed}:{kind}:{index}"))


def streamed_block(seed, index):
    """One deterministic MiB, generated without allocating the whole object."""
    return hashlib.sha256(f"{seed}:block:{index}".encode()).digest() * (1024 * 1024 // 32)


def iter_collection_batches(kind, *, seed, count, user, batch_size=500):
    bounds(seed, 1, count, batch_size)
    collection_id(kind, seed, 0)
    if batch_size > 500:
        raise ValueError("Collection imports include at most 500 records and 500 edges")
    for revision, start in enumerate(range(0, count, batch_size), 1):
        if kind == "message":
            yield {
                "mailbox_user_id": user,
                "mode": "content-backed",
                "expected_revision": revision,
                "messages": [
                    {
                        "id": collection_id(kind, seed, i),
                        "folder_id": "inbox",
                        "immutable_id": collection_id("driveItem", seed, i),
                        "payload": {
                            "subject": f"Scale evidence {i}",
                            "receivedDateTime": timestamp(i),
                            "isRead": False,
                        },
                    }
                    for i in range(start, min(start + batch_size, count))
                ],
            }
        else:
            yield {
                "scope": "scale-site",
                "expected_revision": revision,
                "records": [
                    {
                        "kind": kind,
                        "id": collection_id(kind, seed, i),
                        "payload": {
                            "id": collection_id(kind, seed, i),
                            "name": f"Evidence-{i:06d}.bin",
                            "createdDateTime": timestamp(i),
                            "lastModifiedDateTime": timestamp(i),
                            "file": {"mimeType": "application/octet-stream"},
                            "size": 0,
                            "parentReference": {"id": "scale-root", "driveId": "scale-drive"},
                        },
                    }
                    for i in range(start, min(start + batch_size, count))
                ],
                "edges": [
                    {
                        "source": {"kind": "driveItem", "id": "scale-root"},
                        "relation": "children",
                        "target": {"kind": kind, "id": collection_id(kind, seed, i)},
                    }
                    for i in range(start, min(start + batch_size, count))
                ],
            }
