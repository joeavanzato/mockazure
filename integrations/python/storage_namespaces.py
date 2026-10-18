"""Original Blob and File Share SDKs over local, CA-verified HTTPS."""

import argparse
import json
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from azure.core.pipeline.transport import RequestsTransport
from azure.storage.blob import BlobServiceClient
from azure.storage.fileshare import ShareServiceClient

from integrations.python.arm import LocalSession


def run(profile):
    count = 0
    for service, cls in [("blob", BlobServiceClient), ("file", ShareServiceClient)]:
        endpoint = profile[service + "_endpoint"]
        with LocalSession(
            urlsplit(endpoint).scheme + "://" + urlsplit(endpoint).netloc, profile["ca_file"]
        ) as session:
            with cls(
                endpoint,
                credential={"account_name": profile["account"], "account_key": profile["key"]},
                api_version="2025-11-05",
                transport=RequestsTransport(session=session, connection_verify=profile["ca_file"]),
                retry_total=0,
            ) as client:
                name = "python-evidence"
                if service == "blob":
                    obj = client.create_container(name, metadata={"writer": "python"})
                    assert obj.get_container_properties().metadata["writer"] == "python"
                    assert name in [r.name for r in client.list_containers(include_metadata=True)]
                else:
                    obj = client.create_share(name, metadata={"writer": "python"})
                    assert obj.get_share_properties().metadata["writer"] == "python"
                    assert name in [r.name for r in client.list_shares(include_metadata=True)]
                count += 1
    return {
        "passed": True,
        "created": count,
        "clientVersions": {
            n: version(n) for n in ("azure-storage-blob", "azure-storage-file-share")
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    result = run(json.loads(args.profile.read_text()))
    args.report.write_text(json.dumps(result, indent=2) + "\n")
