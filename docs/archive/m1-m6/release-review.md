# M6 final review and disposition

One fresh read-only review covered
0e637743bf64119a0e468bd6220385e6020157c0..04e263bac56ec55562e32856b29148d74bd532ea.
No critical findings; four important findings; no separate minors.
All four findings were confirmed. This is the sole M6 review, followed by one
consolidated regression-first fix pass. No second review is scheduled.

1. Source attestation omitted shipped HTML/licence resources and the native
   network guard. Shared qualification inputs now include every file under the
   product, tooling, integration, deployment and test-support trees, excluding
   interpreter caches. All seven fingerprints use that definition. Artifact
   validation independently compares the historical candidate inputs with current
   HEAD inputs and actual working bytes, including the complete source hash map.
   Actual-path mutation tests reproduced20 missing invalidations, and a changed
   current-package-byte test reproduced stale artifact acceptance. The corrected
   attestation/artifact/ARM tests passed45cases.
2. Only one directory reader triggered the API restart, allowing evidence readers
   to finish beforehand. Every reader now captures its first-page continuation at
   a shared barrier. Only then does the API restart; all four readers resume their
   retained continuations afterward. Per-reader monotonic capture/resumption times
   and page counts are validated against the restart interval. Six RED regressions
   became GREEN;32scale tests passed. An additional deterministic test uses the
   actual Runtime.read implementation with delayed directory responses and fast
   evidence responses; all three restart-boundary tests pass.
3. The2400second suite timeout was below the measured2842.423second original M3B
   run. Bounded per-stage allowances are now M3:1800s, M3B:5400s, M3C:3600s,
   M4:5400s and M5:3600s. The regression simulates the observed valid M3B duration
   and confirms the orchestration permits completion. Collector assertions and
   failure reporting remain unchanged.
4. PostgreSQL readiness/shutdown allowances did not cover observed recovery.
   All five release databases and the M6 test database require TCP readiness,
   allow180seconds before counting startup failures, and have120seconds shutdown
   grace. This covers the observed approximately75second recovery while retaining
   a bounded failure threshold. Tests cover all six configurations and the suite
   allowance;17affected profile/report/budget tests pass.

The reviewer ran45pure unit tests in1.81seconds and two in-memory reproductions.
Product lock/fence paths were examined without finding another cleanup or batch
defect. The broad product run subsequently completed:755passed, one stale test
fixture failure. That fixture was corrected and its five tests passed; a fresh
complete unit run covers the final harness changes. No product API code changed
during the review fixes.

## Deferred judgments and executor rulings

- Final million-record and five-suite current-source acceptance remain mandatory;
  tuning and earlier-source reports do not substitute for them. Collect final
  evidence only after the consolidated fixes are committed.
- The interrupted M3B database was inspected:5001live seed users, including one
  leftover temporary user. Its state was archived with other interrupted owned
  M6 instances; final runs initialize fresh instances. No count oracle was changed.
- Four original M4 output defects remain mandatory blockers. No original collector
  rewrite or new waiver is authorized.
- Byte-identical Docker layers and production-Azure throughput remain excluded
  claims. Wheel/sdist reproducibility and exact package parity are measured.
- Runtime health and private secret contents were outside the review's read-only
  scope. The executor owns runtime qualification; secret contents are never part
  of exported candidate artifacts or review evidence.

Final measured outcomes and the actual release-blocker ledger are recorded in
release-execution.md and the final qualification report. A blocked draft PR is
allowed; no merge-readiness or release-readiness claim follows from this review.
