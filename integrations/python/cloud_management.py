"""Original Azure management SDK with explicit local endpoint/credential configuration."""

import argparse
import json
import time
from importlib.metadata import version
from pathlib import Path

from azure.core.credentials import AccessToken
from azure.core.pipeline.transport import RequestsTransport
from azure.mgmt.resource import ResourceManagementClient

from integrations.python.arm import LocalSession


class Credential:
    def __init__(self, profile):
        self.token = json.loads(Path(profile["token_file"]).read_text())["access_token"]

    def get_token(self, *scopes, **kwargs):
        return AccessToken(self.token, int(time.time()) + 1800)


def run(profile, workflow):
    with LocalSession(profile["endpoint"], profile["ca_file"]) as session:
        client = ResourceManagementClient(
            Credential(profile),
            profile["subscription_id"],
            base_url=profile["endpoint"],
            credential_scopes=["https://management.azure.com/.default"],
            transport=RequestsTransport(session=session, connection_verify=profile["ca_file"]),
            polling_interval=1,
        )
        if workflow == "cleanup":
            for row in reversed(profile["resources"]):
                client.resources.begin_delete_by_id(row["id"], row["version"]).result()
            return {"deleted": len(profile["resources"])}
        if workflow == "cross-read":
            for row in profile["read_resources"]:
                assert (
                    client.resources.get_by_id(row["id"], row["version"]).id.lower()
                    == row["id"].lower()
                )
            return {"cross_reads": len(profile["read_resources"])}
        for row in profile["resources"]:
            result = client.resources.begin_create_or_update_by_id(
                row["id"], row["version"], row["body"]
            ).result()
            assert result.id.lower() == row["id"].lower()
            got = client.resources.get_by_id(row["id"], row["version"])
            assert got.id.lower() == row["id"].lower()
        for row in profile["read_resources"]:
            got = client.resources.get_by_id(row["id"], row["version"])
            assert got.id.lower() == row["id"].lower()
        target = next(
            row
            for row in profile["resources"]
            if row["type"] == "Microsoft.Compute/virtualMachines"
        )
        client.resources.begin_update_by_id(
            target["id"], target["version"], {"tags": {"writer": "python"}}
        ).result()
        assert (
            client.resources.get_by_id(target["id"], target["version"]).tags["writer"] == "python"
        )
        return {
            "created": len(profile["resources"]),
            "cross_reads": len(profile["read_resources"]),
            "updated": True,
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    profile = json.loads(args.profile.read_text())
    started = time.monotonic()
    result = run(profile, args.workflow)
    args.report.write_text(
        json.dumps(
            {
                "ecosystem": "python",
                "clientVersions": {"azure-mgmt-resource": version("azure-mgmt-resource")},
                "transport": "sdk",
                "workflow": args.workflow,
                "assertions": result,
                "passed": True,
                "elapsedSeconds": time.monotonic() - started,
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
