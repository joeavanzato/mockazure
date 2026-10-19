"""Original Azure Blob SDK byte, block, snapshot and lease interactions."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.transport import RequestsTransport
from azure.storage.blob import BlobBlock, BlobServiceClient

from integrations.python.arm import LocalSession


def run(profile, report):
    config = json.loads(Path(profile).read_text())
    endpoint = config["blob_endpoint"]
    origin = urlsplit(endpoint).scheme + "://" + urlsplit(endpoint).netloc
    content = b"original-sdk-" * 180000
    with LocalSession(origin, config["ca_file"]) as transport:
        service = BlobServiceClient(
            endpoint,
            credential={"account_name": config["account"], "account_key": config["key"]},
            api_version="2025-11-05",
            transport=RequestsTransport(session=transport, connection_verify=config["ca_file"]),
            retry_total=0,
        )
        client = service.get_blob_client("python-evidence", "workflow/content.bin")
        client.upload_blob(content, overwrite=True, metadata={"source": "python-sdk"})
        assert client.download_blob().readall() == content
        assert client.download_blob(offset=1048573, length=31).readall() == content[1048573:1048604]
        props = client.get_blob_properties()
        snapshot = client.create_snapshot()["snapshot"]
        lease = client.acquire_lease(lease_duration=15)
        try:
            client.upload_blob(b"forbidden", overwrite=True)
        except HttpResponseError as error:
            assert error.status_code == 412
        else:
            raise AssertionError("Active lease did not protect bytes")
        client.upload_blob(b"leased-update", overwrite=True, lease=lease)
        lease.release()
        saved = service.get_blob_client(
            "python-evidence", "workflow/content.bin", snapshot=snapshot
        )
        assert saved.download_blob().readall() == content
        try:
            client.upload_blob(
                b"stale",
                overwrite=True,
                etag=props.etag,
                match_condition=MatchConditions.IfNotModified,
            )
        except HttpResponseError as error:
            assert error.status_code == 412
        else:
            raise AssertionError("Stale ETag was accepted")
        block = service.get_blob_client("python-evidence", "workflow/blocks.bin")
        block.stage_block("block-2", b"second")
        block.stage_block("block-1", b"first")
        block.commit_block_list([BlobBlock("block-1"), BlobBlock("block-2")])
        assert block.download_blob().readall() == b"firstsecond"
        committed, uncommitted = block.get_block_list(block_list_type="all")
        assert len(committed) == 2 and not uncommitted
        names = [
            b.name
            for b in service.get_container_client("python-evidence").list_blobs(
                name_starts_with="workflow/", results_per_page=1
            )
        ]
        assert names == ["workflow/blocks.bin", "workflow/content.bin"]
        client.delete_blob(delete_snapshots="include")
        block.delete_blob()
        assert not client.exists() and not block.exists()
    from importlib.metadata import version

    result = {
        "passed": True,
        "clientVersion": version("azure-storage-blob"),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "operations": [
            "put",
            "get",
            "range",
            "head",
            "snapshot",
            "lease",
            "conditional-write",
            "stage-block",
            "commit-block-list",
            "get-block-list",
            "list",
            "delete",
        ],
    }
    Path(report).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    run(args.profile, args.report)
