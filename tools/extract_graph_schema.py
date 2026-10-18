"""Extract a closed structural subset from Microsoft's pinned CSDL, entirely offline."""

import argparse
import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree as ET

EDM = "{http://docs.oasis-open.org/odata/ns/edm}"
ROOTS = (
    "managedDevice",
    "detectedApp",
    "deviceConfigurationAssignment",
    "deviceConfigurationDeviceStatus",
    "deviceCompliancePolicyAssignment",
    "deviceComplianceDeviceStatus",
    "groupAssignmentTarget",
    "exclusionGroupAssignmentTarget",
    "allDevicesAssignmentTarget",
    "allLicensedUsersAssignmentTarget",
    "windows10GeneralConfiguration",
    "iosGeneralDeviceConfiguration",
    "androidGeneralDeviceConfiguration",
    "macOSGeneralDeviceConfiguration",
    "windows10CompliancePolicy",
    "iosCompliancePolicy",
    "androidCompliancePolicy",
    "macOSCompliancePolicy",
    "team",
    "channel",
    "chat",
    "chatMessage",
    "conversationMember",
    "aadUserConversationMember",
    "chatMessageHostedContent",
    "security.activeDirectoryDomainEvidence",
    "security.aiAgentEvidence",
    "security.amazonResourceEvidence",
    "security.analyzedMessageEvidence",
    "security.azureResourceEvidence",
    "security.blobContainerEvidence",
    "security.blobEvidence",
    "security.cloudApplicationEvidence",
    "security.cloudLogonRequestEvidence",
    "security.cloudLogonSessionEvidence",
    "security.containerEvidence",
    "security.containerImageEvidence",
    "security.containerRegistryEvidence",
    "security.deviceEvidence",
    "security.dnsEvidence",
    "security.fileEvidence",
    "security.fileHashEvidence",
    "security.gitHubOrganizationEvidence",
    "security.gitHubRepoEvidence",
    "security.gitHubUserEvidence",
    "security.googleCloudResourceEvidence",
    "security.hostLogonSessionEvidence",
    "security.ioTDeviceEvidence",
    "security.ipEvidence",
    "security.kubernetesClusterEvidence",
    "security.kubernetesControllerEvidence",
    "security.kubernetesNamespaceEvidence",
    "security.kubernetesPodEvidence",
    "security.kubernetesSecretEvidence",
    "security.kubernetesServiceAccountEvidence",
    "security.kubernetesServiceEvidence",
    "security.mailClusterEvidence",
    "security.mailboxConfigurationEvidence",
    "security.mailboxEvidence",
    "security.malwareEvidence",
    "security.networkConnectionEvidence",
    "security.nicEvidence",
    "security.oauthApplicationEvidence",
    "security.processEvidence",
    "security.registryKeyEvidence",
    "security.registryValueEvidence",
    "security.sasTokenEvidence",
    "security.securityGroupEvidence",
    "security.servicePrincipalEvidence",
    "security.submissionMailEvidence",
    "security.teamsMessageEvidence",
    "security.urlEvidence",
    "security.userEvidence",
    "alert",
    "security.alert",
    "security.incident",
    "secureScore",
    "secureScoreControlProfile",
    "messageRule",
    "security.auditLogQuery",
    "security.auditLogRecord",
    "security.defaultAuditData",
    "user",
    "userRegistrationDetails",
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


def extract(path, additional_roots=()):
    tree = ET.parse(path)
    types = {}
    contexts = {}
    aliases = {}
    for schema in tree.iter(EDM + "Schema"):
        if alias := schema.get("Alias"):
            aliases.setdefault(alias, set()).add(schema.attrib["Namespace"])

    def resolve(kind, context):
        if kind.startswith("Collection("):
            return "Collection(" + resolve(kind[11:-1], context) + ")"
        prefix, _, local = kind.partition(".")
        if prefix in {"Edm", "graph", "microsoft"} or not local:
            return kind
        namespace, alias = context
        if prefix == alias:
            return namespace + "." + local
        targets = aliases.get(prefix, set())
        if len(targets) != 1:
            raise ValueError(f"Unknown or ambiguous CSDL alias: {prefix}")
        return next(iter(targets)) + "." + local

    for schema in tree.iter(EDM + "Schema"):
        namespace = schema.attrib.get("Namespace", "")
        if not namespace.startswith("microsoft.graph"):
            continue
        for item in schema:
            if item.tag in {EDM + "EntityType", EDM + "ComplexType", EDM + "EnumType"}:
                prefix = namespace.removeprefix("microsoft.graph").lstrip(".")
                key = (prefix + "." if prefix else "") + item.attrib["Name"]
                types[key] = item
                contexts[key] = (namespace, schema.get("Alias"))
    result = {}

    def collect(name):
        name = name.removeprefix("graph.").removeprefix("microsoft.graph.")
        if name in result:
            return
        node = types[name]
        context = contexts[name]
        if node.tag == EDM + "EnumType":
            result[name] = {"enum": [x.attrib["Name"] for x in node.findall(EDM + "Member")]}
            return
        value = {"properties": {}, "required": []}
        result[name] = value
        if node.get("OpenType") == "true":
            value["open"] = True
        if base := node.attrib.get("BaseType"):
            base = resolve(base, context)
            value["base"] = base.removeprefix("graph.").removeprefix("microsoft.graph.")
            collect(base)
            base_value = result[base.removeprefix("graph.").removeprefix("microsoft.graph.")]
            value["properties"].update(base_value["properties"])
            value["required"].extend(base_value["required"])
        for prop in node.findall(EDM + "Property"):
            field, kind = prop.attrib["Name"], resolve(prop.attrib["Type"], context)
            nullable = prop.attrib.get("Nullable") != "false"
            value["properties"][field] = {"type": kind, "nullable": nullable}
            if not nullable:
                value["required"].append(field)
            nested = kind.removeprefix("Collection(").removesuffix(")")
            if not nested.startswith("Edm."):
                collect(nested)

    for name in (*ROOTS, *additional_roots):
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
        "types": extract(
            args.input,
            (
                "site",
                "list",
                "listItem",
                "fieldValueSet",
                "drive",
                "driveItem",
                "permission",
                "driveItemVersion",
                "organization",
                "mailFolder",
                "message",
                "fileAttachment",
                "itemAttachment",
                "referenceAttachment",
                "mailboxSettings",
            )
            if args.version == "v1.0"
            else (),
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Extracted {len(data['types'])} official {args.version} schema types.")


if __name__ == "__main__":
    main()
