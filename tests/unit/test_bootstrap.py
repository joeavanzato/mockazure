import concurrent.futures
import multiprocessing

import pytest

from mockazure.bootstrap import initialize_instance

OPTIONS = {
    "database_url": "postgresql://local:secret@localhost/mockazure",
    "advertised_host": "api.mockazure.test",
    "port": 8443,
}


def initialize(path):
    return initialize_instance(path, **OPTIONS)


def test_concurrent_processes_reuse_complete_identity(tmp_path):
    path = tmp_path / "space in path"
    with concurrent.futures.ProcessPoolExecutor(
        3, mp_context=multiprocessing.get_context("spawn")
    ) as pool:
        results = list(pool.map(initialize, [path] * 3))
    assert results[0] == results[1] == results[2]
    before = {p: p.read_bytes() for p in path.iterdir() if p.is_file()}
    initialize(path)
    assert before == {p: p.read_bytes() for p in before}
    assert len(results[0].admin_token_file.read_text().strip()) >= 43


def test_partial_initialization_fails_without_replacing_files(tmp_path):
    (tmp_path / "admin.token").write_text("keep me")
    with pytest.raises(ValueError, match="(?i)incomplete|partial"):
        initialize(tmp_path)
    assert (tmp_path / "admin.token").read_text() == "keep me"


def test_corrupt_config_fails_without_rotating_identity(tmp_path):
    files = initialize(tmp_path)
    token = files.admin_token_file.read_bytes()
    files.config_file.write_text("{")
    with pytest.raises(ValueError):
        initialize(tmp_path)
    assert files.admin_token_file.read_bytes() == token
