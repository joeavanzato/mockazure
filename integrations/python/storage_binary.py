"""Original Python SDK byte writes and reads; bounded buffers and explicit endpoints."""

import argparse
import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

from azure.core.pipeline.transport import RequestsTransport
from azure.storage.blob import BlobServiceClient
from azure.storage.fileshare import ShareServiceClient

from integrations.python.arm import LocalSession


def run(profile, report, size=3145745, read_only=False):
    p = json.loads(Path(profile).read_text(encoding="utf-8"))
    results = {}
    source = Path(report).with_suffix(".source.bin")
    block = bytes(range(256)) * 4096
    expected = hashlib.sha256()
    with source.open("wb") as stream:
        for start in range(0, size, len(block)):
            part = block[: min(len(block), size - start)]
            stream.write(part)
            expected.update(part)
    try:
        for service, cls in [("blob", BlobServiceClient), ("file", ShareServiceClient)]:
            endpoint = p[service + "_endpoint"]
            u = urlsplit(endpoint)
            with LocalSession(u.scheme + "://" + u.netloc, p["ca_file"]) as session:
                client = cls(
                    endpoint,
                    credential={"account_name": p["account"], "account_key": p["key"]},
                    api_version="2025-11-05",
                    transport=RequestsTransport(session=session, connection_verify=p["ca_file"]),
                    retry_total=0,
                )
                target = (
                    client.get_blob_client("python-evidence", "binary.bin")
                    if service == "blob"
                    else client.get_share_client("python-evidence").get_file_client("binary.bin")
                )
                if not read_only:
                    with source.open("rb") as stream:
                        if service == "blob":
                            target.upload_blob(
                                stream,
                                length=size,
                                overwrite=True,
                                metadata={"writer": "python"},
                                max_concurrency=1,
                            )
                        else:
                            target.upload_file(
                                stream,
                                length=size,
                                metadata={"writer": "python"},
                                max_concurrency=1,
                            )
                digest = hashlib.sha256()
                total = 0
                reader = (
                    target.download_blob(max_concurrency=1)
                    if service == "blob"
                    else target.download_file(max_concurrency=1)
                )
                for part in reader.chunks():
                    digest.update(part)
                    total += len(part)
                assert total == size and digest.hexdigest() == expected.hexdigest()
                props = (
                    target.get_blob_properties()
                    if service == "blob"
                    else target.get_file_properties()
                )
                assert props.metadata["writer"] == "python"
                results[service] = {"bytes": total, "sha256": digest.hexdigest()}
    finally:
        source.unlink(missing_ok=True)
    Path(report).write_text(
        json.dumps(
            {
                "passed": True,
                "readOnly": read_only,
                "objects": results,
                "clientVersions": {
                    "azure-storage-blob": version("azure-storage-blob"),
                    "azure-storage-file-share": version("azure-storage-file-share"),
                },
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
    parser.add_argument("--bytes", type=int, default=3145745)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    run(args.profile, args.report, args.bytes, args.read_only)
