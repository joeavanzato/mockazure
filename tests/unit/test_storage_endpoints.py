from urllib.parse import urlsplit

import pytest

from mockazure.endpoints import EndpointProfile


def test_old_profiles_gain_noncolliding_data_ports():
    host = EndpointProfile(login="https://localhost:28443")
    container = EndpointProfile(login="https://localhost:8443")
    names = ("storage", "file", "queue", "table", "keyvault")
    assert {getattr(host, n) for n in names}.isdisjoint({getattr(container, n) for n in names})
    assert [urlsplit(getattr(host, n)).port for n in names] == list(range(28444, 28449))
    with pytest.raises(ValueError):
        EndpointProfile(storage="https://localhost:8443")


def test_native_listener_plan_validates_collisions(tmp_path):
    from mockazure.config import Settings
    from mockazure.storage.endpoints import listener_ports

    settings = Settings(
        data_dir=tmp_path,
        database_url="postgresql://u:p@localhost/db",
        listen_port=28443,
        admin_base_url="https://localhost:28443",
    )
    assert listener_ports(settings) == (28443, 28444, 28445, 28446, 28447, 28448)
    assert listener_ports(settings.model_copy(update={"data_listen_ports": ()})) == (28443,)
    with pytest.raises(ValueError):
        listener_ports(settings.model_copy(update={"data_listen_ports": (28443, 28443)}))


def test_pre_storage_compose_metadata_upgrades_without_rotating_identity(tmp_path):
    import json

    from mockazure.bootstrap import initialize_compose

    files = initialize_compose(tmp_path, api_port=28443, db_port=55434)
    path = files.config_file.parent / "container.json"
    previous = json.loads(path.read_text(encoding="utf-8"))
    previous.pop("data_listen_ports")
    for profile in previous["client_profiles"].values():
        for name in ("storage", "file", "queue", "table", "keyvault"):
            profile.pop(name)
    path.write_text(json.dumps(previous, indent=2) + "\n", encoding="utf-8")
    env = tmp_path / ".runtime/compose.env"
    old = env.read_text(encoding="utf-8").splitlines()[:3]
    env.write_text("\n".join(old) + "\n", encoding="utf-8")
    token, ca = files.admin_token_file.read_bytes(), files.ca_cert_file.read_bytes()
    initialize_compose(tmp_path, api_port=28443, db_port=55434)
    assert files.admin_token_file.read_bytes() == token and files.ca_cert_file.read_bytes() == ca
    assert "MOCKAZURE_STORAGE_PORT=28444" in env.read_text(encoding="utf-8")
    current = json.loads(path.read_text(encoding="utf-8"))
    assert current["data_listen_ports"] == [8444, 8445, 8446, 8447, 8448]
    assert current["client_profiles"]["container"]["storage"] == "https://localhost:8444"


def test_compose_data_services_have_real_listener_ports(tmp_path):
    import json

    from mockazure.bootstrap import initialize_compose
    from mockazure.config import load_settings
    from mockazure.storage.endpoints import listener_ports

    files = initialize_compose(tmp_path, api_port=28443, db_port=55434)
    path = files.config_file.parent / "container.json"
    data = json.loads(path.read_text())
    assert data["data_listen_ports"] == [8444, 8445, 8446, 8447, 8448]
    settings = load_settings(path)
    assert listener_ports(settings) == (8443, 8444, 8445, 8446, 8447, 8448)
