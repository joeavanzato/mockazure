# Living-tenant reference inventory

`operations.json` pins official Graph v1.0 operation documentation, permission
includes, the structural CSDL and their licenses by immutable revision and SHA256.
It also references the six existing cloud operation catalogs by hash, preserving
their source attribution without duplicating their reference files.

Rows marked `planned` are implementation requirements, not supported endpoints.
An `existing` row refers to a bounded implementation; consult `COVERAGE.md` for
actual supported variants and limitations. This inventory does not claim parity.

Run `python -m tools.acquire_living_tenant_contracts --verify-only` to verify all
bundled references offline. Source acquisition is explicit tooling, never runtime
behavior. Changing a catalog requires updating its pinned hash deliberately.
