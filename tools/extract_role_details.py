"""Extract identifier-only built-in role actions from a saved official Learn page."""

import argparse
import hashlib
import html
import json
import re
from pathlib import Path

SOURCE = "https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/permissions-reference"


def extract(content: bytes, roles: dict[str, str], retrieved_at: str) -> dict:
    text = content.decode("utf-8")
    sections = re.split(r"<h2\b[^>]*>", text, flags=re.I)[1:]
    result = {}
    for name, identifier in sorted(roles.items()):
        found = [
            section
            for section in sections
            if html.unescape(re.sub("<[^>]*>", "", section.split("</h2>", 1)[0])).strip() == name
        ]
        if len(found) != 1:
            raise ValueError(f"Missing or ambiguous role section: {name}")
        actions = sorted(set(re.findall(r"<td>\s*(microsoft\.[^<\s]+)\s*</td>", found[0])))
        if not actions:
            raise ValueError(f"Missing resource actions: {name}")
        result[identifier] = {
            "description": f"Microsoft Entra built-in role: {name}.",
            "rolePermissions": [{"allowedResourceActions": actions}],
        }
    return {
        "source": SOURCE,
        "sourceSha256": hashlib.sha256(content).hexdigest(),
        "retrievedAt": retrieved_at,
        "descriptionOrigin": (
            "Local label; action identifiers extracted from official resource-action table cells."
        ),
        "roles": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--retrieved-at", required=True)
    parser.add_argument(
        "--templates", type=Path, default=Path("src/mockazure/security/data/role_templates.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("src/mockazure/security/data/role_details.json")
    )
    args = parser.parse_args()
    data = extract(
        args.input.read_bytes(), json.loads(args.templates.read_text())["roles"], args.retrieved_at
    )
    args.output.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
