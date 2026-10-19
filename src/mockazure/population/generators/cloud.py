"""Tenant-bound Azure inventory and typed historical logs."""

from mockazure.population.generators.common import Generation
from mockazure.population.types import Mutation


def generate(name, parameters, refs, *, offset, limit):
    g = Generation(parameters, refs, offset, limit)
    values = []
    for i in g.indexes:
        parent = None
        if name == "cloud.inventory":
            vm_name = "lab-vm-" + g.label + "-" + str(i)
            value = {
                "id": refs["group"] + "/providers/Microsoft.Compute/virtualMachines/" + vm_name,
                "name": vm_name,
                "type": "Microsoft.Compute/virtualMachines",
                "location": "eastus",
                "tags": {"dataset": g.namespace, "purpose": "synthetic-inventory"},
            }
        elif name == "cloud.logs":
            parent = refs["workspace"] + "/StorageBlobLogs"
            value = {
                "id": g.id(name, i),
                "record": {
                    "TimeGenerated": g.stamp(i),
                    "OperationName": "GetBlob",
                    "AccountName": refs.get("account", "local"),
                    "DurationMs": i % 20,
                },
            }
        else:
            raise ValueError("Unknown cloud generator")
        values.append(
            Mutation(operation="import", logical_key=f"{name}:{i}", parent=parent, payload=value)
        )
    return values
