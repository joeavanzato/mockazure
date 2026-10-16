import json

import pytest

from mockazure.bootstrap import initialize_compose
from mockazure.config import load_settings


def test_compose_init_is_reusable_and_paths_are_portable(tmp_path):
    files = initialize_compose(tmp_path, api_port=9443, db_port=55434)
    native = load_settings(files.config_file, {})
    container = json.loads((files.config_file.parent / "container.json").read_text())
    assert native.listen_port == 9443
    assert native.data_dir == tmp_path / ".runtime/instance"
    assert container["data_dir"] == "."
    assert container["listen_host"] == "0.0.0.0"
    assert container["listen_port"] == 8443
    assert "@db:5432/mockazure" in container["database_url"]
    password = (tmp_path / ".runtime/db.password").read_bytes()
    token = files.admin_token_file.read_bytes()
    assert initialize_compose(tmp_path, api_port=9443, db_port=55434) == files
    assert files.admin_token_file.read_bytes() == token
    assert (tmp_path / ".runtime/db.password").read_bytes() == password
    assert password.decode().strip() not in (tmp_path / ".runtime/compose.env").read_text()


def test_compose_init_refuses_partial_or_conflicting_files(tmp_path):
    files = initialize_compose(tmp_path)
    before = files.admin_token_file.read_bytes()
    (tmp_path / ".runtime/db.password").unlink()
    with pytest.raises(ValueError):
        initialize_compose(tmp_path)
    assert files.admin_token_file.read_bytes() == before
