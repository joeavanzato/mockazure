"""Graph v1.0 workload fixtures; required defaults come from pinned CSDL."""

from mockazure.population.generators.common import Generation
from mockazure.population.generators.schema_defaults import payload as schema_payload
from mockazure.population.types import Mutation


def generate(name, parameters, refs, *, offset, limit):
    g = Generation(parameters, refs, offset, limit)
    values = []
    for i in g.indexes:
        identifier, parent = g.id(name, i), None
        times = {"createdDateTime": g.stamp(0), "lastModifiedDateTime": g.stamp(i)}
        if name == "workloads.sites":
            identifier = (
                refs.get("domain", "lab.test") + "," + identifier + "," + g.id("site-web", i)
            )
            parent, value = identifier, {**times, "id": identifier, "displayName": f"Lab site {i}"}
        elif name == "workloads.drives":
            parent, value = (
                refs["site"],
                {
                    **times,
                    "id": identifier,
                    "name": f"Lab documents {i}",
                    "driveType": "documentLibrary",
                },
            )
        elif name == "workloads.teams":
            parent, value = identifier, {"id": identifier, "displayName": f"Lab team {i}"}
        elif name == "workloads.channels":
            parent, value = (
                refs["team"],
                {"id": identifier, "displayName": f"Lab channel {i}", "membershipType": "standard"},
            )
        elif name == "workloads.devices":
            parent, value = (
                "deviceManagement",
                schema_payload(
                    "managedDevice",
                    identifier,
                    reference_time=g.stamp(i),
                    deviceName=f"LAB-{g.label}-{i}",
                    operatingSystem="Windows",
                    osVersion="10.0.19045",
                ),
            )
        else:
            raise ValueError("Unknown workload generator")
        operation = "import"
        if name == "workloads.channels":
            operation = "import-linked"
            value = {
                "payload": value,
                "edges": [
                    {
                        "source": {"kind": "team", "id": parent},
                        "relation": "channels",
                        "target": {"kind": "channel", "id": identifier},
                    }
                ],
            }
        values.append(
            Mutation(operation=operation, logical_key=f"{name}:{i}", parent=parent, payload=value)
        )
    return values
