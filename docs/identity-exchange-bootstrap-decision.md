# Exchange bootstrap design adjustment — approved 2026-10-16

The isolated ExchangeOnlineManagement 3.11.0 probe reached MockAzure with a
validated local Exchange token. Its next request was
GET /AdminApi/v1.0/{tenant}/EXOModuleFile?Version=3.11.0 and returned 501.
The module imports PowerShell returned by this endpoint; Get-OrganizationConfig
is supplied by that downloaded module, not by the installed package.

Proposed bounded adjustment: implement this server protocol with a generated
Get-OrganizationConfig proxy. The proxy makes an authenticated HTTP request to
MockAzure; authorization and organization state remain server-side in PostgreSQL.
Keep the Microsoft module, Connect-ExchangeOnline, and collector wrappers intact.
Reject unsupported command selections and API versions explicitly. No mailbox
operations or upstream fallback are included. Test missing grants, wrong audience,
wrong tenant, and actual module import plus command invocation.

This adjusts the approved plan's prohibition on a hand-written replacement for
the downloaded service command. The user approved this bounded adjustment. The Exchange compatibility gate
remains incomplete until the real module and API probes pass. Identity and Graph work can proceed independently.
