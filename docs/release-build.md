# Building a MockAzure candidate

Candidate builds use committed Git object bytes, an explicit source allowlist,
hash-pinned build tools in build-requirements.txt, and SOURCE_DATE_EPOCH from the
source commit. Runtime dependencies come from uv.lock.

Commit intended changes first. The owned clean worktree must have exactly the
committed source bytes, including LF line endings, so native qualification and
Docker consume the same package inputs. Untracked files are never exported.

Run from this checkout with its Python environment:

~~~powershell
$sourceCommit = git rev-parse HEAD
python -m tools.build_release --commit $sourceCommit --output "work/release/candidate-$sourceCommit"
~~~

Use a fresh output directory for another run. The builder retains failed runs.
It builds a wheel and source archive twice in separate source directories and
compares their SHA256 hashes. Archive checks reject untracked or unexpected
content, private-key files/material, traversal, links, duplicate names, missing
licences, and changed source/resource bytes.

The builder installs the wheel into a fresh environment, exercises module and
console entry points, and compares every package file, migration head and
capability document with a locally built Docker image. The manifest records
both dependency inventories, build-tool versions, source hashes and image ID.
It does not claim byte-identical Docker layers.

The output manifest's artifact pass is separate from release acceptance.
All five original-client qualification suites and all three declared scale
profiles remain mandatory. The four documented M4 collector-output failures
remain blockers; there is no new waiver. No package or image is published.

Final measured artifact references will be recorded after verification.
