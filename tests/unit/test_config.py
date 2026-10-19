import json

import pytest

from mockazure.config import load_settings


def test_config_precedence_relative_paths_and_secret_redaction(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {"data_dir": "instance", "database_url": "postgresql://u:secret-password@localhost/db"}
        )
    )
    settings = load_settings(path, {})
    assert settings.listen_port == 8443
    assert settings.data_dir == tmp_path / "instance"
    assert "secret-password" not in repr(settings)
    assert settings.database_url.get_secret_value().startswith("postgresql+psycopg://")
    assert load_settings(path, {"MOCKAZURE_LISTEN_PORT": "9443"}).listen_port == 9443


@pytest.mark.parametrize("value", ["sqlite:///local.db", "https://example.test", "postgresql://"])
def test_invalid_database_does_not_disclose_input(tmp_path, value):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"database_url": value, "data_dir": "."}))
    with pytest.raises(ValueError) as error:
        load_settings(path, {})
    assert value not in str(error.value)


def test_explicit_backup_key_path_is_relative_to_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "data_dir": "instance",
                "database_url": "postgresql://u:local@localhost/db",
                "keyvault_backup_key_file": "backup.key",
                "keyvault_backup_key_ref": "operator-v1",
            }
        ),
        encoding="utf-8",
    )
    settings = load_settings(path, {})
    assert settings.keyvault_backup_key_file == tmp_path / "backup.key"
