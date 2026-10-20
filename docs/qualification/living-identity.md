# Connected identity and access qualification

The owned Windows native lab ran schema 081 after an in-place upgrade from 077,
preserving its existing tenant data. The root checkout and root stack were untouched.

Command:

```powershell
python -m tools.verify_living_tenant --config .runtime/living-native/native.json --phase identity --report work/living-identity.json
```

The stacked profile completed. The first client run correctly rejected an attempt
to use imported-user credential bootstrap on a Graph-created user. The harness was
corrected to use the password it had created and an explicitly registered local
client. All client cases were then rerun on that populated disposable tenant.
The report records `qualification_resume: true`; no failed check was waived.

Recorded outcomes in [living-identity.json](living-identity.json):

| Workflow | Actual check |
| --- | --- |
| Joiner/mover/leaver | Original Graph SDK creates and changes a user; local PKCE produces a real token; disabling the user changes `/me` from 200 to 403 with that token. |
| Guest lifecycle | Original Graph invitation and user reads show PendingAcceptance, then Accepted after authenticated **local** redemption. Delivery stays local. |
| Delete/recover/collect | Graph SDK deletes and restores the same ID; account remains disabled and old access remains denied. Actual Graph directory audits contain the affected subject IDs and new soft-delete label. |
| Password rotation | Original Graph SDK adds two credentials, removes the old one; real token acquisition returns `invalid_client` for the removed secret and succeeds for the replacement. |
| PIM | Unchanged Microsoft.Graph.Identity.Governance 2.35.1: denied → eligible-denied → activated-allowed → deactivated-denied, with actual local PKCE/MFA. |
| Azure RBAC | Original Azure authorization SDK removes the Blob data role; original Blob SDK changes from successful download to 403 with the same token. |

The six acceptance gates require explicit allowed and denied outcomes, complete
case coverage, source fingerprints, and actual audit target coverage. Credential
values are absent from the report. Certificate `addKey`/`removeKey` is separately
covered by the C4 real-token integration checks; this native run tests password
rollover.

Limits remain intentional: guest redemption is local simulation, PIM is the bounded
tenant-scope profile, and restored objects never regain revoked sessions or grants.
The report describes a dirty development candidate; final combined qualification
records the complete program candidate separately.
