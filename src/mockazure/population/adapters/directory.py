"""Directory fixture creation through the existing validated dataset importer."""

from mockazure.directory.datasets import DirectoryDataset, PublicGroup, PublicUser, import_directory
from mockazure.directory.models import ObjectVersion
from mockazure.population.adapters.base import PayloadAdapter, ReadSpec
from mockazure.population.types import FamilyDescriptor, OperationDescriptor


def adapters():
    result = []
    for family, kind, model in (("users", "user", PublicUser), ("groups", "group", PublicGroup)):

        def normalize(payload, model=model):
            return model.model_validate(payload).model_dump(mode="json", exclude_unset=True)

        async def write(session, ctx, pending, family=family):
            dataset = DirectoryDataset.model_validate(
                {
                    "schema_version": 1,
                    family: [payload for _, payload in pending],
                    "provenance": {"source": "MockAzure profiles"},
                }
            )
            await import_directory(session, ctx.tenant_id, dataset)

        descriptor = FamilyDescriptor(
            key=f"directory.{family}",
            service="Entra",
            api_path=f"/v1.0/{family}",
            display_fields=["id", "displayName", "mailNickname"],
            operations=[
                OperationDescriptor(
                    key="import",
                    mode="fixture",
                    method="POST",
                    path_template="/_mock/tenants/{tenant}/directory/import",
                    api_version="1",
                    input_schema=model.model_json_schema(),
                )
            ],
            contract_refs=["integrations/contracts/graph-directory.md"],
            limitations=[
                "Fixture import is create-only; existing different resources are conflicts."
            ],
        )
        result.append(
            PayloadAdapter(
                descriptor,
                ReadSpec(
                    ObjectVersion,
                    ObjectVersion.object_id,
                    ObjectVersion.properties,
                    ObjectVersion.valid_from,
                    (
                        ObjectVersion.kind == kind,
                        ObjectVersion.valid_to.is_(None),
                        ObjectVersion.removed.is_(False),
                    ),
                ),
                normalize,
                write,
            )
        )
    return result
