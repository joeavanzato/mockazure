# Native PIM client qualification

The [machine-readable report](living-pim.json) records the original Microsoft
Graph Identity.Governance 2.35.1 client against the isolated native MockAzure
HTTPS API and PostgreSQL database at migration 081.

The verifier used local authorization-code/PKCE login and a registered simulated
phone MFA outcome. It retained the same delegated token while checking:

1. The audit API denied access before eligibility.
2. A real `New-MgRoleManagementDirectoryRoleEligibilityScheduleRequest` created
   eligibility, but the audit API still denied access.
3. A real `New-MgRoleManagementDirectoryRoleAssignmentScheduleRequest` activated
   the role, and the audit API allowed access.
4. A real self-deactivation request removed the temporary authority, and the
   audit API denied access again.

PowerShell ran in an isolated Linux client container on the Windows host. Its
TCP relay preserved the configured loopback TLS origin. The server and worker
were native Windows processes; the existing isolated PostgreSQL test server
provided storage. No real Microsoft tenant was contacted.

The unchanged client archives and extracted trees were checked against the
repository's pinned SHA-256 manifest. The report contains no credentials.
To reproduce, follow the command in [identity lifecycle](../identity-lifecycle.md#privileged-access).
The integration suite separately checks security-clock expiry with the worker
stopped, permissions, policy limits, cancellation, paging and protected targets.
