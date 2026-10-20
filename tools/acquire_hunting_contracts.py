"""Acquire/hash published Microsoft Learn table references; verification is offline."""

import argparse
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen

DATA = Path(__file__).parents[1] / "src/mockazure/hunting/data"
TABLES = (
    "IdentityInfo",
    "IdentityLogonEvents",
    "IdentityDirectoryEvents",
    "IdentityQueryEvents",
    "EmailEvents",
    "EmailAttachmentInfo",
    "EmailUrlInfo",
    "EmailPostDeliveryEvents",
    "UrlClickEvents",
    "DeviceInfo",
    "DeviceNetworkInfo",
    "DeviceLogonEvents",
    "DeviceProcessEvents",
    "DeviceNetworkEvents",
    "DeviceFileEvents",
    "DeviceRegistryEvents",
    "DeviceEvents",
    "CloudAppEvents",
    "AlertInfo",
    "AlertEvidence",
)
TYPES = {
    "datetime",
    "string",
    "int",
    "long",
    "real",
    "boolean",
    "bool",
    "dynamic",
    "guid",
    "decimal",
}


class Tables(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.rows, self.cells, self.text, self.in_cell = [], [], [], False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        elif tag in {"td", "th"}:
            self.text, self.in_cell = [], True

    def handle_endtag(self, tag):
        if tag in {"td", "th"}:
            self.cells.append(" ".join("".join(self.text).split()))
            self.in_cell = False
        elif tag == "tr" and self.cells:
            self.rows.append(self.cells)

    def handle_data(self, data):
        if self.in_cell:
            self.text.append(data)


def parse_columns(content):
    columns, notes = {}, {}
    for row in Tables(content.decode("utf-8")).rows:
        if len(row) < 3 or row[1].strip().lower() not in TYPES:
            continue
        match = re.match(r"^([A-Za-z][A-Za-z0-9_]*)", row[0])
        if not match:
            raise ValueError("Unexpected Microsoft column name")
        name = match[1]
        typ = row[1].strip().lower()
        typ = "bool" if typ == "boolean" else typ
        if name in columns:
            raise ValueError("Duplicate Microsoft column definition")
        columns[name] = typ
        notes[name] = {
            "description": row[2],
            "availability": "preview"
            if "preview" in row[0].lower()
            else ("conditional" if "*" in row[0] else "available"),
            "source_label": row[0],
        }
    if len(columns) < 5 or "Timestamp" not in columns:
        raise ValueError("Official hunting column table was not found")
    return columns, notes


def fetch(name):
    url = (
        "https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-" + name.lower() + "-table"
    )
    request = Request(url, headers={"User-Agent": "MockAzure-contract-acquisition"})
    with urlopen(request, timeout=40) as response:
        if response.geturl() != url:
            raise ValueError("Official table reference redirected")
        content = response.read(2 * 1024 * 1024 + 1)
    if len(content) > 2 * 1024 * 1024:
        raise ValueError("Source snapshot exceeds acquisition bound")
    columns, notes = parse_columns(content)
    text = content.decode("utf-8")
    revision = re.search(r'name="gitcommit" content="[^"]*/blob/([0-9a-f]{40})/', text)
    if not revision:
        raise ValueError("Published source omitted its immutable revision")
    return (
        name,
        content,
        {
            "path": "sources/" + name + ".html",
            "url": url,
            "revision": revision[1],
            "sha256": hashlib.sha256(content).hexdigest(),
            "retrieved_at": datetime.now(UTC).isoformat(),
            "license": "Microsoft Learn terms: https://learn.microsoft.com/legal/termsofuse",
            "source_kind": "published-html",
        },
        {
            "name": name,
            "columns": columns,
            "column_metadata": notes,
            "availability": "conditional"
            if any(n["availability"] != "available" for n in notes.values())
            else "available",
            "source_refs": [name],
            "native_mappings": [],
            "entitlements": ["identity"]
            if name.startswith("Identity")
            else (
                ["email"]
                if name.startswith(("Email", "Url"))
                else ["endpoint"]
                if name.startswith("Device")
                else ["cloud-apps"]
                if name == "CloudAppEvents"
                else ["alerts"]
            ),
            "nullable_omissions": True,
            "generated_columns": [],
        },
    )


def acquire():
    if (DATA / "sources.json").is_file():
        manifest = json.loads((DATA / "sources.json").read_text("utf-8"))
        if set(manifest) != set(TABLES):
            raise ValueError("Unexpected hunting acquisition manifest")
        for name, source in manifest.items():
            expected = (
                "https://learn.microsoft.com/en-us/defender-xdr/advanced-hunting-"
                + name.lower()
                + "-table"
            )
            if source["url"] != expected or source["path"] != "sources/" + name + ".html":
                raise ValueError("Untrusted hunting source")
            path = DATA / source["path"]
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]:
                continue
            with urlopen(
                Request(
                    expected,
                    headers={
                        "User-Agent": "MockAzure-contract-acquisition",
                    },
                ),
                timeout=40,
            ) as response:
                if response.geturl() != expected:
                    raise ValueError("Pinned hunting source redirected")
                content = response.read(2 * 1024 * 1024 + 1)
            if hashlib.sha256(content).hexdigest() != source["sha256"]:
                raise ValueError(
                    "Published source changed; an explicit schema pin review is required"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return
    # Stage every verified source before publishing the catalog.
    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(fetch, TABLES))
    DATA.joinpath("sources").mkdir(parents=True, exist_ok=True)
    for _, content, source, _ in rows:
        (DATA / source["path"]).write_bytes(content)
    (DATA / "sources.json").write_text(
        json.dumps(
            {name: source for name, _, source, _ in rows},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (DATA / "tables.json").write_text(
        json.dumps(
            {name: table for name, _, _, table in rows},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    if not args.verify_only:
        acquire()
    from mockazure.hunting.contracts import verify_sources

    print(json.dumps(verify_sources()))
