# Living tenant native evidence qualification

This gate creates a fresh `living-small-business` tenant on the owned native
deployment. All service calls remain local; no Microsoft tenant is contacted.
The immutable result is retained beside this document as `living-evidence.json`.

The 2026-10-20 native run passed **30 collection comparisons with zero missing
expected IDs**, in 130 seconds including fresh population. It collected 73
app-only sign-ins, one directory change, three application-security changes,
11 ARM activity events, and the inbox-rule and file-rename records through all
three applicable audit collectors. All five data planes passed successful and
denied-operation checks through both diagnostic destinations.

Eight report acceptance tests and 928 unit/focused integration tests passed.
The report records base commit `96b8396`, a dirty source tree and its exact
source fingerprint; it does not present the base commit alone as the tested
implementation. These are local results, not hosted CI results.

```powershell
python -m tools.verify_living_tenant --config .runtime/living-native/native.json --phase evidence --report work/living-evidence.json
python -m pytest tests/acceptance/test_living_evidence_workflows.py -q
```

The gate requires authentication, directory, security, mail, workload, ARM and
service source families. Each of Blob, File, Table, Queue and Key Vault must
produce both successful and authenticated-denial diagnostics. Expected IDs come
from committed source records before collector reads create additional events.
Additional records are permitted; missing expected IDs fail the gate.

Collectors include the original Microsoft Graph SDK, the original
ExchangeOnlineManagement module's `Search-UnifiedAuditLog`, Management Activity
REST, ARM Activity Logs, Logs REST and the original Azure Blob SDK. Graph UAL
queries must complete asynchronously before records are read. App-only sign-ins
use the existing documented beta event-type filter rather than changing the
v1.0 interactive-user contract.

The report also verifies that administrative workload-history import creates no
native fact or feed delivery, and that evidence-clock advancement cannot extend
access-token lifetime. The expired-token control uses the local instance's
signing key in memory and is sent through the public HTTPS validator; no
credentials, tokens or signing material appear in the report.

Boundaries are documented in the [evidence guide](../living-tenant-evidence.md).
This is a basic operation-to-evidence gate, not a claim of complete Microsoft
audit semantics or a chaos/failure simulator.
