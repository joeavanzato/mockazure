"""Hash-verified source inventory; loading and verification never use the network."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from mockazure.living_tenant.models import OperationContract

DATA = Path(__file__).parent / "data"
REPOSITORIES = {
    "microsoftgraph/microsoft-graph-docs-contrib",
    "microsoftgraph/msgraph-metadata",
    "MicrosoftDocs/azure-docs",
    "MicrosoftDocs/defender-docs",
    "MicrosoftDocs/office-docs-powershell",
    "Azure/azure-rest-api-specs",
}


def validate_source(source: dict) -> None:
    if set(source) != {"path", "url", "revision", "sha256"}:
        raise ValueError("Invalid source descriptor")
    path, revision = source["path"], source["revision"]
    if (
        not isinstance(path, str)
        or not path
        or "\\" in path
        or ":" in path
        or PurePosixPath(path).is_absolute()
        or any(p in {".", "..", ""} for p in path.split("/"))
        or not isinstance(revision, str)
        or not re.fullmatch(r"[0-9a-f]{40}", revision)
        or not isinstance(source["sha256"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", source["sha256"])
    ):
        raise ValueError("Invalid pinned source path/hash")
    parsed = urlsplit(source["url"])
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.scheme != "https"
        or parsed.netloc != "raw.githubusercontent.com"
        or parsed.query
        or parsed.fragment
        or len(parts) < 4
        or "/".join(parts[:2]) not in REPOSITORIES
        or parts[2] != revision
        or any(p in {".", "..", ""} for p in parts)
        or "%" in parsed.path
        or "\\" in parsed.path
    ):
        raise ValueError("Untrusted contract source")


def read_manifest(root: Path = DATA):
    value = json.loads((root / "operations.json").read_text("utf-8"))
    if (
        not isinstance(value, dict)
        or set(value) - {"sources", "operations", "catalogs"}
        or not {"sources", "operations"} <= value.keys()
    ):
        raise ValueError("Invalid operation manifest")
    if not isinstance(value["sources"], dict) or not isinstance(value["operations"], list):
        raise ValueError("Invalid operation manifest collections")
    return value


def expand_operations(manifest):
    """Read the existing hash-pinned catalog, rather than freeze a second divergent copy."""
    from mockazure.cloud import contracts as cloud

    result = [OperationContract.model_validate(row) for row in manifest["operations"]]
    catalogs = manifest.get("catalogs", {})
    if not isinstance(catalogs, dict):
        raise ValueError("Invalid catalog references")
    for service, digest in catalogs.items():
        if service not in {
            "arm-providers",
            "exchange",
            "graph-workflows",
            "keyvault",
            "queries",
            "storage",
        }:
            raise ValueError("Unknown referenced catalog")
        path = cloud.DATA / service / "operations.json"
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Referenced catalog hash mismatch")
        cloud.verify_references(service)
        for row in cloud._catalog(service)["operations"]:
            permission = row.get("permissions", {})
            if isinstance(permission, dict):
                permission = sorted({p for values in permission.values() for p in values})
            path = row.get("path", "/adminapi/{version}/{tenant}/InvokeCommand")
            if service == "graph-workflows":
                path = "/v1.0" + path
            result.append(
                OperationContract(
                    key=f"cloud.{service}.{row['operationId']}",
                    area="evidence" if service == "queries" else "content",
                    method=row.get("method", "POST"),
                    path=path,
                    status="existing",
                    permissions=[] if service == "exchange" else permission,
                    role_constraints=permission if service == "exchange" else [],
                    source_refs=[f"catalog:{service}"],
                    supported_variants=[row["operationId"]] if service == "exchange" else [],
                    limitations=[
                        "Existing bounded operation profile; consult its source catalog "
                        "and COVERAGE.md for field, permission and qualification limits."
                    ],
                )
            )
    return result


def verify_references(*, root: Path = DATA, area: str | None = None) -> dict[str, int]:
    root = root.resolve()
    manifest = read_manifest(root)
    operations = expand_operations(manifest)
    if len({o.key for o in operations}) != len(operations):
        raise ValueError("Duplicate operation key")
    if area is not None and area not in {"content", "evidence", "identity", "hunting"}:
        raise ValueError("Unknown operation area")
    for operation in operations:
        allowed = set(manifest["sources"]) | {f"catalog:{s}" for s in manifest.get("catalogs", {})}
        if not set(operation.source_refs) <= allowed:
            raise ValueError("Missing operation source reference")
    selected = [o for o in operations if area is None or o.area == area]
    sources = (
        set(manifest["sources"])
        if area is None
        else {key for operation in selected for key in operation.source_refs}
    )
    for key in sources:
        if key.startswith("catalog:"):
            continue
        source = manifest["sources"][key]
        validate_source(source)
        target = (root / source["path"]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise ValueError("Missing or escaped contract source")
        if hashlib.sha256(target.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Contract source hash mismatch")
    return {
        "operations": len(selected),
        "sources": sum(not key.startswith("catalog:") for key in sources),
        "catalogs": len(manifest.get("catalogs", {})),
    }


@lru_cache(maxsize=1)
def _operations():
    verify_references()
    return {row.key: row for row in expand_operations(read_manifest())}


def list_operations() -> list[OperationContract]:
    return [value.model_copy(deep=True) for value in _operations().values()]


def load_operation(key: str) -> OperationContract:
    if key not in _operations():
        raise ValueError("Unknown living tenant operation")
    return _operations()[key].model_copy(deep=True)
