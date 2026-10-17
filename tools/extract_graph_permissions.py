"""Extract supported permission IDs from a pinned official Graph REST reference."""

import argparse
import hashlib
import json
import re
from pathlib import Path
from uuid import UUID

from mockazure.authorization.catalog import CATALOG

SECURITY_PERMISSIONS = {
    "Application.ReadWrite.OwnedBy",
    "AppRoleAssignment.ReadWrite.All",
    "DelegatedPermissionGrant.ReadWrite.All",
    "Device.Read.All",
    "Device.ReadWrite.All",
    "RoleManagement.Read.Directory",
    "RoleManagement.ReadWrite.Directory",
    "UserAuthenticationMethod.Read.All",
    "UserAuthenticationMethod.ReadWrite.All",
    "Policy.Read.All",
    "Policy.ReadWrite.ConditionalAccess",
}


def extract(source: str, deployments: dict) -> list[dict]:
    names = {p.name for p in CATALOG if p.resource == "graph"} | SECURITY_PERMISSIONS
    sections = dict(re.findall(r"^### ([^\n]+)\n(.*?)(?=^### |\Z)", source, re.M | re.S))
    entries = []
    for name in sorted(names):
        if name not in sections:
            if name not in {"Group.ReadBasic.All", "GroupMember.ReadBasic.All"}:
                raise ValueError(f"Permission missing from reference: {name}")
            for row in deployments["permissionDeployments"][name]:
                kinds = {"Application": "application", "DelegatedWork": "delegated"}
                if row["environment"] != "public" or row["scheme"] not in kinds:
                    continue
                if not row["isEnabled"]:
                    continue
                entries.append(
                    {
                        "name": name,
                        "kind": kinds[row["scheme"]],
                        "id": str(UUID(row["id"])),
                        "adminConsentRequired": True,
                        "source": "provisioningInfo.json",
                    }
                )
            continue
        section = sections[name]
        ids = re.search(r"^\| Identifier \| ([^|]+)\| ([^|]+)\|", section, re.M)
        consent = re.search(r"^\| AdminConsentRequired \| ([^|]+)\| ([^|]+)\|", section, re.M)
        if ids is None or consent is None:
            raise ValueError(f"Incomplete reference table: {name}")
        for index, kind in enumerate(("application", "delegated"), start=1):
            identifier = ids.group(index).strip()
            if identifier == "-":
                continue
            entries.append(
                {
                    "name": name,
                    "kind": kind,
                    "id": str(UUID(identifier)),
                    "adminConsentRequired": consent.group(index).strip() == "Yes",
                }
            )
    return entries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--deployments", required=True, type=Path)
    parser.add_argument("--deployment-revision", required=True)
    args = parser.parse_args()
    if re.fullmatch(r"[0-9a-f]{40}", args.revision) is None:
        raise ValueError("A pinned full Git revision is required")
    data = {
        "repository": "https://github.com/microsoftgraph/microsoft-graph-docs-contrib",
        "revision": args.revision,
        "source": "concepts/permissions-reference.md",
        "sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "supplement": {
            "repository": "https://github.com/microsoftgraph/microsoft-graph-devx-content",
            "revision": args.deployment_revision,
            "source": "permissions/provisioningInfo.json",
            "sha256": hashlib.sha256(args.deployments.read_bytes()).hexdigest(),
            "note": "Two existing basic group permissions absent from the REST reference. "
            "Source resourceAppId metadata is not used as a Graph resource identity.",
        },
        "permissions": extract(
            args.input.read_text(encoding="utf-8"),
            json.loads(args.deployments.read_text(encoding="utf-8")),
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(data['permissions'])} official Graph permission identifiers.")


if __name__ == "__main__":
    main()
