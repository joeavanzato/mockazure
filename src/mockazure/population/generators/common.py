"""Deterministic random-access construction; never materializes the whole dataset."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

from mockazure.profiles.identity import stable_id


class Generation:
    def __init__(self, parameters, refs, offset, limit):
        self.parameters, self.refs = parameters, refs
        self.tenant = UUID(parameters["tenant"])
        self.namespace = parameters["namespace"]
        self.seed = parameters.get("seed", 0)
        self.count = parameters.get("count", 1)
        self.users = parameters.get("users", 20)
        self.days = parameters.get("days", 7)
        if (
            not isinstance(self.namespace, str)
            or not 1 <= len(self.namespace) <= 128
            or any(
                type(x) is not int
                for x in (self.seed, self.count, self.users, self.days, offset, limit)
            )
            or not -(2**63) <= self.seed < 2**63
            or not 0 <= self.count <= 1000000
            or not 1 <= self.users <= 100000
            or not 1 <= self.days <= 3660
            or not 0 <= offset <= 1000000
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Generator bounds exceeded")
        self.reference = datetime.fromisoformat(parameters["referenceTime"].replace("Z", "+00:00"))
        if self.reference.tzinfo is None:
            raise ValueError("Reference time requires a timezone")
        self.reference = self.reference.astimezone(UTC)
        self.indexes = range(offset, min(self.count, offset + limit))
        self.label = hashlib.sha256(f"{self.namespace}:{self.seed}".encode()).hexdigest()[:10]

    def id(self, family, index=0):
        return str(stable_id(self.tenant, self.namespace, f"{family}:{index}", self.seed))

    def stamp(self, index=0):
        interval = self.days * 86400 * 1000000
        delta = interval * index // max(self.count, 1)
        return (
            (self.reference - timedelta(days=self.days) + timedelta(microseconds=delta))
            .isoformat()
            .replace("+00:00", "Z")
        )

    def upn(self, index):
        return f"lab-{self.label}-{index:06d}@{self.refs.get('domain', 'lab.test')}"
