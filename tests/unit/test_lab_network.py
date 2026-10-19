from pathlib import Path

import pytest
from cryptography import x509

from mockazure.bootstrap import initialize_compose, initialize_instance
from mockazure.config import load_settings


def test_native_shared_lab_has_explicit_binding_and_verified_hostname(tmp_path):
    files = initialize_instance(
        tmp_path,
        database_url="postgresql://lab:fake@localhost/lab",
        advertised_host="lab.mockazure.test",
        port=39443,
        bind_address="0.0.0.0",
    )
    settings = load_settings(files.config_file, {})
    assert settings.listen_host == "0.0.0.0"
    assert settings.endpoints.graph == "https://lab.mockazure.test:39443"
    assert settings.endpoints.storage == "https://lab.mockazure.test:39444"
    cert = x509.load_pem_x509_certificate(files.server_cert_file.read_bytes())
    assert "lab.mockazure.test" in cert.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value.get_values_for_type(x509.DNSName)
    with pytest.raises(ValueError):
        initialize_instance(
            tmp_path,
            database_url="postgresql://lab:fake@localhost/lab",
            advertised_host="lab.mockazure.test",
            port=39443,
        )


def test_compose_shared_lab_keeps_database_loopback_and_worker_routes_internal(tmp_path):
    files = initialize_compose(
        tmp_path,
        api_port=39443,
        db_port=55448,
        advertised_host="lab.mockazure.test",
        bind_address="0.0.0.0",
    )
    settings = load_settings(files.config_file.parent / "container.json", {})
    assert (
        settings.advertised_endpoints("lab.mockazure.test:39443").graph
        == "https://lab.mockazure.test:39443"
    )
    assert settings.population_endpoints.graph == "https://api:8443"
    assert settings.population_endpoints.logs == "https://api:8449"
    assert "api" in settings.allowed_hosts
    assert "MOCKAZURE_BIND_ADDRESS=0.0.0.0" in (tmp_path / ".runtime/compose.env").read_text()
    compose = Path("compose.yaml").read_text()
    assert "127.0.0.1:${MOCKAZURE_DB_PORT" in compose
    assert "${MOCKAZURE_BIND_ADDRESS:-127.0.0.1}:${MOCKAZURE_API_PORT" in compose
    assert (
        initialize_compose(
            tmp_path,
            api_port=39443,
            db_port=55448,
            advertised_host="lab.mockazure.test",
            bind_address="0.0.0.0",
        )
        == files
    )
