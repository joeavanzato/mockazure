import json

from mockazure.bootstrap import initialize_compose
from mockazure.config import load_settings


def test_compose_advertises_internal_and_host_ports(tmp_path):
    files = initialize_compose(tmp_path, api_port=28443, db_port=55434)
    config = load_settings(files.config_file.parent / "container.json", {})
    assert config.advertised_endpoints("localhost:8443").graph == "https://localhost:8443"
    assert config.advertised_endpoints("localhost:28443").graph == "https://localhost:28443"


def test_exact_legacy_container_metadata_upgrades_without_identity_rotation(tmp_path):
    files = initialize_compose(tmp_path, api_port=28443, db_port=55434)
    path = files.config_file.parent / "container.json"
    previous = json.loads(path.read_text())
    previous["client_profiles"] = {}
    path.write_bytes((json.dumps(previous, indent=2) + "\n").encode())
    token = files.admin_token_file.read_bytes()
    ca = files.ca_cert_file.read_bytes()
    initialize_compose(tmp_path, api_port=28443, db_port=55434)
    assert (
        load_settings(path, {}).advertised_endpoints("localhost:8443").graph
        == "https://localhost:8443"
    )
    assert files.admin_token_file.read_bytes() == token and files.ca_cert_file.read_bytes() == ca
