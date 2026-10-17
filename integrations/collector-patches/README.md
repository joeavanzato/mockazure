# Explicit collector source profiles

The default mail verifier uses original: exactly the pinned public Hawk and
Microsoft-Extractor-Suite archives in integrations/manifest.json.

The release verifier selects serialization-v1. This is a **patched client
profile**, not a claim that unmodified upstream collectors pass. Its JSON manifest
pins the original file hashes, exact single replacements and resulting file hashes:

- Hawk Out-MultipleFileType: JSON append reads the existing document and writes
  one combined array. Nested values and date-looking strings are retained. A
  malformed existing document fails before overwrite.
- MES Retrieve-MessageTrace: the CSV projection serializes Received DateTime
  and DateTimeOffset values using invariant round-trip format. Original objects,
  API calls, permissions and pagination remain unchanged. CSV write errors fail
  visibly.

tools.collector_profiles builds a separate tree under
work/collector-profiles/serialization-v1, directly from the hash-verified public
archives. It verifies the complete original and patched file inventories. Existing
changed profiles fail verification instead of being repaired. Original licenses
and all other source bytes are retained. These downloaded clients are not bundled
in the MockAzure wheel or container.

Run the real mail qualification explicitly:

    python tools/verify_mail.py --collector-profile serialization-v1

The patched run first records original Hawk inbox-rule and MES trace outcomes
(application/delegated, allowed/denied), then requires all 44 patched-profile cases
to pass the unchanged output oracles. Reports identify the selected profile and
hash every executed source file and output artifact. The original failures remain
visible in upstream_baseline; they are not converted into successes.

Local serializer regressions require the prepared pinned archives and PowerShell:

    python -m pytest tests/acceptance/test_collector_serialization.py

Set MOCKAZURE_SERIALIZER_TEST_PROFILE=original to reproduce the three upstream
serializer failures. This deliberately failing reproducer is separate from release
acceptance. No emitted collector file is post-processed or repaired.

Upstream references:
- https://github.com/T0pCyber/hawk/tree/a44536f2b4225dd8ea2e463db88d5db40cfac8c7
- https://github.com/invictus-ir/Microsoft-Extractor-Suite/tree/47565f7842784cd3667ff630e3dccb49fa3408ed

Both pinned revisions matched upstream HEAD when checked on 2026-10-17.
These are narrow local compatibility patches, not upstream fixes or endorsements.
