# Coverage audit — 2026-10-18

The previous COVERAGE.md was not fully current. This review compares the map with
implementation snapshot `0ceb9db53ed5d2664c726606ae2a54e64cf3cfb8`, whose Git tree exactly
matches the qualified final head `8580605`. The corrections change documentation only.

## Findings and corrections

| Finding | Actual behavior and correction | Primary implementation evidence |
| --- | --- | --- |
| Expanded organization fields appeared to have beta parity. | Beta returns the three original identity fields. The 16 additional fields are v1.0 only; the row now says so. | [organization handler](../src/mockazure/providers/graph/probes.py), [directory tests](../tests/integration/test_directory_collection_completion.py) |
| Phone/email rows said “also /me” beside all mutations. | Self POST/PATCH is denied, including explicit `/users/{self}`. Self DELETE needs a current session authenticated within ten minutes. These boundaries are now explicit. | [method authorization](../src/mockazure/security/method_mutations.py), [method tests](../tests/integration/test_method_management.py), [contract](../integrations/contracts/method-management.md) |
| The prose said descriptors were configured only through administration. | Phone/email Graph writes are implemented. Only Authenticator/FIDO2 enrollment remains admin-provisioned; their public DELETE is supported. | [method mutation action matrix](../src/mockazure/security/method_mutations.py) |
| Directory delta notes said no other family had delta; a gap row said “mail folders.” | Directory user/group delta, **messages within one mail folder**, and drive-root delta are distinct supported profiles. Folder-object delta is not implemented. | [directory delta](../src/mockazure/providers/graph/delta.py), [mail mutations/delta](../src/mockazure/providers/graph/mail_mutation_routes.py), [drive dispatch](../src/mockazure/providers/graph/drive_routes.py) |
| Workload paths were abbreviated into misleading combinations and mixed into a “no implementation” section. | Separate SharePoint/OneDrive, Teams and Intune tables now spell out concrete parent/child routes. List metadata, list items and fields are separate. Team/channel/chat member **item** reads are explicitly excluded. | [site dispatch](../src/mockazure/providers/graph/site_routes.py), [Teams dispatch](../src/mockazure/providers/graph/team_routes.py), [Intune dispatch](../src/mockazure/providers/graph/intune_routes.py) |
| Mail paths were abbreviated and the UAL row mentioned only configuration audits. | Expanded the concrete message/folder/attachment/action paths; UAL also projects native message Update/Move/Copy/SoftDelete facts. | [mail routes](../src/mockazure/providers/graph/mail_content_routes.py), [mutations](../src/mockazure/mail/message_mutations.py), [UAL projection](../src/mockazure/mail/audit.py) |
| Query, batch and throttling summaries stopped at older surfaces. | Added workload-specific query profiles and budget mappings. Authentication-method policies are not an allowed batch root or a declared read-budget bucket; registered endpoints do not imply either behavior. Batch rejects encoded paths and has response-size limits. | [batch validation](../src/mockazure/providers/graph/batch.py), [budget mapping](../src/mockazure/providers/graph/read_profiles.py), [protocol tests](../tests/integration/test_collection_protocol_completion.py) |
| The population table omitted new evidence/access/binary/session controls and the service-origin table omitted Activity. | Added workload import/export/reset, separate authoritative ACL/RSC controls, content upload/finalization and trusted session-device binding. Activity uses its own origin, distinct from ARM. | [workload controls](../src/mockazure/admin/workload_routes.py), [content controls](../src/mockazure/content/routes.py), [session controls](../src/mockazure/admin/session_routes.py) |
| The footer still cited 445 unit + 418 integration tests. | Replaced with the qualified expansion's 1,153 passed/1 skipped and explicitly identified its historical qualification head. Linked the current review evidence separately. | [qualification](coverage-expansion-results.md) |
| Navigation and rollout status were stale. | Fixed the gaps anchor, added workload navigation, removed obsolete “awaiting merge” labels from the original plans, and identified the implementation snapshot rather than assuming branch names establish availability. | [coverage map](../COVERAGE.md) |

The phone/email distinctions were checked against the official v1.0
[phone creation](https://learn.microsoft.com/en-us/graph/api/authentication-post-phonemethods?view=graph-rest-1.0)
and [email creation](https://learn.microsoft.com/en-us/graph/api/authentication-post-emailmethods?view=graph-rest-1.0)
references. Delta terminology was checked against
[message delta](https://learn.microsoft.com/en-us/graph/api/message-delta?view=graph-rest-1.0)
and [driveItem delta](https://learn.microsoft.com/en-us/graph/api/driveitem-delta?view=graph-rest-1.0).

## Verification

- **105 existing contract and PostgreSQL integration tests passed in 84.77 seconds**.
  They cover operation-manifest/schema registration, methods/policies, Activity contracts,
  directory completion, shared query/batch/throttling, SharePoint/OneDrive, Teams and Intune.
- The reviewed application exposes 732 OpenAPI method/path entries. This is **not** a
  supported-operation count: wildcard dispatch and intentionally rejected methods require
  inspecting the handler. The map was checked against those concrete dispatch rules.
- Source inspection covered provider registration, the public Graph handlers/query parsers,
  method mutation guards, workload authority, Activity, and the existing Exchange/ARM
  operation contracts. Original-client/full-scale evidence remains the previously recorded
  qualification; it was not rerun or represented as a new result for these prose edits.
- No runtime code changed. No real tenant was contacted. This review does not certify every
  payload/permission combination or full upstream parity; each row retains its bounded profile.

Reproduce the targeted audit suite from the repository root with the locked development
and client dependencies and the existing test PostgreSQL instance:

```powershell
$env:MOCKAZURE_TEST_DB_PORT = '55445'
python -m pytest tests/unit/test_coverage_operation_contracts.py tests/unit/test_authentication_policy_contracts.py tests/unit/test_method_management_contracts.py tests/unit/test_site_drive_contracts.py tests/unit/test_teams_contracts.py tests/unit/test_intune_contracts.py tests/unit/test_activity_contracts.py tests/integration/test_directory_collection_completion.py tests/integration/test_collection_protocol_completion.py tests/integration/test_method_management.py tests/integration/test_site_drive_evidence.py tests/integration/test_teams_evidence.py tests/integration/test_intune_evidence.py -q
```

## Integration finding

At review time, GitHub marked PRs #14–#24 merged, but #15–#24 had merged into their
predecessor branches. Remote `main` was `aae317e` (the plan-only merge). The complete
qualified implementation was in `0ceb9db`, not in `main`. A consolidation PR targeting
`main` is therefore required; merging the individual stacked PRs did not retarget them.
This is an integration-state issue, separate from the endpoint-table corrections.
