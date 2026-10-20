"""Acquire pinned official source bytes or verify the packaged references offline."""

import argparse
import hashlib
from urllib.request import Request, urlopen

from mockazure.living_tenant.contracts import (
    DATA,
    read_manifest,
    validate_source,
    verify_references,
)


def acquire(area=None):
    manifest = read_manifest()
    keys = (
        set(manifest["sources"])
        if area is None
        else {key for o in manifest["operations"] if o["area"] == area for key in o["source_refs"]}
    )
    pending = {}
    for key in sorted(keys):
        source = manifest["sources"][key]
        validate_source(source)
        target = (DATA / source["path"]).resolve()
        if not target.is_relative_to(DATA.resolve()):
            raise ValueError("Source path escaped package")
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == source["sha256"]:
            continue
        request = Request(source["url"], headers={"User-Agent": "MockAzure-contract-acquisition"})
        with urlopen(request, timeout=30) as response:
            if response.geturl() != source["url"]:
                raise ValueError("Contract source redirected")
            content = response.read(16 * 1024 * 1024 + 1)
        if (
            len(content) > 16 * 1024 * 1024
            or hashlib.sha256(content).hexdigest() != source["sha256"]
        ):
            raise ValueError("Contract source size/hash mismatch")
        pending[target] = content
    for path, content in pending.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return verify_references(area=area)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--area", choices=("content", "evidence", "identity", "hunting"))
    args = parser.parse_args()
    result = verify_references(area=args.area) if args.verify_only else acquire(args.area)
    print(
        f"Verified {result['operations']} operations, {result['sources']} source files "
        f"and {result['catalogs']} existing catalogs."
    )


if __name__ == "__main__":
    main()
