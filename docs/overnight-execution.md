# Overnight execution: remaining MockAzure milestones

User direction (2026-10-16 America/New_York): continue M3C, M4, M5 and all later defined milestones without routine approval pauses; make the most reasonable decisions and record them. This includes M6, the only defined milestone after M5. It does not invent M7 or broad Defender/Purview/Intune work.

## Execution contract
- Native implementation in this thread; one independent final review per milestone, no implementation agents.
- Separate specs, plans, tests, decisions, commits and PR boundaries for M3C/M4/M5/M6.
- Use actual Microsoft Graph v1.0 metadata/REST references and official Exchange/ARM contracts.
- Persistent PostgreSQL state, real local HTTP/SDK/collector flows, operational permission/policy enforcement.
- Do not access/edit VTW, copy its secrets/untracked files, contact real tenants, or alter host trust/DNS/firewall.
- Preserve existing root checkout/worktrees/runtimes. Main is at 5cdcf5a after merged PRs5/6.
- Commit and push feature branches/PRs; user merges. Stack qualified milestone branches if necessary.
- No public package registry publication. M6 prepares reproducible release artifacts for review.
- Do not turn a failure into success. Only the already approved original MES audit JSON append/merge exception applies.
- Hourly thread heartbeat: continue-mockazure-m3c-through-m5 (name: Continue MockAzure through M6). Stop it when all defined work is complete.
- A scheduled continuation needs the Codex host available; it is not an external unattended build service.

## Ordered milestone queue
| Milestone | Deliverable | State |
| --- | --- | --- |
| M3C | Applications/SPs/devices/owners; credentials, permission grants, directory roles, authentication-method inspection; enforced/report-only Conditional Access; audit effects | Implementing in .worktrees/security on feat/security-administration; tasks 1–6 implemented and verified; Conditional Access next |
| M4 | Mailbox configuration/forwarding, inbox and sweep rules, message trace, durable Graph UAL query/poll/download, original Hawk/MES workflows | Queued after qualified M3C |
| M5 | ARM inventory and selected state changes, inherited scoped RBAC, Activity Logs and original MES export | Queued after qualified M4 |
| M6 | Combined workflows, package/CLI/Docker parity, restart/fault/multi-worker checks, small/5,000-user and 100,000-user/1,000,000-evidence scale profiles, measured results and release artifacts | Queued after qualified M5 |

Each new milestone reads its predecessor's ledger and verified interfaces. The M3B baseline is 316 passing unit/integration tests,25 runtime checks,3SDKprofiles and20accepted collector cases(14strict,6documented MES limitations). Those results are historical baseline, not proof of new changes.

Latest checkpoint: M3C tasks1–6 are implemented. Role management passed255
affected unit/integration tests; method inventory passed267 in207.21s. Continue
Conditional Access persistence/enforcement, real-client qualification and final
milestone review/PR.
The authoritative continuation checkpoint is docs/security-execution.md; the
native ledger is .superpowers/sdd/2026-10-16-mockazure-security/progress.md.
All later milestones remain queued and the hourly schedule remains active.
