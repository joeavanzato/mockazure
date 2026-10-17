import re
from pathlib import Path

import pytest


@pytest.mark.parametrize("surface", ["graph", "evidence", "security", "mail", "arm"])
def test_release_postgres_uses_owned_durable_linux_volume(surface):
    config = Path(f"compose.release.{surface}.yaml").read_text()
    match = re.search(r"^\s+- ([\w-]+):/var/lib/postgresql$", config, re.MULTILINE)
    assert match, "Release PostgreSQL must use a Docker-managed volume"
    assert f"\nvolumes:\n  {match[1]}:" in config
    assert "org.mockazure.purpose: release-postgresql" in config
    assert "stop_grace_period: 120s" in config
