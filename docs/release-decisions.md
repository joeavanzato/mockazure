# M6 implementation decisions

## Scope and release gate

M6 finishes the defined overnight queue. It adds bounded maintenance, qualification
profiles, measured scale/recovery and reproducible candidate artifacts. It does
not create another feature milestone or publish to a registry. The user merges
the feature PR.

The four M4 original collector-output failures remain mandatory blockers:
Hawk app/delegated inbox-rule JSON concatenation and MES app/delegated message
trace CSV precision loss. The existing original MES audit JSON exception in
M3B is the only approved waiver. A passing package build or API test cannot
override those collector outcomes.

## Maintenance and publication

UAL cleanup preserves expired query/job tombstones and the public410 behavior.
It fences expired jobs under the evidence-state lock before removing pins and
bounded result rows. Mail cleanup uses the mailbox lock also used by first-page
capture and mutations; current versions and every live matching cursor survive.
Audit and mail cleanup are separate atomic phases sharing a deletion budget,
avoiding the existing mailbox-to-evidence lock ordering conflict. Retrying a
partially completed maintenance request is safe; an uninspected phase reports
unknown remaining count rather than claiming no work.

Measured projection costs justified batches of up to100 pending historical
sign-in jobs. The bounded job selection happens before source lookup; ordinary
dispatch remains first in each worker cycle. Every source keeps its own immutable
publication, revision and receipt. Batch claims and publication commit together,
so a worker death rolls back both; running and malformed jobs retain ordinary
lease/retry handling. Missing-job repair runs at most once per5seconds per worker,
rather than on every batch. This can delay repair by5seconds plus in-flight work.

## Qualification ownership

Each M6 surface has fresh credentials, CA, state, Docker project, ports and
isolated networks. Only named hash-verified public client archives are reused.
Profile substitutions apply at explicit configuration boundaries; original
Microsoft SDKs and collector commands remain unchanged. Native callers retain
their loopback-only transport guard.

M6Graph moved to47443 after detecting that38443 was already owned by M2.
The user subsequently authorized stopping old MockAzure instances as needed.
Only idle predecessor MockAzure containers were stopped; their data and source
were retained. VTW and unrelated containers were untouched.

Initial Graph startup exposed a PostgreSQL initialization race: the temporary
Unix-socket server passed pg_isready before the production TCP server existed.
M6 health checks now require TCP. ARM recovery after recreation also exceeded
the original approximately60second startup window; actual readiness time and
the resulting grace-period decision are recorded with final validation.

## Evidence and artifact handling

Scale imports use real administrative APIs and normal projection workers.
Declared targets are100/1000,5000/50000 and100000/1000000 users/evidence records.
Two concurrent readers per family verify exact identities, counts and digest;
API and worker restarts are part of each successful profile. Partial imports,
timeouts, interrupted reports and reduced datasets cannot pass.

Source fingerprints cover product, tooling, schemas, packaging and runtime
configuration. Source-changing fixes invalidate affected proofs. Failed and
interrupted attempts remain in the local work/release history and are never
relabeled as the final current-source result.

Candidate archives are built twice from allowlisted committed Git object bytes,
with pinned build tools and SOURCE_DATE_EPOCH. An empty.git boundary in the
temporary export prevents Hatch from discovering an ancestor checkout's ignore
file; it is not packaged. Archive contents and source bytes are verified, then
a clean wheel installation is compared against Docker. No finished artifact is
rewritten to manufacture reproducibility.

## Execution ordering

Task4 build tooling was completed after small/medium tuning and before the final
large measurement because tooling participates in the source fingerprint.
M6's single final code review likewise runs before the final expensive measures,
so its consolidated fixes can be included in their source identity. Current
product regression and independent client checks continue meanwhile. Every
planned final gate remains required; no second review or implementation agents
are added.
