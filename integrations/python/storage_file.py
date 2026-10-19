"""Original Azure Files SDK against local FileREST byte and snapshot workflows."""

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from azure.core.exceptions import HttpResponseError
from azure.core.pipeline.transport import RequestsTransport
from azure.storage.fileshare import ShareServiceClient

from integrations.python.arm import LocalSession


def run(profile, report):
    config = json.loads(Path(profile).read_text(encoding="utf-8"))
    endpoint = config["file_endpoint"]
    origin = urlsplit(endpoint).scheme + "://" + urlsplit(endpoint).netloc
    data = b"original-files-sdk-" * 90000
    with LocalSession(origin, config["ca_file"]) as transport:
        service = ShareServiceClient(
            endpoint,
            credential={"account_name": config["account"], "account_key": config["key"]},
            api_version="2025-11-05",
            transport=RequestsTransport(session=transport, connection_verify=config["ca_file"]),
            retry_total=0,
        )
        share = service.get_share_client("python-evidence")
        directory = share.get_directory_client("workflow")
        directory.create_directory(metadata={"source": "original-sdk"})
        file = directory.get_file_client("content.bin")
        file.upload_file(data, metadata={"source": "python"})
        assert file.download_file().readall() == data
        assert file.download_file(offset=1048573, length=29).readall() == data[1048573:1048602]
        snapshot = share.create_snapshot()["snapshot"]
        saved = service.get_share_client("python-evidence", snapshot=snapshot).get_file_client(
            "workflow/content.bin"
        )
        lease = file.acquire_lease()
        try:
            file.upload_range(b"forbidden", offset=0, length=9)
        except HttpResponseError as error:
            assert error.status_code == 412
        else:
            raise AssertionError("File lease did not protect content")
        file.upload_range(b"changed", offset=0, length=7, lease=lease)
        lease.release()
        file.clear_range(offset=1048064, length=1024)
        expected = bytearray(data)
        expected[:7] = b"changed"
        expected[1048064:1049088] = bytes(1024)
        file.resize_file(len(data) - 5)
        expected = expected[:-5]
        assert file.download_file().readall() == bytes(expected)
        assert saved.download_file().readall() == data
        ranges = file.get_ranges()
        assert len(ranges) == 2
        assert file.get_file_properties().size == len(expected)
        names = [item["name"] for item in directory.list_directories_and_files(results_per_page=1)]
        assert names == ["content.bin"]
        snapshots = [
            item.snapshot
            for item in service.list_shares(include_snapshots=True)
            if item.name == "python-evidence"
        ]
        assert snapshot in snapshots
        service.get_share_client("python-evidence", snapshot=snapshot).delete_share()
        file.delete_file()
        directory.delete_directory()
    Path(report).write_text(
        json.dumps(
            {
                "passed": True,
                "clientVersion": version("azure-storage-file-share"),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "operations": [
                    "directory-create",
                    "file-create",
                    "put-range",
                    "download",
                    "range",
                    "properties",
                    "clear-range",
                    "resize",
                    "snapshot",
                    "lease",
                    "list",
                    "list-snapshots",
                    "delete",
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    run(args.profile, args.report)
