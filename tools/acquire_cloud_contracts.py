"""Acquire only pinned Microsoft contract sources; verification is entirely offline."""

import argparse
import hashlib
import json
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from mockazure.cloud import contracts


def acquire(service):
    catalog = contracts._catalog(service)
    root = (contracts.DATA / service).resolve()
    pending = {}
    for operation in catalog["operations"]:
        for source in operation["sources"]:
            relative = source["localPath"]
            target = (root / relative).resolve()
            parsed = urlsplit(source["url"])
            revision = source["revision"]
            if (
                not target.is_relative_to(root)
                or target == root
                or parsed.scheme != "https"
                or parsed.netloc != "raw.githubusercontent.com"
                or parsed.query
                or parsed.fragment
                or not re.fullmatch(r"[0-9a-f]{40}", revision)
                or not parsed.path.startswith(("/MicrosoftDocs/", "/Azure/azure-rest-api-specs/"))
                or f"/{revision}/" not in parsed.path
            ):
                raise ValueError("Untrusted contract source")
            if target in pending:
                if pending[target][1] != source["sha256"]:
                    raise ValueError("Conflicting contract source")
                continue
            if (
                target.is_file()
                and hashlib.sha256(target.read_bytes()).hexdigest() == source["sha256"]
            ):
                continue
            request = Request(
                source["url"], headers={"User-Agent": "MockAzure-contract-acquisition"}
            )
            with urlopen(request, timeout=30) as response:
                if response.geturl() != source["url"]:
                    raise ValueError("Contract source redirected")
                content = response.read(8 * 1024 * 1024 + 1)
            if (
                len(content) > 8 * 1024 * 1024
                or hashlib.sha256(content).hexdigest() != source["sha256"]
            ):
                raise ValueError("Contract source hash mismatch")
            pending[target] = (content, source["sha256"])
    for target, (content, _) in pending.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    contracts.verify_references(service)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if args.verify_only:
        contracts.verify_references(args.service)
    else:
        acquire(args.service)
    # A repository mirror is a review artifact; prevent it silently diverging.
    from pathlib import Path

    mirror = (
        Path(__file__).resolve().parents[1]
        / "integrations/contracts/cloud"
        / f"{args.service}.json"
    )
    if mirror.exists() and json.loads(mirror.read_text()) != contracts._catalog(args.service):
        raise ValueError("Contract catalog mirror differs")
    print(f"{args.service}: verified")


if __name__ == "__main__":
    main()
