# Post-M6 stabilization

Base: merged main916798f09041bec5c76155a8ccf2bbb355abcf13.
Branch: fix/release-stabilization. Existing isolated release worktree reused.
User approved continuing runtime/capacity fixes, explicit collector fixes and full
requalification. Prior delegated reasonable-decision/Native execution preferences
remain applicable; no routine approval pause. VTW and unrelated services are excluded.

This is bounded stabilization of existing flows, not new API surfaces.
Keep API contracts, permissions, original multi-rule and fractional-time fixtures,
and100000-user/1000000-record target intact. No fabricated evidence or output repair.

1. Diagnose storage/startup with bounded isolated probes. Compare a Windows
   bind-mounted PostgreSQL database against a Docker-managed Linux volume under
   identical limits, preserve row counts across restart, and test replaced bind
   directory visibility with public markers. Do not alter host settings.
   Adopt storage/recreation changes only against measured evidence. Add sanitized
   failure classification so future startup errors can be distinguished without
   logging secrets or raw database exceptions.
2. Both pinned collector commits equal their current upstream HEADs (verified
   through GitHub API). Reproduce actual serializer defects. Use explicitly named,
   hash-bound source patch profiles for JSON append and round-trip trace timestamps;
   preserve original extracted trees and unmodified baseline outcomes. Patched
   execution must never be described as unmodified upstream execution.
3. Regression-first fixes, affected functional qualification, one fresh final review
   and consolidated fixes, then frozen-source full qualification, exact large paging/
   recovery and reproducible candidate builds. Commit/push a new PR targeting main.
   Preserve every unsuccessful attempt; never lower the target or silently waive
   timestamp/identity checks. Final acceptance distinguishes supported patch profile
   from recorded upstream compatibility limitations.

The source-patch option was explicitly part of the three approved next steps.
Its cost is a maintained compatibility patch until upstream corrects serialization.
This changes the qualified client profile, not the emulator response or output oracle.

Reference: https://docs.docker.com/desktop/features/wsl/best-practices/
Docker recommends Linux filesystem storage for performance; that alone does not
prove the cause of the recorded WAL allocation error. The controlled probe and
full target measurement must supply local evidence.
