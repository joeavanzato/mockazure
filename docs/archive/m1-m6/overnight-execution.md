> Current stabilization results: [validated candidate and scope](../../stabilization-results.md). Earlier candidate outcomes below are retained as historical evidence.

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
| M3C | Security administration, permissions and policy | Qualified; PR7 open for user merge |
| M4 | Mail/Exchange, traces, durable UAL, original collectors | API fixes committed; mandatory original Hawk JSON and MES trace precision gates unresolved; draft PR |
| M5 | ARM inventory, selected mutations, inherited RBAC, Activity Logs and MES export | Qualified17 runtime/3SDK profiles/8MES cases; review fixes complete; M4 blockers retained |
| M6 | Combined workflows, package/CLI/Docker parity, cleanup, restart/fault checks, small/5,000-user and100,000-user/1,000,000-evidence scale, release artifacts | Implementation and measured qualification handed off in draft PR10; small/medium and artifacts pass; M4 and million-record failures block release |

Each milestone consumes its predecessor's actual verified interfaces and current
failure record. Earlier all-green shaped M4 reports are historical and superseded
by final review. No new collector-output waiver is inferred from overnight
delegation. Continuing independent implementation is distinct from claiming
acceptance or merging. M4 final reviewer findings/dispositions are in
[mail-review.md](mail-review.md). The overnight execution reached the M6 blocked handoff. Final gate details are in [release-execution.md](release-execution.md); no further milestone is defined. The hourly continuation is now PAUSED; final exports and draft PR10 are updated.
