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

## Measurements and review

The isolated storage probe imported and checkpointed 200,000 rows (246,527,679
database bytes) and preserved all rows across restart on both filesystems:
Windows v9fs bind: 26.9277 seconds; Linux ext2/ext3 volume: 7.8293 seconds.
Neither probe reproduced the WAL panic. Replacing a public-marker bind directory
also did not reproduce stale contents in either the existing or a fresh container.

Pinned original serializers failed all three targeted checks. Explicit patches
passed JSON append/nested/date-string checks and CSV fraction preservation in
en-US and nl-NL, including continuation inputs after a full 5,000-row page.
No collector output is repaired.

One independent final review identified three P2 findings: overly broad baseline
failure acceptance, overwritten baseline logs/oracles, and a stale-metadata
storage transition. The consolidated corrections require structured serializer
failure codes, preserve profile/stamp-specific process logs and oracle inputs,
checkpoint the original baseline before patched execution, and reject unsafe
storage transitions. Qualification and full-scale results remain pending.

## Fresh-runtime procedure for the bind-to-volume transition

Do not run the new release Compose files against an old runtime directory.
The release runner fails before setup if it detects legacy postgres storage,
provisioning metadata without a volume, wrong volume ownership, or a volume
without instance configuration.

For each owned release profile, stop and remove its containers with its explicit
Compose file (compose down, WITHOUT --volumes). Preserve the entire matching
.runtime/m6-<surface> directory under a new project-local archive path, including
its database, credentials, wrapping key, configuration and provisioning journal.
Verify source and destination resolve inside this checkout before moving them.
Then run release preparation with the original runtime path absent and no
pre-existing named volume for that profile. This creates a fresh synthetic
qualification instance; it is not a migration of the old database.

Never remove an existing volume to bypass the check. Restore its corresponding
instance backup, or deliberately choose a separate owned profile. PostgreSQL
data and the identity wrapping-key backup must remain paired.

For this run, the stopped five prior runtime directories are preserved under
.runtime/stabilization-prior-m6. The earlier failed million-record runtime remains
at .runtime/m6-large-63002-failed. Earlier release reports/logs are preserved under
work/stabilization/prior-m6-release. No VTW or unrelated service was modified.
