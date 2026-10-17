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

## Measured development candidate

Source commit: bf64b4e2ae09defdae83a6d306d777fc97bf6667.
Release source fingerprint:
64e8af55c881ed05119a49c1740bb0827d6004536f6cdf36f7d1eee7dcd6c39e.

Both clean builds produced identical wheel and source-archive hashes. Clean
installation, console/module entry points and native/Docker parity passed for
all 292 package files, migration head 037 and the capability document.
The verified manifest and dependency inventories are in
[release-build.json](release-build.json).

Wheel SHA256:
52345ae6eec774f78d7569ed2618a3b5908fe2d7d531d625ee57b743a6386908.

The candidate is version 0.1.0.dev0 and is not an accepted release. The four
documented M4 collector-output failures and the failed million-record profile
remain mandatory blockers. Artifact reproducibility does not override these
failures. Subsequent documentation commits do not change the candidate inputs;
the validator compares the committed source objects and current working bytes.

Local user-facing exports include both archives, candidate-manifest.json and
SHA256SUMS.txt. Runtime directories, database contents, credentials, keys and
environment files are excluded. No package or image was published to a registry.
