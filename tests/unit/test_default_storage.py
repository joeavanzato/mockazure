from pathlib import Path

import pytest

from mockazure.bootstrap import initialize_compose


def legacy_instance(root: Path):
    initialize_compose(root)
    env = root / ".runtime/compose.env"
    env.write_bytes(b"MOCKAZURE_API_PORT=8443\nMOCKAZURE_DB_PORT=55432\n")
    return env


def test_fresh_compose_uses_volume_and_reuses_identity(tmp_path):
    files = initialize_compose(tmp_path)
    env = tmp_path / ".runtime/compose.env"
    assert "MOCKAZURE_POSTGRES_STORAGE=postgres-data\n" in env.read_text()
    identity = {p.name: p.read_bytes() for p in files.config_file.parent.iterdir() if p.is_file()}
    initialize_compose(tmp_path)
    assert all(
        (files.config_file.parent / name).read_bytes() == value for name, value in identity.items()
    )


def test_legacy_bind_is_retained_without_replacing_database_or_credentials(tmp_path):
    env = legacy_instance(tmp_path)
    database = tmp_path / ".runtime/postgres/18/docker"
    database.mkdir(parents=True)
    (database / "PG_VERSION").write_text("18\n")
    (database / "global").mkdir()
    (database / "global/pg_control").write_bytes(b"control data")
    password = (tmp_path / ".runtime/db.password").read_bytes()
    initialize_compose(tmp_path)
    assert "MOCKAZURE_POSTGRES_STORAGE=./.runtime/postgres\n" in env.read_text()
    assert (database / "PG_VERSION").read_text() == "18\n"
    assert (tmp_path / ".runtime/db.password").read_bytes() == password
    initialize_compose(tmp_path)


def test_legacy_metadata_without_database_is_not_converted_to_empty_volume(tmp_path):
    env = legacy_instance(tmp_path)
    before = env.read_bytes()
    with pytest.raises(ValueError, match="Legacy"):
        initialize_compose(tmp_path)
    assert env.read_bytes() == before


def test_volume_instance_rejects_unexpected_legacy_directory(tmp_path):
    initialize_compose(tmp_path)
    (tmp_path / ".runtime/postgres").mkdir()
    with pytest.raises(ValueError, match="storage"):
        initialize_compose(tmp_path)


@pytest.mark.parametrize("state", ["empty", "version-only", "wrong-version"])
def test_legacy_incomplete_cluster_is_refused_without_metadata_change(tmp_path, state):
    env = legacy_instance(tmp_path)
    database = tmp_path / ".runtime/postgres/18/docker"
    database.mkdir(parents=True)
    if state != "empty":
        (database / "PG_VERSION").write_text("17" if state == "wrong-version" else "18")
    before = env.read_bytes()
    with pytest.raises(ValueError, match="Legacy"):
        initialize_compose(tmp_path)
    assert env.read_bytes() == before
