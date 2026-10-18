# Official permission references

permissions.json extracts identifiers and consent flags from Microsoft's Graph
REST permissions reference, pinned by revision and SHA-256. Its source is licensed
under CC BY 4.0 (LICENSE.docs). The extraction omits descriptive prose; see
https://learn.microsoft.com/en-us/graph/permissions-reference for the original.

Two existing basic group permissions absent from that reference use Microsoft's
devx provisioning data instead (MIT, LICENSE.devx), separately pinned and marked
in each entry. Its resourceAppId metadata does not define the Graph resource ID.

Reproduce with tools/extract_graph_permissions.py using the pinned inputs named
in permissions.json. No tenant access is involved. OAuth application permission
roles and delegated scopes have distinct identifiers; unknown IDs fail closed.


role_details.json contains identifier-only resource actions from the official Entra
role permissions reference (same CC BY 4.0 attribution above). The source URL, SHA-256
and retrieval date are embedded. Descriptions are local labels, not copied prose.
Reproduce from the saved HTML using:

```
python tools/extract_role_details.py --input permissions-reference.html --retrieved-at 2026-10-17
```

The current built-in role templates determine the extracted subset. This descriptive
catalog does not change operational authorization or implement arbitrary custom roles.
