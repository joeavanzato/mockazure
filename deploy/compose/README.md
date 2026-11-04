# Compose configuration guide

For a normal installation use [`compose.yaml`](../../compose.yaml). For local
integration tests use [`compose.test.yaml`](../../compose.test.yaml). These are the
only Compose files at the repository root. Run commands below from that root.

## Which files are relevant?

| Location | Purpose | When to use it |
| --- | --- | --- |
| `compose.yaml` | Default PostgreSQL, migrations, identity setup, API, worker and ingress | Running MockAzure, including `start-mockazure.bat` |
| `compose.test.yaml` | Default integration-test database, port 55433 | Local test development; prepare credentials with `python tools/prepare_tests.py` |
| `deploy/compose/release/` | Current isolated release-qualification stacks and their collector clients | `tools.verify_release` or a suite with `MOCKAZURE_QUALIFICATION_PROFILE=release` |
| `deploy/compose/milestones/` | Preserved original identity/Graph/evidence/security/mail/ARM qualification stacks | Reproducing an older qualification suite; not the normal application startup |
| `deploy/compose/tests/` | Alternate isolated test databases for earlier milestones and release CI | Only when a test run explicitly selects the corresponding port |

The milestone number is a qualification environment's identity, not an indication
that its YAML is the recommended application configuration. These files remain
because qualification tools and compatibility workflows still reference them.
No application, runtime directory, secret file, database, or container is removed
by this reorganization.

## Release qualification

`release/` contains application/client pairs for `graph`, `evidence`, `security`,
`mail`, and `arm`. The qualification profile selects the correct pair automatically;
existing Python verifier commands and environment selection remain unchanged.

To inspect one without starting it:

```powershell
docker compose -f deploy/compose/release/compose.release.arm.yaml config --quiet
```

The CI test database uses `deploy/compose/tests/compose.test.m6.yaml` on port 55445.
Set `MOCKAZURE_TEST_DB_PORT=55445` for tests using it. The other alternate test
files retain their original ports: M3b 55437, M4 55441, and M5 55443.

## Preserved milestone workflows

| Milestone | Surface / primary caller |
| --- | --- |
| M2 | Identity and network boundary: `tools.verify_identity` |
| M3 | Graph collectors: `tools.verify_graph` |
| M3b | Evidence collection: `tools.verify_evidence` |
| M3c | Security collection: `tools.verify_security` |
| M4 | Exchange/mail: `tools.verify_mail` |
| M5 | Azure Resource Manager: `tools.verify_arm` |

`milestones/compose.clients.yaml` is the original network-boundary client base.
Its M2 overlay is intentionally used together with that base:

```powershell
docker compose -f deploy/compose/milestones/compose.clients.yaml -f deploy/compose/milestones/compose.clients.m2.yaml config --quiet
```

The M3–M5 and release client files are complete definitions. They do not use the
M2 overlay. Runtime preparation and local-only network restrictions still apply;
moving a Compose file does not prepare its certificates, identities or external
client network.

## Path and history notes

All moved files retain their original filenames and explicit project/network/
volume names. Host bind mounts and secret-file paths now use `../../../` to reach
the repository root. This preserves `.runtime/`, `work/`, `integrations/` and
`deploy/` locations when invoking the nested file directly. Do not override
`--project-directory` to the root for these nested definitions: Compose would then
resolve those already-rebased host paths against the wrong directory. The default
Windows launcher continues to use the unchanged root `compose.yaml`.

Historical plans and qualification reports record the filenames/commands used at
the time and are preserved. To replay an old command, prepend the appropriate
`deploy/compose/milestones/`, `release/`, or `tests/` directory to its Compose
filename. Newly generated qualification fingerprints include the new locations;
old attestations are historical evidence, not validation of the reorganized tree.
