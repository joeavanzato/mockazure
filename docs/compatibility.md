# Client compatibility

All qualified clients use explicit local authorities/endpoints and the generated CA.
There is no upstream service fallback. SDK/module/collector pins are in
../integrations/manifest.json and uv.lock. Transport adapters and patched collectors
are named explicitly; they are not represented as unmodified end-to-end compatibility.

## Qualified baseline

The last combined release qualification is implementation commit 8c7287c.
[Its results](stabilization-results.md) and [machine-readable gates](stabilization-qualification.json)
include all five client suites and the full million-record scale run. Later source
changes require their own checks; historical pass reports do not transfer automatically.

| Workflow | Proven coverage / boundary |
| --- | --- |
| MSAL / Azure Identity | Local app-only and delegated flows; native/container managed identity profiles |
| Graph Python / PowerShell | Selected directory, evidence, security, mail and audit workflows; explicit local context |
| ExchangeOnlineManagement 3.11.0 | Actual module download and local HTTP command proxies with persisted API/RBAC checks |
| Az PowerShell / Azure SDKs | Inventory, supported mutations/RBAC and Activity Logs |
| Hawk / MES evidence | Selected sign-in/audit/security workflows, authorized and denied cases |
| Mail collectors | 44 passing cases under the explicit serialization-v1 source patch profile |
| MES Azure collection | Eight original Activity Log collector cases |

PowerShell qualification uses locally acquired tokens; native credential acquisition
is not claimed for every module. Complete collect-everything entry points remain
outside this baseline. [Collector patch details](../integrations/collector-patches/README.md).

## Preserved upstream limitations

Original Hawk JSON append creates concatenated documents for multiple suspicious rules.
Original MES trace CSV loses fractional timestamp precision. Four original failures and
four original denial passes are retained separately from the successful patched run.

M3B's original MES audit JSON append/merge defect remains the previously accepted,
explicit limitation. Exact retrieval is independently checked and SOF-ELK output passes.
No additional output defect is silently accepted.

## Current operation contracts

- [Graph directory](../integrations/contracts/graph-directory.md)
- [Graph evidence](../integrations/contracts/graph-evidence.md)
- [Graph security](../integrations/contracts/graph-security.md)
- [Mail and audit](../integrations/contracts/mail.md)
- [Azure management](../integrations/contracts/arm.md)

[Historical reports and decisions](archive/m1-m6/README.md) retain the original milestone
outcomes and failed attempts. See [default runtime](default-runtime.md) for supported
startup/storage behavior.
