# M3B evidence execution record

The approved evidence plan is implemented on feat/graph-evidence in
D:/projects/mockazure/.worktrees/evidence. The primary checkout and earlier
MockAzure runtime projects are preserved.

## Structural authority

Graph payload validation uses the actual Microsoft v1.0 CSDL, pinned to
microsoftgraph/msgraph-metadata revision
34b8e9c29032c82352a6fd7138be0a864a842bb2. The original v1.0-Prod.csdl SHA-256 is
286cdc5d812b88bba9870b452d63d37aed925e6180541f20e034c949cd1816f0.
The extracted subset, metadata provenance and licence are bundled with the wheel.
The separately pinned beta schema is used for beta-only workload sign-in fields.
REST operation documentation controls permission, licensing and query contracts;
CSDL alone does not establish those semantics.

Real SDK execution exposed namespace-qualified bound actions. Both the documented
short form and microsoft.graph-qualified confirmCompromised, dismiss and
assignLicense use the same authorization and state transition handlers.
A v1.0 historical record with explicit isInteractive=true also matches beta's
interactiveUser filter. False/unknown flags do not establish a workload event type.

## Verified SDK distribution

The combined SDK qualification passed 22 gates in 303.815 seconds before collector
qualification: actual MSAL 1.39.0/Graph Python 1.64.0 on Windows and in a container,
Graph PowerShell 2.35.1 in a container, both application/delegated flows,
Azure Identity 1.26.0 managed identity on Windows and in a container,
permission denials, licence assignment/removal, delegated-only licence details,
risk confirmation/dismissal, exact 6,000 signIn/4,000 directoryAudit IDs,
5,000 directory users, two native clean-wheel replicas, two workers, API/worker/PG
restart and continuation replay, persisted 429, tenant/audience boundaries,
and external DNS/IPv4/IPv6 network denial.
This was a partial qualification report with collectors explicitly pending;
it was never recorded as whole-milestone success.

## Original collector boundary

Hawk revision a44536f2b4225dd8ea2e463db88d5db40cfac8c7 and MES revision
47565f7842784cd3667ff630e3dccb49fa3408ed are acquired and hash-verified from the
manifest. The collector functions, date helpers, query construction and output
serializers are original. The MES loader executes its original top-level function
and type declarations and original collector file, excluding only module-level
UI/update lookup. Hawk uses its original SkipUpdate/NonInteractive parameters;
named New-AIClient and Send-AIEvent telemetry-only no-ops record calls locally.

The MES-only transport wrapper accepts the exact public Graph origin and the
configured local HTTPS origin, preserving path/query/body/method/headers.
It invokes module-qualified Microsoft.Graph.Authentication Invoke-MgGraphRequest.
The pinned raw-request SDK provider needs its existing allowed-host validator
bound to the local hostname. Its original redirect handler is set to zero
redirects through a verified pinned layout; unknown layouts fail closed.
Microsoft authentication and retry handlers remain in place. Reconnect attempts
fail explicitly. No generated Graph command or service response is replaced.

Collector fixtures contain 1,101 records for each of the four sign-in event types,
1,101 in-window audits, equal timestamps, other-user and outside-time distractors,
and an outside-window audit target that matches MES's literal unparenthesized OR.
Hawk's exact 551-record risky subset is independently checked in JSON and CSV.
MES JSON and SOF-ELK raw and original MergeOutput results are independently parsed.
Empty and denied results have explicit separate gates. A persisted next_page_429
waits for the first continuation request; no harness pacing or retry substitutes
for the original collector/SDK retry.

## Local operational profile

M3B owns .runtime/m3b, database mockazure_m3b, image mockazure:evidence and
Compose project mockazure-m3b. Loopback ports are 51443 HTTPS,
51444/51445 temporary native replicas,51446 managed identity and 55436 PostgreSQL.
The proposed 494xx range and next 504xx range were reserved by Windows, so verified
free 514xx ports were selected. Internal network 10.231.78.0/24 and ingress 10.231.79.0/24
were unused when created. Runtime PostgreSQL data remains persistent under D:.

Preparation downloads pinned public client artifacts. Qualification has no
upstream tenant fallback and runs on the internal client network. Certificates,
client secrets and token files stay in ignored runtime directories.
The instance CA is passed explicitly to clients. Host trust stores, DNS and
network reservations are unchanged.

The initial 10,000-record projection and additional 5,508 collector records exercise
normal persistent worker jobs. These measurements are functional qualification,
not a throughput SLA; Windows-mounted PostgreSQL storage materially affects
projection time. No proprietary risk scoring, fraud detection, conditional-access
engine, arbitrary Graph surface or full Exchange/ARM compatibility is claimed.

## Final qualification

The final combined qualification passed all 25 runtime gates, three native/container SDK
profiles in both authorization modes, and all 20 collector acceptance gates (14 strict passes; 6
explicitly accepted original MES audit JSON limitations). SDK/runtime checks took 581.622
seconds; the complete run took 2842.423 seconds. The report is bound to source, wheel, lockfile,
manifest, original collector files, outputs and the running image.

Original MES strict JSON failures remain false and visible. Exact IDs and sentinel values, no
duplicate/truncated records, unchanged sources and the original merge behavior are required for
the approved exception. See [verification summary](evidence-verification.json) for measured
results and hashes.

The original MES writer uses second-resolution filenames and appends each page's
JSON array. Multiple fast pages therefore produce adjacent arrays in one file;
the original MergeOutput function does not correct this. Preserved basic and
actor-filter raw outputs contain all 1,101 expected audit IDs and sentinel values
with no duplicates when examined as successive documents, but remain invalid
single JSON documents. Diagnostic decoding never changes their failed status.
See the pinned [original
writer](https://github.com/invictus-ir/Microsoft-Extractor-Suite/blob/47565f7842784cd3667ff630e3dccb49fa3408ed/Scripts/Get-AzureEntraGraphLogs.ps1#L530).

The actor/target-OR cases also return legitimate pre-existing SDK/native audits
outside the date window. Their expectation now uses a separately exported and
hashed administrative snapshot while requiring exact declared fixtures. Server
filter semantics and original collector predicates remain unchanged.

The final independent review found four Important issues, all reproduced and
fixed: current risky-user retention, delayed old risk contributions clearing
newer risk, nullable collection filters, and the combined verifier call signature.
Six focused regression cases passed. The full suite then passed 302 tests in
331.27 seconds with one existing MSAL advisory; 18 focused report/orchestration
checks passed after the expectation correction. Lint, package provenance and
diff checks passed. See [execution decisions](evidence-decisions.md) for every
ruling, its cost and review disposition.

Initial full regression: 292 passed in 331.22 seconds, with one existing MSAL
response-mode advisory. Five subsequent capability/report boundary checks passed
in 1.97 seconds; lint and package provenance checks passed.

The native administrative export check subsequently found a create/delete audit
validation defect: a JSON string containing null denotes an absent before/after
value, not a resource with a null ID. Three regressions reproduced the error and
passed after correction. The rebuilt local API then exported all 5,223 current
audit records as valid JSON, with every declared collector fixture intact.
The final full regression after that fix passed 307 tests in 704.59 seconds;
26 focused report/orchestration/exception checks passed in 2.48 seconds.

The final regression after the joined dismissal read passed all 316 tests in 721.14 seconds. The
13 focused risk regressions also passed.
