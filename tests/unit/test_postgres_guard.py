import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")


def shell_path(path):
    value = path.resolve().as_posix()
    return "/" + value[0].lower() + value[2:] if os.name == "nt" else value


@pytest.mark.skipif(not BASH or not Path(BASH).is_file(), reason="Bash not installed")
@pytest.mark.parametrize(
    "state,allowed",
    [
        ("fresh", True),
        ("restart", True),
        ("legacy", True),
        ("lost", False),
        ("identity-only", False),
        ("partial", False),
        ("wrong-version", False),
    ],
)
def test_postgres_guard_never_initializes_replacement_storage(tmp_path, state, allowed):
    instance = tmp_path / "instance"
    cluster = tmp_path / "cluster"
    binaries = tmp_path / "bin"
    for directory in (instance, cluster, binaries):
        directory.mkdir()
    if state in {"restart", "legacy", "wrong-version"}:
        (cluster / "PG_VERSION").write_text("17\n" if state == "wrong-version" else "18\n")
        (cluster / "global").mkdir()
        (cluster / "global/pg_control").write_bytes(b"existing control data")
    if state in {"restart", "lost", "partial"}:
        (instance / "postgres.initialized").write_text("mockazure-postgres-18\n")
    if state == "partial":
        (cluster / "PG_VERSION").write_text("18\n")
    if state == "identity-only":
        (instance / "identity.wrap").write_text("existing identity")
    sentinel = tmp_path / "entrypoint-called"
    executable = binaries / "docker-entrypoint.sh"
    executable.write_text('#!/bin/sh\nprintf entered > "$ENTRYPOINT_SENTINEL"\n', newline="\n")
    executable.chmod(0o700)
    environment = dict(
        os.environ,
        PGDATA=shell_path(cluster),
        MOCKAZURE_INSTANCE_DIR=shell_path(instance),
        ENTRYPOINT_SENTINEL=shell_path(sentinel),
    )
    result = subprocess.run(
        [
            BASH,
            "-c",
            'export PATH="$1:$PATH"; /bin/sh "$2" postgres',
            "guard-test",
            shell_path(binaries),
            shell_path(ROOT / "deploy/postgres-guard.sh"),
        ],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert (result.returncode == 0) == allowed, result.stderr
    assert sentinel.exists() == allowed
    if allowed:
        assert (instance / "postgres.initialized").is_file()
    else:
        assert "refusing" in result.stderr.lower()
