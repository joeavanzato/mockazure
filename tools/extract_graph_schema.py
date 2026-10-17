"""Extract a closed structural subset from Microsoft's pinned CSDL, entirely offline."""

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

EDM = "{http://docs.oasis-open.org/odata/ns/edm}"
ROOTS = (
    "user",
    "group",
    "signIn",
    "directoryAudit",
    "riskyUser",
    "riskDetection",
    "subscribedSku",
    "licenseDetails",
    "assignedLicense",
    "assignedPlan",
    "application",
    "servicePrincipal",
    "device",
    "passwordCredential",
    "keyCredential",
    "appRoleAssignment",
    "oAuth2PermissionGrant",
    "unifiedRoleDefinition",
    "unifiedRoleAssignment",
    "directoryRole",
    "authenticationMethod",
    "passwordAuthenticationMethod",
    "phoneAuthenticationMethod",
    "emailAuthenticationMethod",
    "microsoftAuthenticatorAuthenticationMethod",
    "fido2AuthenticationMethod",
    "conditionalAccessPolicy",
    "namedLocation",
    "ipNamedLocation",
    "iPv4CidrRange",
    "iPv6CidrRange",
)


def extract(path):
    tree = ET.parse(path)
    types = {}
    for schema in tree.iter(EDM + "Schema"):
        namespace = schema.attrib.get("Namespace", "")
        if not namespace.startswith("microsoft.graph"):
            continue
        for item in schema:
            if item.tag in {EDM + "EntityType", EDM + "ComplexType", EDM + "EnumType"}:
                prefix = namespace.removeprefix("microsoft.graph").lstrip(".")
                types[(prefix + "." if prefix else "") + item.attrib["Name"]] = item
    result = {}

    def collect(name):
        name = name.removeprefix("graph.").removeprefix("microsoft.graph.")
        if name in result:
            return
        node = types[name]
        if node.tag == EDM + "EnumType":
            result[name] = {"enum": [x.attrib["Name"] for x in node.findall(EDM + "Member")]}
            return
        value = {"properties": {}, "required": []}
        result[name] = value
        if base := node.attrib.get("BaseType"):
            value["base"] = base.removeprefix("graph.").removeprefix("microsoft.graph.")
            collect(base)
            base_value = result[base.removeprefix("graph.").removeprefix("microsoft.graph.")]
            value["properties"].update(base_value["properties"])
            value["required"].extend(base_value["required"])
        for prop in node.findall(EDM + "Property"):
            field, kind = prop.attrib["Name"], prop.attrib["Type"]
            nullable = prop.attrib.get("Nullable") != "false"
            value["properties"][field] = {"type": kind, "nullable": nullable}
            if not nullable:
                value["required"].append(field)
            nested = kind.removeprefix("Collection(").removesuffix(")")
            if not nested.startswith("Edm."):
                collect(nested)

    for name in ROOTS:
        collect(name)
    return dict(sorted(result.items()))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--version", choices=["v1.0", "beta"], required=True)
    args = parser.parse_args()
    data = {
        "repository": "https://github.com/microsoftgraph/msgraph-metadata",
        "revision": args.revision,
        "source": f"schemas/{args.version}-Prod.csdl",
        "sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "version": args.version,
        "types": extract(args.input),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(data['types'])} official {args.version} schema types.")


if __name__ == "__main__":
    main()
