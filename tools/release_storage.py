"""Fail closed on bind-to-volume transitions; never discard a database or identity."""

import json
import subprocess
from pathlib import Path


def validate_transition(runtime: Path, project: str, volume: dict | None) -> None:
    procedure = "Follow the fresh-runtime procedure in docs/stabilization-execution.md."
    if (runtime / "postgres").exists():
        raise RuntimeError("Legacy release database directory detected. " + procedure)
    if volume is None:
        if list(runtime.glob("provisioning*.json")) or list(runtime.glob("clients/*/client.json")):
            raise RuntimeError(
                "Provisioned metadata has no matching PostgreSQL volume. " + procedure
            )
        return
    labels = volume.get("Labels") or {}
    if (
        labels.get("com.docker.compose.project") != project
        or labels.get("org.mockazure.purpose") != "release-postgresql"
    ):
        raise RuntimeError("PostgreSQL volume ownership mismatch")
    if not (runtime / "instance/native.json").is_file():
        raise RuntimeError("PostgreSQL volume has no matching instance configuration. " + procedure)


def check_storage(root: Path, profile) -> None:
    name = profile.project + "_postgres-data"
    result = subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    volume = None
    if name in result.stdout.splitlines():
        result = subprocess.run(
            ["docker", "volume", "inspect", name],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        volume = json.loads(result.stdout)[0]
    validate_transition(root / profile.runtime_dir, profile.project, volume)
